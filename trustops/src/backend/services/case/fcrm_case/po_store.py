"""The persistence behind the product owner's surface.

Same split as :mod:`fcrm_case.store` and :mod:`fcrm_case.views_store`: a small
:class:`PoStore` ``Protocol`` the service depends on, a Mongo implementation the
lifespan wires and an in-memory twin for unit tests. What this store adds:

========================  ====================================================
``submission_versions``   frozen submissions, unique ``{case_id, version}``
``po_decisions``          the ledger of PO gate decisions (the first decision
                          on a gate emits ``gate.decided``; see
                          :class:`~fcrm_case.domain.po_rows.PoDecisionRow`)
``integrations``          one owner's integration choices, indexed ``{user_id}``
========================  ====================================================

**Writes** are one method, :meth:`PoStore.commit`, that applies a whole
:class:`~fcrm_case.domain.po_commands.PoCommand` in a single transaction: the
case document, its field values, review items, documents, the frozen submission,
the activity rows and the outbox events commit together or not at all (the
outbox rule). It returns what the route publishes to the stream *after* the
commit. **Reads** are bulk by construction: a page of cases is summarised with a
handful of ``$in`` queries, never one query per case.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Optional, Protocol

from fcrm_common.errors import ConflictError, NotFoundError
from fcrm_common.mongo import Repository, to_bson, with_transaction
from fcrm_common.outbox import Outbox
from fcrm_common.pagination import decode_cursor, encode_cursor
from fcrm_common.time import utcnow
from fcrm_contracts.api.case_internal import UiEventIn
from fcrm_contracts.common import Page
from fcrm_contracts.enums import ItemKind, ItemStatus
from fcrm_contracts.events.envelope import Envelope
from pymongo import ASCENDING, AsyncMongoClient, IndexModel, ReplaceOne, UpdateOne
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.errors import BulkWriteError, DuplicateKeyError

from fcrm_case.activity_log import MongoActivityLog
from fcrm_case.domain.activity import ActivityEntry
from fcrm_case.domain.fieldset import FieldSetData
from fcrm_case.domain.po_commands import (
    FieldWrite,
    PoCommand,
    events_to_enqueue,
    record_after,
    staged_activity,
)
from fcrm_case.domain.po_rows import (
    IntegrationRow,
    PoDecisionRow,
    SubmissionVersionRow,
    integration_row_id,
    po_decision_id,
)
from fcrm_case.domain.po_stage import (
    NEEDS_RESPONSE_STAGES,
    OPEN_CONDITION_STATUSES,
    CaseSummary,
    needs_response_filter,
    po_stage,
    stage_filter,
)
from fcrm_case.domain.record import CaseRecord
from fcrm_case.domain.views import ConditionRow, field_value_id
from fcrm_case.po_errors import AlreadySubmittedError, ensure_open
from fcrm_case.store import InMemoryCaseStore
from fcrm_case.views_store import InMemoryViewStore, ViewRepos, next_field_row

logger = logging.getLogger(__name__)

#: A PO owns tens of cases; this bound only stops a bug from reading a million.
MAX_OWNER_CASES: Final = 500

#: The case-document fields a PO command may change.
_CASE_KEYS: Final = frozenset(
    {
        "change_types",
        "required_fields",
        "domains",
        "submitted_at",
        "current_submission_v",
        "discarded_at",
        "discard_reason",
        "run_requested",
    }
)

_INTAKE_KINDS: Final = (ItemKind.CATEGORY.value, ItemKind.GAP.value)
_NON_BLANK: Final = {"$regex": r"\S"}


def gate_key(gate_type: str, key: str) -> str:
    """The name under which :attr:`PoCommitted.emitted` reports a gate."""
    return f"{gate_type}:{key}"


@dataclass(frozen=True)
class PoCommitted:
    """What a committed PO write leaves behind.

    Attributes:
        record: The case document after the write.
        entries: The activity rows staged in the transaction, to publish to the
            case stream now that it committed.
        emitted: The gates (:func:`gate_key`) whose ``gate.decided`` event this
            write enqueued; a gate decided before is absent.
    """

    record: CaseRecord
    entries: tuple[ActivityEntry, ...] = ()
    emitted: frozenset[str] = frozenset()


class PoStore(Protocol):
    """What the PO service needs; see the module docstring."""

    async def ensure_indexes(self) -> None:
        """Create every index the queries below rely on (idempotent)."""
        ...

    async def create_case(
        self, record: CaseRecord, event: Envelope, activity: UiEventIn
    ) -> PoCommitted:
        """Insert a new draft, its first activity row and ``case.created`` in
        one transaction.
        """
        ...

    async def commit(self, command: PoCommand) -> PoCommitted:
        """Apply ``command`` atomically (module docstring).

        Raises:
            fcrm_common.errors.NotFoundError: No such case, or a discarded one.
            fcrm_case.po_errors.CaseClosedError: The case is decided or closed.
            fcrm_case.po_errors.AlreadySubmittedError: The case was submitted.
            fcrm_common.errors.ConflictError: A document id or a review item
                the command names conflicts with stored state.
        """
        ...

    async def list_cases(
        self,
        owner: str,
        *,
        stage: Optional[str],
        needs_response: bool,
        limit: int,
        cursor: Optional[str],
    ) -> Page[CaseRecord]:
        """One page of ``owner``'s cases, newest first, filtered by PO stage."""
        ...

    async def owner_cases(self, owner: str) -> list[CaseRecord]:
        """Every case ``owner`` has not discarded, newest first (bounded)."""
        ...

    async def summaries(self, case_ids: Sequence[str]) -> dict[str, CaseSummary]:
        """A :class:`CaseSummary` per id, from a fixed number of bulk queries."""
        ...

    async def average_days_to_decision(self) -> float:
        """Mean days from intake to decision over every decided case, to one
        decimal (0.0 when none is).
        """
        ...

    async def latest_unit(self, owner: str) -> Optional[str]:
        """The unit of ``owner``'s newest case, or ``None``."""
        ...

    async def integration_rows(self, user_id: str) -> dict[str, IntegrationRow]:
        """``user_id``'s stored integration choices by integration id."""
        ...

    async def save_integration(self, row: IntegrationRow) -> None:
        """Insert or replace one integration choice."""
        ...

    async def get_submission(
        self, case_id: str, version: int
    ) -> Optional[SubmissionVersionRow]:
        """One frozen submission, or ``None``."""
        ...


def _decision_row(
    command: PoCommand, gate_type: str, key: str, now: datetime
) -> PoDecisionRow:
    return PoDecisionRow(
        decision_id=po_decision_id(command.case_id, gate_type, key),
        case_id=command.case_id,
        gate_type=gate_type,
        key=key,
        decided_by=command.actor,
        decided_at=now,
    )


def _case_changes(before: CaseRecord, after: CaseRecord) -> dict[str, Any]:
    """The case-document fields ``after`` changed from ``before``."""
    old = before.model_dump(mode="python", include=set(_CASE_KEYS))
    new = after.model_dump(mode="python", include=set(_CASE_KEYS))
    return {key: value for key, value in new.items() if value != old[key]}


def _open_pipeline(ids: Sequence[str]) -> list[dict[str, Any]]:
    """Open conditions per case."""
    return [
        {
            "$match": {
                "case_id": {"$in": list(ids)},
                "status": {"$in": list(OPEN_CONDITION_STATUSES)},
            }
        },
        {"$group": {"_id": "$case_id", "n": {"$sum": 1}}},
    ]


def _threads_pipeline(ids: Sequence[str]) -> list[dict[str, Any]]:
    """Questions from FCRM still waiting for an answer, per case."""
    return [
        {"$match": {"case_id": {"$in": list(ids)}, "status": "awaiting_response"}},
        {"$group": {"_id": "$case_id", "n": {"$sum": 1}}},
    ]


async def _group_counts(
    collection: Any, pipeline: list[dict[str, Any]]
) -> dict[str, int]:
    """Run a ``{_id: case_id, n}`` grouping and read it as a dict."""
    cursor = await collection.aggregate(pipeline)
    return {str(row["_id"]): int(row["n"]) for row in await cursor.to_list()}


class MongoPoStore:
    """The real store over ``fcrm_case``.

    Args:
        client: The Mongo client (transactions need the ``rs0`` replica set).
        db: The ``fcrm_case`` database.
        cases: The repository over ``cases``.
        views: The repositories over the view collections.
        outbox: The case service's outbox, so an event commits with its write.
        activity: The activity log (staged rows join the transaction).
        field_sets: The field set of a pinned version, which a confirmed type
            resolves its fields against.
        type_domains: The risk domains each change type brings into scope.
    """

    def __init__(
        self,
        client: AsyncMongoClient[Any],
        db: Any,
        cases: Repository[CaseRecord],
        views: ViewRepos,
        outbox: Outbox,
        activity: MongoActivityLog,
        *,
        field_sets: Callable[[int], FieldSetData],
        type_domains: Mapping[str, Sequence[str]],
    ) -> None:
        self._client = client
        self._cases = cases
        self._views = views
        self._outbox = outbox
        self._activity = activity
        self._field_sets = field_sets
        self._type_domains = type_domains
        self._decisions = db["po_decisions"]
        self._threads = db["clarification_threads"]
        self._submissions: Repository[SubmissionVersionRow] = Repository(
            db["submission_versions"],
            SubmissionVersionRow,
            id_of=lambda r: r.version_id,
        )
        self._integrations: Repository[IntegrationRow] = Repository(
            db["integrations"],
            IntegrationRow,
            id_of=lambda r: integration_row_id(r.user_id, r.integration_id),
        )

    async def ensure_indexes(self) -> None:
        """Annex F for what this store adds: the unique submission version, the
        owner's integrations, and the owner's case list in the order it reads
        (newest first).
        """
        await self._submissions.ensure_indexes(
            [
                IndexModel(
                    [("case_id", ASCENDING), ("version", ASCENDING)],
                    unique=True,
                    name="case_version_unique",
                )
            ]
        )
        await self._integrations.ensure_indexes(
            [IndexModel([("user_id", ASCENDING)], name="user_idx")]
        )
        await self._cases.ensure_indexes(
            [IndexModel([("owner", ASCENDING), ("_id", -1)], name="owner_case_desc")]
        )

    # -- writes ----------------------------------------------------------------

    async def create_case(
        self, record: CaseRecord, event: Envelope, activity: UiEventIn
    ) -> PoCommitted:
        stored = record.model_copy(update={"activity_seq": 1, "run_requested": True})

        async def _do(session: AsyncClientSession) -> PoCommitted:
            await self._cases.insert_one(stored, session=session)
            entry = await self._activity.stage(session, stored.case_id, 1, activity)
            await self._outbox.enqueue(session, event)
            return PoCommitted(stored, (entry,))

        return await with_transaction(self._client, _do)

    async def commit(self, command: PoCommand) -> PoCommitted:
        async def _do(session: AsyncClientSession) -> PoCommitted:
            return await self._commit_in(session, command)

        try:
            return await with_transaction(self._client, _do)
        except BulkWriteError as exc:
            logger.warning(
                "po write conflicted with stored documents",
                extra={"case_id": command.case_id},
            )
            raise ConflictError(
                "a document with that id already exists on this case"
            ) from exc
        except DuplicateKeyError as exc:
            if command.submission is None:
                raise
            raise AlreadySubmittedError(
                f"case {command.case_id} was already submitted"
            ) from exc

    async def _commit_in(
        self, session: AsyncClientSession, command: PoCommand
    ) -> PoCommitted:
        now = utcnow()
        record = await self._load(session, command.case_id)
        ensure_open(record)
        updated = record_after(
            record,
            command,
            now,
            field_sets=self._field_sets,
            type_domains=self._type_domains,
        )
        # The ledger first: whether a gate emits decides which activity rows
        # exist, and the case update below allocates their sequence numbers.
        emitted = await self._emit_gates(session, command, now)
        staged = staged_activity(command, emitted)
        updated = await self._write_case(session, record, updated, len(staged))
        await self._write_fields(session, command, now)
        await self._decide_items(session, command)
        if command.documents:
            await self._views.documents.insert_many(command.documents, session=session)
        if command.submission is not None:
            await self._submissions.insert_one(command.submission, session=session)
        for event in events_to_enqueue(record, command):
            await self._outbox.enqueue(session, event)
        first = updated.activity_seq - len(staged) + 1
        entries = [
            await self._activity.stage(session, command.case_id, first + i, event)
            for i, event in enumerate(staged)
        ]
        return PoCommitted(updated, tuple(entries), emitted)

    async def _load(self, session: AsyncClientSession, case_id: str) -> CaseRecord:
        doc = await self._cases.collection.find_one(
            {"_id": case_id},
            projection={"submission_versions": 0, "documents": 0, "fields": 0},
            session=session,
        )
        if doc is None:
            raise NotFoundError(f"no such case {case_id}")
        return self._cases.from_document(doc)

    async def _write_case(
        self,
        session: AsyncClientSession,
        before: CaseRecord,
        after: CaseRecord,
        activity_rows: int,
    ) -> CaseRecord:
        """Set what the command changed on the case document and allocate its
        ``activity_rows`` sequence numbers in the same update.
        """
        changes = _case_changes(before, after)
        update: dict[str, Any] = {}
        if changes:
            update["$set"] = changes
        if activity_rows:
            update["$inc"] = {"activity_seq": activity_rows}
        if not update:
            return after
        written = await self._cases.find_one_and_update(
            {"case_id": after.case_id}, update, session=session
        )
        if written is None:
            raise NotFoundError(f"no such case {after.case_id}")
        return after.model_copy(update={"activity_seq": written.activity_seq})

    async def _write_fields(
        self, session: AsyncClientSession, command: PoCommand, now: datetime
    ) -> None:
        if not command.fields:
            return
        repo = self._views.field_values
        ids = [field_value_id(command.case_id, w.field_id) for w in command.fields]
        stored = await repo.collection.find(
            {"_id": {"$in": ids}}, session=session
        ).to_list()
        previous = {d["_id"]: repo.from_document(d) for d in stored}
        operations = []
        for write, row_id in zip(command.fields, ids, strict=True):
            row = next_field_row(
                previous.get(row_id),
                command.case_id,
                write.field_id,
                write.label,
                write.value,
                write.origin,
                write.source,
                command.actor,
                now,
            )
            operations.append(
                ReplaceOne({"_id": row_id}, repo.to_document(row), upsert=True)
            )
        await repo.collection.bulk_write(operations, ordered=True, session=session)

    async def _decide_items(
        self, session: AsyncClientSession, command: PoCommand
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

    async def _emit_gates(
        self, session: AsyncClientSession, command: PoCommand, now: datetime
    ) -> frozenset[str]:
        """Record each gate's decision in the ledger and enqueue its event
        when the ledger row is new (an upsert that inserted); a gate decided
        before leaves the ledger and the outbox alone.
        """
        emitted: set[str] = set()
        for gate in command.gates:
            row = _decision_row(command, gate.gate_type, gate.key, now)
            fields = to_bson(row.model_dump(mode="python", exclude={"decision_id"}))
            result = await self._decisions.update_one(
                {"_id": row.decision_id},
                {"$setOnInsert": fields},
                upsert=True,
                session=session,
            )
            if result.upserted_id is not None:
                await self._outbox.enqueue(session, gate.event)
                emitted.add(gate_key(gate.gate_type, gate.key))
        return frozenset(emitted)

    # -- reads -----------------------------------------------------------------

    async def list_cases(
        self,
        owner: str,
        *,
        stage: Optional[str],
        needs_response: bool,
        limit: int,
        cursor: Optional[str],
    ) -> Page[CaseRecord]:
        clauses: list[dict[str, Any]] = [{"owner": owner, "discarded_at": None}]
        if stage is not None:
            clauses.append(stage_filter(stage))
        if needs_response:
            clauses.append(needs_response_filter())
        query = clauses[0] if len(clauses) == 1 else {"$and": clauses}
        return await self._cases.find_paginated(
            query, limit=limit, cursor=cursor, descending=True, with_total=True
        )

    async def owner_cases(self, owner: str) -> list[CaseRecord]:
        docs = await self._cases.collection.find(
            {"owner": owner, "discarded_at": None},
            sort=[("_id", -1)],
            limit=MAX_OWNER_CASES,
        ).to_list()
        return [self._cases.from_document(d) for d in docs]

    async def summaries(self, case_ids: Sequence[str]) -> dict[str, CaseSummary]:
        ids = list(dict.fromkeys(case_ids))
        if not ids:
            return {}
        conditions, threads, filled, documents, pending, ready = await asyncio.gather(
            _group_counts(self._views.conditions.collection, _open_pipeline(ids)),
            _group_counts(self._threads, _threads_pipeline(ids)),
            self._filled_by_case(ids),
            self._documents_by_case(ids),
            self._pending_by_case(ids),
            self._ready_cases(ids),
        )
        summaries: dict[str, CaseSummary] = {}
        for case_id in ids:
            docs = documents.get(case_id, {})
            types, blocking = pending.get(case_id, ([], 0))
            summaries[case_id] = CaseSummary(
                open_conditions=conditions.get(case_id, 0),
                open_threads=threads.get(case_id, 0),
                filled_field_ids=frozenset(filled.get(case_id, ())),
                documents=sum(docs.values()),
                documents_unread=docs.get("stored", 0),
                documents_unreadable=docs.get("failed", 0) + docs.get("quarantined", 0),
                intake_ready=case_id in ready,
                pending_types=tuple(sorted(types)),
                blocking_gaps_open=blocking,
            )
        return summaries

    async def _filled_by_case(self, ids: Sequence[str]) -> dict[str, list[str]]:
        cursor = await self._views.field_values.collection.aggregate(
            [
                {"$match": {"case_id": {"$in": list(ids)}, "value": _NON_BLANK}},
                {"$group": {"_id": "$case_id", "fields": {"$addToSet": "$field_id"}}},
            ]
        )
        return {str(r["_id"]): list(r["fields"]) for r in await cursor.to_list()}

    async def _documents_by_case(self, ids: Sequence[str]) -> dict[str, dict[str, int]]:
        cursor = await self._views.documents.collection.aggregate(
            [
                {"$match": {"case_id": {"$in": list(ids)}}},
                {
                    "$group": {
                        "_id": {"c": "$case_id", "s": "$status"},
                        "n": {"$sum": 1},
                    }
                },
            ]
        )
        counts: dict[str, dict[str, int]] = {}
        for row in await cursor.to_list():
            counts.setdefault(str(row["_id"]["c"]), {})[str(row["_id"]["s"])] = int(
                row["n"]
            )
        return counts

    async def _pending_by_case(
        self, ids: Sequence[str]
    ) -> dict[str, tuple[list[str], int]]:
        """Per case: the change types still proposed and how many blocking
        questions are unanswered, from one grouped read of the open intake items.
        """
        cursor = await self._views.review_items.collection.aggregate(
            [
                {
                    "$match": {
                        "case_id": {"$in": list(ids)},
                        "kind": {"$in": list(_INTAKE_KINDS)},
                        "status": ItemStatus.PROPOSED.value,
                    }
                },
                {
                    "$group": {
                        "_id": {"c": "$case_id", "k": "$kind"},
                        "types": {"$push": "$subject.change_type"},
                        "blocking": {
                            "$sum": {
                                "$cond": [{"$eq": ["$subject.blocking", True]}, 1, 0]
                            }
                        },
                    }
                },
            ]
        )
        pending: dict[str, tuple[list[str], int]] = {}
        for row in await cursor.to_list():
            case_id = str(row["_id"]["c"])
            types, blocking = pending.get(case_id, ([], 0))
            if row["_id"]["k"] == ItemKind.CATEGORY.value:
                types = [str(t) for t in row["types"] if t is not None]
            else:
                blocking = int(row["blocking"])
            pending[case_id] = (types, blocking)
        return pending

    async def _ready_cases(self, ids: Sequence[str]) -> set[str]:
        docs = await self._views.intake_views.collection.find(
            {"_id": {"$in": list(ids)}}, projection={"_id": 1}
        ).to_list()
        return {str(d["_id"]) for d in docs}

    async def average_days_to_decision(self) -> float:
        cursor = await self._cases.collection.aggregate(
            [
                {"$match": {"state": "closed", "decided_at": {"$ne": None}}},
                {
                    "$group": {
                        "_id": None,
                        "avg": {
                            "$avg": {
                                "$dateDiff": {
                                    "startDate": "$created_at",
                                    "endDate": "$decided_at",
                                    "unit": "day",
                                }
                            }
                        },
                    }
                },
            ]
        )
        rows = await cursor.to_list()
        return round(float(rows[0]["avg"]), 1) if rows else 0.0

    async def latest_unit(self, owner: str) -> Optional[str]:
        doc = await self._cases.collection.find_one(
            {"owner": owner}, projection={"unit": 1}, sort=[("_id", -1)]
        )
        return str(doc["unit"]) if doc is not None and doc.get("unit") else None

    async def integration_rows(self, user_id: str) -> dict[str, IntegrationRow]:
        rows = await self._integrations.find({"user_id": user_id}, limit=50)
        return {row.integration_id: row for row in rows}

    async def save_integration(self, row: IntegrationRow) -> None:
        document = self._integrations.to_document(row)
        await self._integrations.collection.replace_one(
            {"_id": document["_id"]}, document, upsert=True
        )

    async def get_submission(
        self, case_id: str, version: int
    ) -> Optional[SubmissionVersionRow]:
        return await self._submissions.find_one(
            {"case_id": case_id, "version": version}
        )


class InMemoryPoStore:
    """The test twin: it reads and writes the same in-memory case and view
    stores the routes use, applies a command in the same order the Mongo store
    does (validating before it changes anything, so a refusal leaves no trace)
    and puts the events it would enqueue on ``cases.events``.

    Args:
        cases: The in-memory case store (its records and activity log).
        views: The in-memory view store (field values, documents, items).
        field_sets: The field set of a pinned version, which a confirmed type
            resolves its fields against.
        type_domains: The risk domains each change type brings into scope.
    """

    def __init__(
        self,
        cases: InMemoryCaseStore,
        views: InMemoryViewStore,
        *,
        field_sets: Callable[[int], FieldSetData],
        type_domains: Mapping[str, Sequence[str]],
    ) -> None:
        self._cases = cases
        self._views = views
        self._field_sets = field_sets
        self._type_domains = type_domains
        self.submissions: dict[str, SubmissionVersionRow] = {}
        self.decisions: dict[str, PoDecisionRow] = {}
        self.integrations: dict[str, IntegrationRow] = {}
        #: What S1's later lanes store; tests put rows here to see them counted.
        self.conditions: list[ConditionRow] = []
        self.open_threads: dict[str, int] = {}

    async def ensure_indexes(self) -> None:
        """Nothing to create."""

    async def create_case(
        self, record: CaseRecord, event: Envelope, activity: UiEventIn
    ) -> PoCommitted:
        stored = record.model_copy(update={"activity_seq": 1, "run_requested": True})
        self._cases.records[stored.case_id] = stored
        entry = self._cases.activity.stage(stored.case_id, 1, activity)
        self._cases.events.append(event)
        return PoCommitted(stored, (entry,))

    async def commit(self, command: PoCommand) -> PoCommitted:
        record = self._cases.records.get(command.case_id)
        if record is None:
            raise NotFoundError(f"no such case {command.case_id}")
        ensure_open(record)
        now = utcnow()
        updated = record_after(
            record,
            command,
            now,
            field_sets=self._field_sets,
            type_domains=self._type_domains,
        )
        self._validate(command)
        emitted = self._emit_gates(command, now)
        staged = staged_activity(command, emitted)
        for write in command.fields:
            self._write_field(command, write, now)
        for item in command.items:
            self._views.items[item.item_id] = self._views.items[
                item.item_id
            ].model_copy(update={"status": item.status, "decided": item.decided})
        for row in command.documents:
            self._views.documents[row.doc_id] = row
        if command.submission is not None:
            self.submissions[command.submission.version_id] = command.submission
        self._cases.events.extend(events_to_enqueue(record, command))
        updated = updated.model_copy(
            update={"activity_seq": record.activity_seq + len(staged)}
        )
        self._cases.records[command.case_id] = updated
        entries = tuple(
            self._cases.activity.stage(
                command.case_id, record.activity_seq + i + 1, event
            )
            for i, event in enumerate(staged)
        )
        return PoCommitted(updated, entries, emitted)

    def _validate(self, command: PoCommand) -> None:
        """Everything the Mongo transaction would refuse, checked before any
        write so a refusal changes nothing.
        """
        for item in command.items:
            row = self._views.items.get(item.item_id)
            if row is None or row.case_id != command.case_id:
                raise ConflictError(
                    f"a review item of case {command.case_id} is no longer there"
                )
        for doc in command.documents:
            if doc.doc_id in self._views.documents:
                raise ConflictError(
                    "a document with that id already exists on this case"
                )
        if command.submission is not None and any(
            s.case_id == command.case_id and s.version == command.submission.version
            for s in self.submissions.values()
        ):
            raise AlreadySubmittedError(f"case {command.case_id} was already submitted")

    def _write_field(
        self, command: PoCommand, write: FieldWrite, now: datetime
    ) -> None:
        row_id = field_value_id(command.case_id, write.field_id)
        self._views.field_values[row_id] = next_field_row(
            self._views.field_values.get(row_id),
            command.case_id,
            write.field_id,
            write.label,
            write.value,
            write.origin,
            write.source,
            command.actor,
            now,
        )

    def _emit_gates(self, command: PoCommand, now: datetime) -> frozenset[str]:
        emitted: set[str] = set()
        for gate in command.gates:
            row = _decision_row(command, gate.gate_type, gate.key, now)
            if row.decision_id in self.decisions:
                continue
            self.decisions[row.decision_id] = row
            self._cases.events.append(gate.event)
            emitted.add(gate_key(gate.gate_type, gate.key))
        return frozenset(emitted)

    async def list_cases(
        self,
        owner: str,
        *,
        stage: Optional[str],
        needs_response: bool,
        limit: int,
        cursor: Optional[str],
    ) -> Page[CaseRecord]:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        rows = [
            r
            for r in self._cases.records.values()
            if r.owner == owner
            and r.discarded_at is None
            and (stage is None or po_stage(r) == stage)
            and (not needs_response or po_stage(r) in NEEDS_RESPONSE_STAGES)
        ]
        rows.sort(key=lambda r: r.case_id, reverse=True)
        total = len(rows)
        if cursor is not None:
            after = decode_cursor(cursor)
            rows = [r for r in rows if r.case_id < after]
        page = rows[:limit]
        more = len(rows) > limit
        return Page(
            items=page,
            next_cursor=encode_cursor(page[-1].case_id) if more and page else None,
            total=total,
        )

    async def owner_cases(self, owner: str) -> list[CaseRecord]:
        rows = [
            r
            for r in self._cases.records.values()
            if r.owner == owner and r.discarded_at is None
        ]
        return sorted(rows, key=lambda r: r.case_id, reverse=True)[:MAX_OWNER_CASES]

    async def summaries(self, case_ids: Sequence[str]) -> dict[str, CaseSummary]:
        return {case_id: self._summary(case_id) for case_id in dict.fromkeys(case_ids)}

    def _summary(self, case_id: str) -> CaseSummary:
        rows = [r for r in self._views.field_values.values() if r.case_id == case_id]
        docs = [d for d in self._views.documents.values() if d.case_id == case_id]
        items = [
            i
            for i in self._views.items.values()
            if i.case_id == case_id and i.status == ItemStatus.PROPOSED
        ]
        return CaseSummary(
            open_conditions=sum(
                1
                for c in self.conditions
                if c.case_id == case_id and c.status in OPEN_CONDITION_STATUSES
            ),
            open_threads=self.open_threads.get(case_id, 0),
            filled_field_ids=frozenset(
                r.field_id for r in rows if r.value and r.value.strip()
            ),
            documents=len(docs),
            documents_unread=sum(1 for d in docs if d.status == "stored"),
            documents_unreadable=sum(
                1 for d in docs if d.status in ("failed", "quarantined")
            ),
            intake_ready=case_id in self._views.intake,
            pending_types=tuple(
                sorted(
                    str(i.subject["change_type"])
                    for i in items
                    if i.kind == ItemKind.CATEGORY and "change_type" in i.subject
                )
            ),
            blocking_gaps_open=sum(
                1
                for i in items
                if i.kind == ItemKind.GAP and i.subject.get("blocking") is True
            ),
        )

    async def average_days_to_decision(self) -> float:
        days = [
            (r.decided_at.date() - r.created_at.date()).days
            for r in self._cases.records.values()
            if r.state.value == "closed" and r.decided_at is not None
        ]
        return round(sum(days) / len(days), 1) if days else 0.0

    async def latest_unit(self, owner: str) -> Optional[str]:
        rows = sorted(
            (r for r in self._cases.records.values() if r.owner == owner),
            key=lambda r: r.case_id,
            reverse=True,
        )
        return rows[0].unit if rows and rows[0].unit else None

    async def integration_rows(self, user_id: str) -> dict[str, IntegrationRow]:
        return {
            row.integration_id: row
            for row in self.integrations.values()
            if row.user_id == user_id
        }

    async def save_integration(self, row: IntegrationRow) -> None:
        self.integrations[integration_row_id(row.user_id, row.integration_id)] = row

    async def get_submission(
        self, case_id: str, version: int
    ) -> Optional[SubmissionVersionRow]:
        for row in self.submissions.values():
            if row.case_id == case_id and row.version == version:
                return row
        return None
