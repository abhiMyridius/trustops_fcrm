"""The case-persistence store S1's routes read and write through.

Same shape as ``fcrm_scoring.store.ConfigStore``: a small ``Protocol`` so
``routes.py`` and its unit tests never need a database, plus an in-memory
implementation for tests and a Mongo-backed one the real app wires at
startup (``lifespan.py``).

The product owner's writes (create, upload, decide, submit, discard) are not
here: they go through :mod:`fcrm_case.po_store`, which applies a whole PO action,
and its outbox event, in one transaction. This store holds the reads every role
shares and H1's state and assignment writes.

H1's two writes to a case, :meth:`CaseStore.apply_state` and
:meth:`CaseStore.assign_analyst`, are different: they are not gated on
``OPEN_FOR_WRITES_STATES`` (H1 decides every transition, including the one into
``closed``), and each writes the case, its activity row and the ``activity_seq``
bump in one transaction. They return a :class:`CaseWrite` so the route can
publish the new entry to the stream after the commit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Protocol

from fcrm_common.ids import mint_case_id
from fcrm_common.mongo import Repository, with_transaction
from fcrm_common.outbox import Outbox
from fcrm_common.pagination import decode_cursor, encode_cursor
from fcrm_common.time import utcnow
from fcrm_contracts.api.case_internal import StatePatch, UiActor
from fcrm_contracts.common import Page
from fcrm_contracts.enums import CaseState
from fcrm_contracts.events.envelope import Envelope
from pymongo import AsyncMongoClient, IndexModel, ReturnDocument
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.collection import AsyncCollection

from fcrm_case.activity_log import ActivityLog, InMemoryActivityLog, MongoActivityLog
from fcrm_case.domain.activity import ActivityEntry
from fcrm_case.domain.record import CaseRecord
from fcrm_case.domain.stage import (
    apply_changes,
    assigned_event,
    stage_changed_event,
    state_changes,
)


@dataclass(frozen=True)
class CaseWrite:
    """The result of a write that may add an activity row: the case after the
    write and, when the write was a visible change, the entry to publish to the
    stream once the transaction has committed.
    """

    record: CaseRecord
    entry: Optional[ActivityEntry] = None


class CaseStore(Protocol):
    """What ``routes.py`` needs; see the module docstring for the
    conditional-update contract every write method shares.
    """

    @property
    def activity(self) -> ActivityLog:
        """The activity log this store writes through (``POST .../events``
        and the stream read it).
        """
        ...

    async def next_case_id(self, year: int) -> str:
        """A fresh ``FCRM-<year>-NNNN`` id from this year's counter."""
        ...

    async def get(
        self, case_id: str, *, with_versions: bool = True
    ) -> Optional[CaseRecord]:
        """The case, or ``None`` if no such id exists. ``with_versions=False``
        leaves ``submission_versions`` out of the read (they grow with every
        clarification round and the internal record does not need them).
        """
        ...

    async def exists(self, case_id: str) -> bool:
        """Whether the case exists (an ``_id`` lookup, no document read)."""
        ...

    async def list(
        self,
        *,
        owner: Optional[str] = None,
        analyst_id: Optional[str] = None,
        state: Optional[CaseState] = None,
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> Page[CaseRecord]:
        """A page of cases, oldest id first, filtered by whichever of
        ``owner``/``analyst_id``/``state`` is given.
        """
        ...

    async def apply_state(
        self, case_id: str, patch: StatePatch, actor: UiActor
    ) -> Optional[CaseWrite]:
        """Apply H1's state patch (only the fields it sets change) and, when
        the stage actually changed, write the ``case.stage_changed`` activity
        row in the same transaction. ``None`` if the case does not exist.
        Replaying the same patch changes nothing and adds no row.
        """
        ...

    async def assign_analyst(
        self, case_id: str, analyst_id: str, analyst_name: str, actor: UiActor
    ) -> Optional[CaseWrite]:
        """Set the case's analyst and write the ``case.assigned`` row (visible
        to the PO, carrying the name only). Assigning the analyst already
        assigned changes nothing. ``None`` if the case does not exist.
        """
        ...

    async def open_case_counts(self) -> dict[str, int]:
        """``{analyst_id: open cases}`` over every case that is not closed, in
        one aggregation (an analyst with no open case is absent).
        """
        ...


class InMemoryCaseStore:
    """Used by unit tests: a plain dict, no transactions (a single
    in-process dict update is already atomic with respect to this
    service's own event loop; the only interleaving that matters --
    "did the predicate still hold when we wrote" -- is checked the same
    way the Mongo store checks it, just without a real database).
    """

    def __init__(self) -> None:
        self._cases: dict[str, CaseRecord] = {}
        self._counters: dict[int, int] = {}
        self.events: list[Envelope] = []
        self.activity = InMemoryActivityLog(self._bump_activity_seq)

    @property
    def records(self) -> dict[str, CaseRecord]:
        """The stored cases by id: what the in-memory PO store twin reads and
        writes through (the Mongo store has no such back door).
        """
        return self._cases

    def _bump_activity_seq(self, case_id: str) -> Optional[int]:
        """The next ``activity_seq`` of ``case_id`` (``None`` if no such case)."""
        record = self._cases.get(case_id)
        if record is None:
            return None
        seq = record.activity_seq + 1
        self._cases[case_id] = record.model_copy(update={"activity_seq": seq})
        return seq

    async def next_case_id(self, year: int) -> str:
        self._counters[year] = self._counters.get(year, 0) + 1
        return mint_case_id(year, self._counters[year])

    async def get(
        self, case_id: str, *, with_versions: bool = True
    ) -> Optional[CaseRecord]:
        record = self._cases.get(case_id)
        if record is not None and not with_versions:
            return record.model_copy(update={"submission_versions": []})
        return record

    async def exists(self, case_id: str) -> bool:
        return case_id in self._cases

    async def list(
        self,
        *,
        owner: Optional[str] = None,
        analyst_id: Optional[str] = None,
        state: Optional[CaseState] = None,
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> Page[CaseRecord]:
        if limit < 1:
            # Same contract as ``Repository.find_paginated``: a negative
            # limit would otherwise slice ``[0:-1]`` and silently drop the
            # last row instead of failing.
            raise ValueError("limit must be >= 1")
        matching = [
            c
            for c in self._cases.values()
            if (owner is None or c.owner == owner)
            and (analyst_id is None or c.analyst_id == analyst_id)
            and (state is None or c.state == state)
        ]
        matching.sort(key=lambda c: c.case_id)
        start = 0
        if cursor is not None:
            after = decode_cursor(cursor)
            start = next(
                (i + 1 for i, c in enumerate(matching) if c.case_id == after),
                len(matching),
            )
        page = matching[start : start + limit]
        has_more = start + limit < len(matching)
        next_cursor = encode_cursor(page[-1].case_id) if has_more and page else None
        return Page(items=page, next_cursor=next_cursor, total=len(matching))

    async def apply_state(
        self, case_id: str, patch: StatePatch, actor: UiActor
    ) -> Optional[CaseWrite]:
        record = self._cases.get(case_id)
        if record is None:
            return None
        updated = apply_changes(record, state_changes(patch, utcnow()))
        entry: Optional[ActivityEntry] = None
        if record.state != patch.stage:
            seq = record.activity_seq + 1
            updated = updated.model_copy(update={"activity_seq": seq})
            entry = self.activity.stage(
                case_id, seq, stage_changed_event(record.state, patch, actor)
            )
        self._cases[case_id] = updated
        return CaseWrite(updated, entry)

    async def assign_analyst(
        self, case_id: str, analyst_id: str, analyst_name: str, actor: UiActor
    ) -> Optional[CaseWrite]:
        record = self._cases.get(case_id)
        if record is None:
            return None
        if record.analyst_id == analyst_id:
            return CaseWrite(record)
        seq = record.activity_seq + 1
        updated = record.model_copy(
            update={
                "analyst_id": analyst_id,
                "analyst_name": analyst_name,
                "activity_seq": seq,
            }
        )
        self._cases[case_id] = updated
        entry = self.activity.stage(case_id, seq, assigned_event(analyst_name, actor))
        return CaseWrite(updated, entry)

    async def open_case_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for record in self._cases.values():
            if record.analyst_id is not None and record.state != CaseState.CLOSED:
                counts[record.analyst_id] = counts.get(record.analyst_id, 0) + 1
        return counts


class MongoCaseStore:
    """The real store: ``fcrm_case.cases``, ``fcrm_case.case_counters`` and
    the ``fcrm_case.outbox`` row written alongside a create/submit.
    """

    def __init__(
        self,
        client: AsyncMongoClient[Any],
        cases: Repository[CaseRecord],
        counters: AsyncCollection[Any],
        outbox: Outbox,
        activity: MongoActivityLog,
    ) -> None:
        self._client = client
        self._cases = cases
        self._counters = counters
        self._outbox = outbox
        self.activity = activity

    async def next_case_id(self, year: int) -> str:
        doc = await self._counters.find_one_and_update(
            {"_id": year},
            {"$inc": {"seq": 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        # upsert=True with ReturnDocument.AFTER always returns a document;
        # pymongo's stubs type the result Optional regardless.
        assert doc is not None
        return mint_case_id(year, int(doc["seq"]))

    async def get(
        self, case_id: str, *, with_versions: bool = True
    ) -> Optional[CaseRecord]:
        projection = None if with_versions else {"submission_versions": 0}
        doc = await self._cases.collection.find_one(
            {"_id": case_id}, projection=projection
        )
        return self._cases.from_document(doc) if doc is not None else None

    async def exists(self, case_id: str) -> bool:
        doc = await self._cases.collection.find_one(
            {"_id": case_id}, projection={"_id": 1}
        )
        return doc is not None

    async def list(
        self,
        *,
        owner: Optional[str] = None,
        analyst_id: Optional[str] = None,
        state: Optional[CaseState] = None,
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> Page[CaseRecord]:
        filter_: dict[str, Any] = {}
        if owner is not None:
            filter_["owner"] = owner
        if analyst_id is not None:
            filter_["analyst_id"] = analyst_id
        if state is not None:
            filter_["state"] = state.value
        return await self._cases.find_paginated(
            filter_, limit=limit, cursor=cursor, with_total=True
        )

    async def apply_state(
        self, case_id: str, patch: StatePatch, actor: UiActor
    ) -> Optional[CaseWrite]:
        async def _do(session: AsyncClientSession) -> Optional[CaseWrite]:
            before = await self._cases.collection.find_one(
                {"_id": case_id}, projection={"state": 1}, session=session
            )
            if before is None:
                return None
            previous = CaseState(before["state"])
            changed = previous != patch.stage
            update: dict[str, Any] = {"$set": state_changes(patch, utcnow())}
            if changed:
                update["$inc"] = {"activity_seq": 1}
            updated = await self._cases.find_one_and_update(
                {"case_id": case_id}, update, session=session
            )
            if updated is None:
                return None
            entry: Optional[ActivityEntry] = None
            if changed:
                entry = await self.activity.stage(
                    session,
                    case_id,
                    updated.activity_seq,
                    stage_changed_event(previous, patch, actor),
                )
            return CaseWrite(updated, entry)

        return await with_transaction(self._client, _do)

    async def assign_analyst(
        self, case_id: str, analyst_id: str, analyst_name: str, actor: UiActor
    ) -> Optional[CaseWrite]:
        async def _do(session: AsyncClientSession) -> Optional[CaseWrite]:
            updated = await self._cases.find_one_and_update(
                {"case_id": case_id, "analyst_id": {"$ne": analyst_id}},
                {
                    "$set": {"analyst_id": analyst_id, "analyst_name": analyst_name},
                    "$inc": {"activity_seq": 1},
                },
                session=session,
            )
            if updated is None:
                # Missing, or already assigned to this analyst: a replay.
                existing = await self._cases.find_one(
                    {"case_id": case_id}, session=session
                )
                return None if existing is None else CaseWrite(existing)
            entry = await self.activity.stage(
                session,
                case_id,
                updated.activity_seq,
                assigned_event(analyst_name, actor),
            )
            return CaseWrite(updated, entry)

        return await with_transaction(self._client, _do)

    async def open_case_counts(self) -> dict[str, int]:
        rows = await self._cases.aggregate(
            [
                {
                    "$match": {
                        "state": {"$ne": CaseState.CLOSED.value},
                        "analyst_id": {"$type": "string"},
                    }
                },
                {"$group": {"_id": "$analyst_id", "n": {"$sum": 1}}},
            ]
        )
        return {str(row["_id"]): int(row["n"]) for row in rows}


def build_cases_repository(db: Any) -> Repository[CaseRecord]:
    """``Repository[CaseRecord]`` over ``db.cases``, keyed by ``case_id``."""
    return Repository(db["cases"], CaseRecord, id_of=lambda c: c.case_id)


async def ensure_case_indexes(cases: Repository[CaseRecord]) -> None:
    """Indexes ``routes.py``'s list/queue filters rely on."""
    await cases.ensure_indexes(
        [
            IndexModel([("owner", 1)], name="owner_idx"),
            IndexModel([("analyst_id", 1)], name="analyst_idx"),
            IndexModel([("state", 1)], name="state_idx"),
            # Annex F: the PO's own list newest first, and the load aggregation.
            IndexModel([("owner", 1), ("created_at", -1)], name="owner_created_idx"),
            IndexModel([("analyst_id", 1), ("state", 1)], name="analyst_state_idx"),
        ]
    )
