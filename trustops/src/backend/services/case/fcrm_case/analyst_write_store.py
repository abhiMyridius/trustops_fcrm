"""The persistence behind the analyst's write routes.

Same split as :mod:`fcrm_case.po_store`: a small :class:`AnalystWriteStore`
``Protocol`` the service depends on, a Mongo implementation the lifespan wires
and an in-memory twin for unit tests. What this store adds:

========================  ====================================================
``analyst_decisions``     the ledger of gates the analyst decided, ``_id =
                          gate_id`` (the first decision on a gate emits
                          ``gate.decided``; see
                          :class:`~fcrm_case.domain.analyst_commands.AnalystDecisionRow`)
========================  ====================================================

**Writes** are one method, :meth:`AnalystWriteStore.commit`, that applies a whole
:class:`~fcrm_case.domain.analyst_commands.AnalystCommand` in a single
transaction: the decided items, the ledger rows, the staged activity rows and the
outbox events commit together or not at all, so an item is never decided without
its event and an event is never published for a decision that was not stored
(Caseflow reads the decided value from the item). A command that has gates and
decides none of them new is a replay and changes nothing.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional, Protocol

from fcrm_common.errors import ConflictError, NotFoundError
from fcrm_common.mongo import Repository, to_bson, with_transaction
from fcrm_common.outbox import Outbox
from fcrm_common.time import utcnow
from fcrm_contracts.api.case_internal import UiEventIn
from pymongo import ASCENDING, AsyncMongoClient, IndexModel, UpdateOne
from pymongo.asynchronous.client_session import AsyncClientSession

from fcrm_case.activity_log import MongoActivityLog
from fcrm_case.analyst_errors import RecommendationLockedError
from fcrm_case.assessment_store import InMemoryAssessmentStore
from fcrm_case.domain.activity import ActivityEntry
from fcrm_case.domain.analyst_commands import (
    AnalystCommand,
    AnalystDecisionRow,
    AnalystGate,
    RecommendationChange,
    ThreadResolution,
)
from fcrm_case.domain.clarifications import ClarificationThreadRow
from fcrm_case.domain.record import CaseRecord
from fcrm_case.domain.views import ScoreSnapshotRow
from fcrm_case.errors import ensure_open
from fcrm_case.store import InMemoryCaseStore
from fcrm_case.views_store import InMemoryViewStore, ViewRepos

logger = logging.getLogger(__name__)

#: A case has a handful of score snapshots and decisions; this only stops a bug
#: reading a million.
MAX_ROWS_PER_CASE = 500


@dataclass(frozen=True)
class AnalystCommitted:
    """What a committed analyst write leaves behind.

    Attributes:
        record: The case document as the transaction saw it.
        entries: The activity rows staged in the transaction, to publish to the
            case stream now that it committed.
        emitted: The gate ids whose ``gate.decided`` this write enqueued.
        replayed: The command had gates and every one was decided before, so
            nothing was written.
    """

    record: CaseRecord
    entries: tuple[ActivityEntry, ...] = ()
    emitted: frozenset[str] = frozenset()
    replayed: bool = False


class AnalystWriteStore(Protocol):
    """What the analyst write service needs; see the module docstring."""

    async def ensure_indexes(self) -> None:
        """Create every index the queries below rely on (idempotent)."""
        ...

    async def commit(self, command: AnalystCommand) -> AnalystCommitted:
        """Apply ``command`` atomically (module docstring).

        Raises:
            fcrm_common.errors.NotFoundError: No such case.
            fcrm_case.errors.CaseClosedError: The case is closed.
            fcrm_common.errors.ConflictError: A review item the command names
                is no longer there.
        """
        ...

    async def get_decision(self, gate_id: str) -> Optional[AnalystDecisionRow]:
        """The ledger row of ``gate_id``, or ``None`` when it was not decided."""
        ...

    async def decided_gate_ids(self, case_id: str) -> frozenset[str]:
        """The gate ids the analyst decided on ``case_id``."""
        ...

    async def score_snapshots(self, case_id: str) -> list[ScoreSnapshotRow]:
        """The case's score snapshots that opened an acknowledgement gate."""
        ...

    async def threads(self, case_id: str) -> list[ClarificationThreadRow]:
        """The case's questions, oldest round first."""
        ...

    async def get_thread(
        self, case_id: str, thread_id: str
    ) -> Optional[ClarificationThreadRow]:
        """One question of the case, or ``None``."""
        ...


def recommendation_update(
    change: RecommendationChange,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The ``$set`` a recommendation change applies and the condition the stored
    row must meet for it to apply: an edit needs the row unlocked, a lock needs
    it unlocked, an unlock needs it locked.
    """
    if change.kind == "edit":
        assert change.edits is not None
        return {"edits": change.edits.model_dump(mode="json")}, {
            "locked": {"$ne": True}
        }
    if change.kind == "lock":
        return {
            "locked": True,
            "locked_at": change.at,
            "locked_by": change.by,
        }, {"locked": {"$ne": True}}
    return {
        "locked": False,
        "locked_at": None,
        "locked_by": None,
        "unlock_reason": change.reason,
        "unlocked_at": change.at,
        "unlocked_by": change.by,
    }, {"locked": True}


def decision_row(
    command: AnalystCommand,
    gate_id: str,
    gate_type: str,
    decision: str,
    fingerprint: str,
    now: datetime,
) -> AnalystDecisionRow:
    """The ledger row for one gate of ``command``."""
    return AnalystDecisionRow(
        gate_id=gate_id,
        case_id=command.case_id,
        gate_type=gate_type,
        decision=decision,
        fingerprint=fingerprint,
        decided_by=command.actor,
        decided_at=now,
    )


class MongoAnalystWriteStore:
    """The real store over ``fcrm_case``.

    Args:
        client: The Mongo client (transactions need the ``rs0`` replica set).
        db: The ``fcrm_case`` database.
        cases: The repository over ``cases``.
        views: The repositories over the view collections.
        outbox: The case service's outbox, so an event commits with its write.
        activity: The activity log (staged rows join the transaction).
    """

    def __init__(
        self,
        client: AsyncMongoClient[Any],
        db: Any,
        cases: Repository[CaseRecord],
        views: ViewRepos,
        outbox: Outbox,
        activity: MongoActivityLog,
    ) -> None:
        self._client = client
        self._cases = cases
        self._views = views
        self._outbox = outbox
        self._activity = activity
        self._decisions: Repository[AnalystDecisionRow] = Repository(
            db["analyst_decisions"], AnalystDecisionRow, id_of=lambda r: r.gate_id
        )
        self._recommendations = db["recommendations"]
        self._threads: Repository[ClarificationThreadRow] = Repository(
            db["clarification_threads"],
            ClarificationThreadRow,
            id_of=lambda r: r.thread_id,
        )

    async def ensure_indexes(self) -> None:
        """``analyst_decisions{case_id}`` (the open-gate read); the ledger is
        otherwise read by ``_id``.
        """
        await self._decisions.ensure_indexes(
            [IndexModel([("case_id", ASCENDING)], name="case_idx")]
        )

    # -- writes ----------------------------------------------------------------

    async def commit(self, command: AnalystCommand) -> AnalystCommitted:
        async def _do(session: AsyncClientSession) -> AnalystCommitted:
            return await self._commit_in(session, command)

        return await with_transaction(self._client, _do)

    async def _commit_in(
        self, session: AsyncClientSession, command: AnalystCommand
    ) -> AnalystCommitted:
        now = utcnow()
        record = await self._load(session, command.case_id)
        ensure_open(record)
        fresh = await self._record_gates(session, command, now)
        if command.gates and not fresh:
            return AnalystCommitted(record, replayed=True)
        # State first, events after: the decided items and the recommendation are
        # written before any event reaches the outbox (one transaction either way).
        if not await self._change_recommendation(session, command):
            return AnalystCommitted(record, replayed=True)
        await self._decide_items(session, command)
        await self._open_round(session, command)
        await self._resolve_threads(session, command)
        entries = await self._stage(session, command.case_id, command.activity)
        for gate in fresh:
            await self._outbox.enqueue(session, gate.event)
        for event in command.events:
            await self._outbox.enqueue(session, event)
        return AnalystCommitted(record, entries, frozenset(g.gate_id for g in fresh))

    async def _load(self, session: AsyncClientSession, case_id: str) -> CaseRecord:
        doc = await self._cases.collection.find_one(
            {"_id": case_id},
            projection={"submission_versions": 0, "documents": 0, "fields": 0},
            session=session,
        )
        if doc is None:
            raise NotFoundError(f"no such case {case_id}")
        return self._cases.from_document(doc)

    async def _record_gates(
        self, session: AsyncClientSession, command: AnalystCommand, now: datetime
    ) -> list[AnalystGate]:
        """Record each gate's decision in the ledger (an upsert that inserted) and
        return the gates whose row was new, whose events the caller enqueues; a
        gate decided before leaves the ledger alone and is not returned.
        """
        fresh: list[AnalystGate] = []
        for gate in command.gates:
            row = decision_row(
                command,
                gate.gate_id,
                gate.gate_type.value,
                gate.decision,
                gate.fingerprint,
                now,
            )
            fields = to_bson(row.model_dump(mode="python", exclude={"gate_id"}))
            result = await self._decisions.collection.update_one(
                {"_id": gate.gate_id},
                {"$setOnInsert": fields},
                upsert=True,
                session=session,
            )
            if result.upserted_id is not None:
                fresh.append(gate)
        return fresh

    async def _change_recommendation(
        self, session: AsyncClientSession, command: AnalystCommand
    ) -> bool:
        """Apply the command's change to the recommendation row, if it has one.
        ``False`` when a lock or unlock found the row already in that state (a
        replay: nothing else of the command is applied).

        Raises:
            RecommendationLockedError: An edit found the recommendation locked
                (or gone) by the time the transaction ran.
        """
        change = command.recommendation
        if change is None:
            return True
        fields, condition = recommendation_update(change)
        result = await self._recommendations.update_one(
            {"_id": command.case_id, **condition},
            {"$set": to_bson(fields)},
            session=session,
        )
        if result.matched_count == 1:
            return True
        if change.kind == "edit":
            raise RecommendationLockedError(
                "the recommendation is locked; unlock it with a reason to edit it",
                case_id=command.case_id,
            )
        return False

    async def _open_round(
        self, session: AsyncClientSession, command: AnalystCommand
    ) -> None:
        """Insert the questions of the round the command asks."""
        await self._threads.insert_many(command.threads, session=session)

    async def _resolve_threads(
        self, session: AsyncClientSession, command: AnalystCommand
    ) -> None:
        """Resolve the answered questions the command names.

        Raises:
            ConflictError: A question is not answered (it changed meanwhile).
        """
        for resolution in command.resolutions:
            result = await self._threads.collection.update_one(
                {
                    "_id": resolution.thread_id,
                    "case_id": command.case_id,
                    "status": "answered",
                },
                {
                    "$set": {
                        "status": "resolved",
                        "resolved_at": resolution.at,
                        "resolved_by": resolution.by,
                        "resolve_note": resolution.note,
                    }
                },
                session=session,
            )
            if result.matched_count != 1:
                raise ConflictError(
                    f"question {resolution.thread_id} is not an answered question "
                    "of this case",
                    case_id=command.case_id,
                )

    async def _decide_items(
        self, session: AsyncClientSession, command: AnalystCommand
    ) -> None:
        if not command.items:
            return
        operations = [
            UpdateOne(
                {"_id": item.item_id, "case_id": command.case_id},
                {
                    "$set": to_bson(
                        {
                            "status": item.status,
                            "decided": item.decided.model_dump(mode="python"),
                        }
                    )
                },
            )
            for item in command.items
        ]
        result = await self._views.review_items.collection.bulk_write(
            operations, ordered=True, session=session
        )
        if result.matched_count != len(operations):
            raise ConflictError(
                f"a review item of case {command.case_id} is no longer there"
            )

    async def _stage(
        self,
        session: AsyncClientSession,
        case_id: str,
        events: Sequence[UiEventIn],
    ) -> tuple[ActivityEntry, ...]:
        """Allocate ``len(events)`` consecutive seqs on the case and insert the
        rows (the same ``$inc``-then-``stage`` the PO store does).
        """
        rows = list(events)
        if not rows:
            return ()
        bumped = await self._cases.find_one_and_update(
            {"case_id": case_id},
            {"$inc": {"activity_seq": len(rows)}},
            session=session,
        )
        if bumped is None:
            raise NotFoundError(f"no such case {case_id}")
        first = bumped.activity_seq - len(rows) + 1
        return tuple(
            [
                await self._activity.stage(session, case_id, first + i, event)
                for i, event in enumerate(rows)
            ]
        )

    # -- reads -----------------------------------------------------------------

    async def get_decision(self, gate_id: str) -> Optional[AnalystDecisionRow]:
        return await self._decisions.find_one({"gate_id": gate_id})

    async def decided_gate_ids(self, case_id: str) -> frozenset[str]:
        docs = await self._decisions.collection.find(
            {"case_id": case_id}, projection={"_id": 1}, limit=MAX_ROWS_PER_CASE
        ).to_list()
        return frozenset(str(d["_id"]) for d in docs)

    async def threads(self, case_id: str) -> list[ClarificationThreadRow]:
        return await self._threads.find(
            {"case_id": case_id},
            sort=[("round", ASCENDING), ("question_id", ASCENDING)],
            limit=MAX_ROWS_PER_CASE,
        )

    async def get_thread(
        self, case_id: str, thread_id: str
    ) -> Optional[ClarificationThreadRow]:
        return await self._threads.find_one(
            {"thread_id": thread_id, "case_id": case_id}
        )

    async def score_snapshots(self, case_id: str) -> list[ScoreSnapshotRow]:
        return await self._views.score_snapshots.find(
            {"case_id": case_id, "gate_id": {"$ne": None}},
            sort=[("computed_at", ASCENDING)],
            limit=MAX_ROWS_PER_CASE,
        )


class InMemoryAnalystWriteStore:
    """The test twin: it reads and writes the same in-memory case and view stores
    the routes use, applies a command in the same order the Mongo store does
    (validating before it changes anything, so a refusal leaves no trace) and puts
    the events it would enqueue on ``cases.events``.

    Args:
        cases: The in-memory case store (its records and activity log).
        views: The in-memory view store (the review items).
        assessment: The in-memory assessment store (the score snapshots).
    """

    def __init__(
        self,
        cases: InMemoryCaseStore,
        views: InMemoryViewStore,
        assessment: InMemoryAssessmentStore,
    ) -> None:
        self._cases = cases
        self._views = views
        self._assessment = assessment
        self.decisions: dict[str, AnalystDecisionRow] = {}

    async def ensure_indexes(self) -> None:
        """Nothing to create."""

    async def commit(self, command: AnalystCommand) -> AnalystCommitted:
        record = self._cases.records.get(command.case_id)
        if record is None:
            raise NotFoundError(f"no such case {command.case_id}")
        ensure_open(record)
        now = utcnow()
        self._validate(command)
        fresh = [g for g in command.gates if g.gate_id not in self.decisions]
        if command.gates and not fresh:
            return AnalystCommitted(record, replayed=True)
        if not self._change_recommendation(command):
            return AnalystCommitted(record, replayed=True)
        for item in command.items:
            self._views.items[item.item_id] = self._views.items[
                item.item_id
            ].model_copy(update={"status": item.status, "decided": item.decided})
        for row in command.threads:
            self._views.threads[row.thread_id] = row
        for resolution in command.resolutions:
            self._resolve(command, resolution)
        for gate in fresh:
            self.decisions[gate.gate_id] = decision_row(
                command,
                gate.gate_id,
                gate.gate_type.value,
                gate.decision,
                gate.fingerprint,
                now,
            )
            self._cases.events.append(gate.event)
        self._cases.events.extend(command.events)
        base = record.activity_seq
        updated = record.model_copy(
            update={"activity_seq": base + len(command.activity)}
        )
        self._cases.records[command.case_id] = updated
        entries = tuple(
            self._cases.activity.stage(command.case_id, base + i + 1, event)
            for i, event in enumerate(command.activity)
        )
        return AnalystCommitted(record, entries, frozenset(g.gate_id for g in fresh))

    def _resolve(self, command: AnalystCommand, resolution: ThreadResolution) -> None:
        """The twin of ``MongoAnalystWriteStore._resolve_threads`` for one question."""
        thread = self._views.threads.get(resolution.thread_id)
        if (
            thread is None
            or thread.case_id != command.case_id
            or thread.status != "answered"
        ):
            raise ConflictError(
                f"question {resolution.thread_id} is not an answered question "
                "of this case",
                case_id=command.case_id,
            )
        self._views.threads[resolution.thread_id] = thread.model_copy(
            update={
                "status": "resolved",
                "resolved_at": resolution.at,
                "resolved_by": resolution.by,
                "resolve_note": resolution.note,
            }
        )

    def _change_recommendation(self, command: AnalystCommand) -> bool:
        """The twin of ``MongoAnalystWriteStore._change_recommendation``."""
        change = command.recommendation
        if change is None:
            return True
        row = self._assessment.recommendations.get(command.case_id)
        fields, _ = recommendation_update(change)
        wants_locked = change.kind == "unlock"
        if row is None or row.locked is not wants_locked:
            if change.kind == "edit":
                raise RecommendationLockedError(
                    "the recommendation is locked; unlock it with a reason to edit it",
                    case_id=command.case_id,
                )
            return False
        self._assessment.recommendations[command.case_id] = row.model_copy(
            update=fields
        )
        return True

    def _validate(self, command: AnalystCommand) -> None:
        """Everything the Mongo transaction would refuse, checked before any
        write so a refusal changes nothing.
        """
        for item in command.items:
            row = self._views.items.get(item.item_id)
            if row is None or row.case_id != command.case_id:
                raise ConflictError(
                    f"a review item of case {command.case_id} is no longer there"
                )

    async def get_decision(self, gate_id: str) -> Optional[AnalystDecisionRow]:
        return self.decisions.get(gate_id)

    async def decided_gate_ids(self, case_id: str) -> frozenset[str]:
        return frozenset(g for g, r in self.decisions.items() if r.case_id == case_id)

    async def threads(self, case_id: str) -> list[ClarificationThreadRow]:
        rows = [t for t in self._views.threads.values() if t.case_id == case_id]
        return sorted(rows, key=lambda t: (t.round, t.question_id))

    async def get_thread(
        self, case_id: str, thread_id: str
    ) -> Optional[ClarificationThreadRow]:
        row = self._views.threads.get(thread_id)
        return row if row is not None and row.case_id == case_id else None

    async def score_snapshots(self, case_id: str) -> list[ScoreSnapshotRow]:
        rows = [
            s
            for s in self._assessment.snapshots.values()
            if s.case_id == case_id and s.gate_id is not None
        ]
        return sorted(rows, key=lambda s: (s.computed_at, s.snapshot_id))
