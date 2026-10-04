"""The case-persistence store S1's routes read and write through.

Same shape as ``fcrm_scoring.store.ConfigStore``: a small ``Protocol`` so
``routes.py`` and its unit tests never need a database, plus an in-memory
implementation for tests and a Mongo-backed one the real app wires at
startup (``lifespan.py``).

Every state-changing method is a single conditional update against
``OPEN_FOR_WRITES_STATES``/``submitted_at`` (``fcrm_case.domain.record.
CaseRecord.open_for_writes``), so the "closed or already-submitted cases
reject further writes" rule is enforced atomically here -- not just by a
route-layer check that a concurrent request could race past -- and a
method returns ``None`` (never raises) when the predicate did not match,
so ``routes.py`` alone decides whether that means 404 or 409.

:meth:`CaseStore.create` and :meth:`CaseStore.submit` also take the
``Envelope`` for ``case.created``/``case.submitted`` (design A §3.1,
``fcrm_contracts.events.catalogue``): the Mongo store writes the case
document and the outbox row in one transaction
(:func:`fcrm_common.mongo.with_transaction`), so an event is never
recorded for a write that did not happen, or vice versa. ``PUT .../fields/
{field_id}`` and the title update have no catalogued event of their own
(only ``case.created``, ``case.submitted`` and a handful of later-lifecycle
types are in ``EVENT_TYPES`` today) and stay single-document updates.
"""

from __future__ import annotations

from typing import Any, Optional, Protocol

from fcrm_common.ids import mint_case_id
from fcrm_common.mongo import Repository, with_transaction
from fcrm_common.outbox import Outbox
from fcrm_common.pagination import decode_cursor, encode_cursor
from fcrm_contracts.common import Page
from fcrm_contracts.enums import CaseState
from fcrm_contracts.events.envelope import Envelope
from pymongo import AsyncMongoClient, IndexModel, ReturnDocument
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.collection import AsyncCollection

from fcrm_case.domain.record import (
    OPEN_FOR_WRITES_STATES,
    CaseRecord,
    FieldValueRecord,
    SubmissionVersionRecord,
)

# The Mongo spelling of ``CaseRecord.open_for_writes``, derived from the
# same ``OPEN_FOR_WRITES_STATES`` set so the two can never drift apart.
_OPEN_FILTER: dict[str, Any] = {
    "state": {"$in": sorted(state.value for state in OPEN_FOR_WRITES_STATES)},
    "submitted_at": None,
}


class CaseStore(Protocol):
    """What ``routes.py`` needs; see the module docstring for the
    conditional-update contract every write method shares.
    """

    async def next_case_id(self, year: int) -> str:
        """A fresh ``FCRM-<year>-NNNN`` id from this year's counter."""
        ...

    async def create(self, record: CaseRecord, event: Envelope) -> CaseRecord:
        """Insert ``record`` and enqueue ``event`` atomically."""
        ...

    async def get(self, case_id: str) -> Optional[CaseRecord]:
        """The case, or ``None`` if no such id exists."""
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

    async def update_title(self, case_id: str, title: str) -> Optional[CaseRecord]:
        """Set ``title``; ``None`` if the case does not exist or is not
        open for writes.
        """
        ...

    async def write_field(
        self, case_id: str, field: FieldValueRecord
    ) -> Optional[CaseRecord]:
        """Set ``fields[field.field_id]``; ``None`` if the case does not
        exist or is not open for writes.
        """
        ...

    async def submit(
        self, case_id: str, version: SubmissionVersionRecord, event: Envelope
    ) -> Optional[CaseRecord]:
        """Append ``version``, stamp ``submitted_at`` and enqueue ``event``
        atomically; ``None`` if the case does not exist or was already
        submitted/closed (submission is one-shot: the same conditional
        update that blocks a field write after submission also blocks a
        second submit).
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

    async def next_case_id(self, year: int) -> str:
        self._counters[year] = self._counters.get(year, 0) + 1
        return mint_case_id(year, self._counters[year])

    async def create(self, record: CaseRecord, event: Envelope) -> CaseRecord:
        self._cases[record.case_id] = record
        self.events.append(event)
        return record

    async def get(self, case_id: str) -> Optional[CaseRecord]:
        return self._cases.get(case_id)

    async def list(
        self,
        *,
        owner: Optional[str] = None,
        analyst_id: Optional[str] = None,
        state: Optional[CaseState] = None,
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> Page[CaseRecord]:
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

    async def update_title(self, case_id: str, title: str) -> Optional[CaseRecord]:
        record = self._cases.get(case_id)
        if record is None or not record.open_for_writes:
            return None
        updated = record.model_copy(update={"title": title})
        self._cases[case_id] = updated
        return updated

    async def write_field(
        self, case_id: str, field: FieldValueRecord
    ) -> Optional[CaseRecord]:
        record = self._cases.get(case_id)
        if record is None or not record.open_for_writes:
            return None
        fields = dict(record.fields)
        fields[field.field_id] = field
        updated = record.model_copy(update={"fields": fields})
        self._cases[case_id] = updated
        return updated

    async def submit(
        self, case_id: str, version: SubmissionVersionRecord, event: Envelope
    ) -> Optional[CaseRecord]:
        record = self._cases.get(case_id)
        if record is None or not record.open_for_writes:
            return None
        versions = [*record.submission_versions, version]
        updated = record.model_copy(
            update={"submission_versions": versions, "submitted_at": version.created_at}
        )
        self._cases[case_id] = updated
        self.events.append(event)
        return updated


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
    ) -> None:
        self._client = client
        self._cases = cases
        self._counters = counters
        self._outbox = outbox

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

    async def create(self, record: CaseRecord, event: Envelope) -> CaseRecord:
        async def _do(session: AsyncClientSession) -> CaseRecord:
            await self._cases.insert_one(record, session=session)
            await self._outbox.enqueue(session, event)
            return record

        return await with_transaction(self._client, _do)

    async def get(self, case_id: str) -> Optional[CaseRecord]:
        return await self._cases.find_one({"case_id": case_id})

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

    async def update_title(self, case_id: str, title: str) -> Optional[CaseRecord]:
        return await self._cases.find_one_and_update(
            {"case_id": case_id, **_OPEN_FILTER}, {"title": title}
        )

    async def write_field(
        self, case_id: str, field: FieldValueRecord
    ) -> Optional[CaseRecord]:
        return await self._cases.find_one_and_update(
            {"case_id": case_id, **_OPEN_FILTER},
            {f"fields.{field.field_id}": field.model_dump(mode="python")},
        )

    async def submit(
        self, case_id: str, version: SubmissionVersionRecord, event: Envelope
    ) -> Optional[CaseRecord]:
        async def _do(session: AsyncClientSession) -> Optional[CaseRecord]:
            updated = await self._cases.find_one_and_update(
                {"case_id": case_id, **_OPEN_FILTER},
                {
                    "$push": {"submission_versions": version.model_dump(mode="python")},
                    "$set": {"submitted_at": version.created_at},
                },
                session=session,
            )
            if updated is not None:
                await self._outbox.enqueue(session, event)
            return updated

        return await with_transaction(self._client, _do)


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
        ]
    )
