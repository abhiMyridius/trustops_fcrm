"""The persistence behind the owner's follow-up after submission: answering a
question FCRM asked, evidencing a condition, and reading the plain outcome.

Same split as :mod:`fcrm_case.po_store`: a small :class:`PoFollowupStore`
``Protocol`` the service depends on, a Mongo implementation the lifespan wires and
an in-memory twin for unit tests.

**Writes** are one method, :meth:`PoFollowupStore.commit`, that applies a whole
:class:`~fcrm_case.domain.po_followup.FollowupCommand` in a single transaction:
the answered thread, the values and documents the answer brings, the frozen next
submission (when the answer closes the round; decided inside the transaction, so
two answers arriving together cannot both think the other is still open), the
activity rows and the outbox events commit together or not at all.

**Reads are fenced at the data layer.** The outcome and the conditions are read
with a projection that leaves out what only staff may see (the analyst's reason
and request, the condition's provenance and review note), so a view built from
them cannot leak what it never held.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Final, Optional, Protocol

from fcrm_common.errors import ConflictError, NotFoundError
from fcrm_common.mongo import Repository, with_transaction
from fcrm_common.outbox import Outbox
from fcrm_common.time import utcnow
from fcrm_contracts.api.case_internal import UiEventIn
from fcrm_contracts.enums import Outcome
from fcrm_contracts.events.envelope import Envelope
from pydantic import BaseModel, ConfigDict
from pymongo import ASCENDING, AsyncMongoClient, IndexModel, ReplaceOne
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.errors import BulkWriteError

from fcrm_case.activity_log import MongoActivityLog
from fcrm_case.domain.activity import ActivityEntry
from fcrm_case.domain.clarifications import ClarificationThreadRow, open_threads
from fcrm_case.domain.po_followup import (
    EvidenceWrite,
    FollowupCommand,
    case_answered_envelope,
    condition_evidence_envelope,
    evidence_changes,
    po_row,
    round_submission,
    staff_row,
)
from fcrm_case.domain.po_rows import SubmissionVersionRow
from fcrm_case.domain.record import CaseRecord
from fcrm_case.domain.views import ConditionRow, field_value_id
from fcrm_case.errors import ensure_open
from fcrm_case.store import InMemoryCaseStore
from fcrm_case.views_store import InMemoryViewStore, ViewRepos, next_field_row

if TYPE_CHECKING:
    from fcrm_case.outcome_store import InMemoryOutcomeStore
    from fcrm_case.po_store import InMemoryPoStore

logger = logging.getLogger(__name__)

#: A case holds tens of threads, fields and documents; these only stop a bug from
#: reading a million.
MAX_ROWS_PER_CASE: Final = 500

#: What a condition read leaves out: only staff see where a condition came from,
#: which pack it was in and the analyst's review note.
_CONDITION_STAFF_ONLY: Final = {"source": 0, "pack_id": 0, "note": 0}


class ThreadNotOpenError(ConflictError):
    """The question is not waiting for an answer (it was answered, or the analyst
    resolved it).
    """

    code = "thread_not_open"
    title = "Question not open"


class DecidedOutcome(BaseModel):
    """The part of a case's outcome the owner may read: what was decided, the
    plain words for the owner and when. Never the analyst's reason or the pack.
    """

    model_config = ConfigDict(extra="forbid")

    outcome: Outcome
    plain_summary: Optional[str] = None
    decided_at: Optional[datetime] = None


@dataclass(frozen=True)
class FollowupCommitted:
    """What a committed follow-up write leaves behind.

    Attributes:
        record: The case document as the transaction saw it.
        entries: The activity rows staged in the transaction, to publish to the
            case stream now that it committed.
        submission_version: The version this write froze (the answer closed its
            round), or ``None``.
        replayed: The write had already happened (evidence the owner already sent
            word for word): nothing was written.
    """

    record: CaseRecord
    entries: tuple[ActivityEntry, ...] = ()
    submission_version: Optional[int] = None
    replayed: bool = False


class PoFollowupStore(Protocol):
    """What the owner's follow-up service needs; see the module docstring."""

    async def ensure_indexes(self) -> None:
        """Create every index the queries below rely on (idempotent)."""
        ...

    async def commit(self, command: FollowupCommand) -> FollowupCommitted:
        """Apply ``command`` atomically (module docstring).

        Raises:
            fcrm_common.errors.NotFoundError: No such case or condition.
            fcrm_case.errors.CaseClosedError: An answer to a closed case.
            ThreadNotOpenError: The question is not waiting for an answer.
            fcrm_case.errors.ConditionClosedError: The condition is closed.
        """
        ...

    async def threads(self, case_id: str) -> list[ClarificationThreadRow]:
        """The case's questions, oldest round first."""
        ...

    async def get_thread(
        self, case_id: str, thread_id: str
    ) -> Optional[ClarificationThreadRow]:
        """One question of the case, or ``None``."""
        ...

    async def decided_outcome(self, case_id: str) -> Optional[DecidedOutcome]:
        """What was decided, in the fields the owner may read; ``None`` before."""
        ...

    async def conditions(self, case_id: str) -> list[ConditionRow]:
        """The case's conditions without what only staff see, soonest due first."""
        ...

    async def condition(
        self, case_id: str, condition_id: str
    ) -> Optional[ConditionRow]:
        """One condition of the case without what only staff see, or ``None``."""
        ...


def _stripped(row: ConditionRow) -> ConditionRow:
    """``row`` without what only staff may see."""
    return row.model_copy(update={"source": {}, "pack_id": None, "note": None})


def round_rows(
    command: FollowupCommand, thread: ClarificationThreadRow, version: int
) -> list[UiEventIn]:
    """The activity rows a closed round adds: the owner's own line and the
    analyst's.
    """
    return [
        po_row(
            command.actor,
            "clarification.answered",
            f"You answered FCRM's questions · saved as version {version}",
            {"round": thread.round, "submission_version": version},
        ),
        staff_row(
            command.actor,
            "submission.created",
            f"The owner answered round {thread.round}: submission v{version} saved",
            {"round": thread.round, "submission_version": version},
        ),
    ]


class MongoPoFollowupStore:
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
        self._threads: Repository[ClarificationThreadRow] = Repository(
            db["clarification_threads"],
            ClarificationThreadRow,
            id_of=lambda r: r.thread_id,
        )
        self._submissions: Repository[SubmissionVersionRow] = Repository(
            db["submission_versions"],
            SubmissionVersionRow,
            id_of=lambda r: r.version_id,
        )
        self._outcomes = db["outcomes"]

    async def ensure_indexes(self) -> None:
        """``clarification_threads{case_id, round}`` (Annex F)."""
        await self._threads.ensure_indexes(
            [
                IndexModel(
                    [("case_id", ASCENDING), ("round", ASCENDING)], name="case_round"
                )
            ]
        )

    # -- writes ----------------------------------------------------------------

    async def commit(self, command: FollowupCommand) -> FollowupCommitted:
        async def _do(session: AsyncClientSession) -> FollowupCommitted:
            return await self._commit_in(session, command)

        try:
            return await with_transaction(self._client, _do)
        except BulkWriteError as exc:
            logger.warning(
                "follow-up write conflicted with stored documents",
                extra={"case_id": command.case_id},
            )
            raise ConflictError(
                "a document with that id already exists on this case"
            ) from exc

    async def _commit_in(
        self, session: AsyncClientSession, command: FollowupCommand
    ) -> FollowupCommitted:
        now = utcnow()
        record = await self._load(session, command.case_id)
        if command.evidence is None:
            ensure_open(record)
        events: list[Envelope] = []
        rows: list[UiEventIn] = list(command.activity)
        changes: dict[str, Any] = {}
        version: Optional[int] = None
        if command.evidence is not None:
            changed = await self._evidence(session, command, events)
            if not changed:
                return FollowupCommitted(record, replayed=True)
        if command.answer is not None:
            thread = await self._answer(session, command)
            await self._write_fields(session, command, now)
            if command.documents:
                await self._views.documents.insert_many(
                    command.documents, session=session
                )
            events.extend(command.events)
            closed = await self._close_round(session, record, thread, command, now)
            if closed is not None:
                submission, envelope = closed
                version = submission.version
                events.append(envelope)
                rows.extend(round_rows(command, thread, version))
                changes["current_submission_v"] = version
        entries = await self._touch_case(session, record, rows, changes)
        for event in events:
            await self._outbox.enqueue(session, event)
        return FollowupCommitted(record, entries, version)

    async def _load(self, session: AsyncClientSession, case_id: str) -> CaseRecord:
        doc = await self._cases.collection.find_one(
            {"_id": case_id},
            projection={"submission_versions": 0, "documents": 0, "fields": 0},
            session=session,
        )
        if doc is None:
            raise NotFoundError(f"no such case {case_id}")
        return self._cases.from_document(doc)

    async def _answer(
        self, session: AsyncClientSession, command: FollowupCommand
    ) -> ClarificationThreadRow:
        assert command.answer is not None
        updated = await self._threads.find_one_and_update(
            {
                "thread_id": command.answer.thread_id,
                "case_id": command.case_id,
                "status": "awaiting_response",
            },
            {
                "status": "answered",
                "answer": command.answer.answer.model_dump(mode="python"),
            },
            session=session,
        )
        if updated is None:
            raise ThreadNotOpenError(
                "this question is not waiting for an answer",
                case_id=command.case_id,
                thread_id=command.answer.thread_id,
            )
        return updated

    async def _write_fields(
        self, session: AsyncClientSession, command: FollowupCommand, now: datetime
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

    async def _close_round(
        self,
        session: AsyncClientSession,
        record: CaseRecord,
        thread: ClarificationThreadRow,
        command: FollowupCommand,
        now: datetime,
    ) -> Optional[tuple[SubmissionVersionRow, Envelope]]:
        """When this answer was the round's last, freeze the next submission."""
        in_round = await self._threads.find(
            {"case_id": command.case_id, "round": thread.round},
            sort=[("question_id", ASCENDING)],
            limit=MAX_ROWS_PER_CASE,
            session=session,
        )
        if open_threads(in_round):
            return None
        answered = [t for t in in_round if t.status == "answered"]
        field_rows = await self._views.field_values.find(
            {"case_id": command.case_id}, limit=MAX_ROWS_PER_CASE, session=session
        )
        documents = await self._views.documents.find(
            {"case_id": command.case_id},
            sort=[("uploaded_at", ASCENDING)],
            limit=MAX_ROWS_PER_CASE,
            session=session,
        )
        submission = round_submission(
            record=record,
            round_no=thread.round,
            answered=answered,
            field_rows=field_rows,
            documents=documents,
            now=now,
            by=command.actor,
        )
        await self._submissions.insert_one(submission, session=session)
        await self._threads.collection.update_many(
            {"case_id": command.case_id, "round": thread.round, "status": "answered"},
            {"$set": {"resulting_version": submission.version}},
            session=session,
        )
        return submission, case_answered_envelope(
            record, submission, answered, command.actor
        )

    async def _evidence(
        self,
        session: AsyncClientSession,
        command: FollowupCommand,
        events: list[Envelope],
    ) -> bool:
        """Record the evidence; ``False`` when the owner already said exactly it."""
        write = command.evidence
        assert write is not None
        row = await self._views.conditions.find_one(
            {"condition_id": write.condition_id, "case_id": command.case_id},
            session=session,
        )
        if row is None:
            raise NotFoundError(f"no such condition {write.condition_id}")
        changes = evidence_changes(row, write)
        if changes is None:
            return False
        updated = await self._views.conditions.find_one_and_update(
            {"condition_id": write.condition_id, "status": row.status},
            changes,
            session=session,
        )
        if updated is None:
            raise ConflictError(
                f"condition {write.condition_id} changed while it was being updated",
                condition_id=write.condition_id,
            )
        events.append(condition_evidence_envelope(row, write))
        return True

    async def _touch_case(
        self,
        session: AsyncClientSession,
        record: CaseRecord,
        rows: Sequence[UiEventIn],
        changes: dict[str, Any],
    ) -> tuple[ActivityEntry, ...]:
        """Allocate ``len(rows)`` consecutive seqs on the case, apply ``changes``
        and insert the rows, in the one update the activity log relies on.
        """
        if not rows and not changes:
            return ()
        update: dict[str, Any] = {}
        if rows:
            update["$inc"] = {"activity_seq": len(rows)}
        if changes:
            update["$set"] = changes
        bumped = await self._cases.find_one_and_update(
            {"case_id": record.case_id}, update, session=session
        )
        if bumped is None:
            raise NotFoundError(f"no such case {record.case_id}")
        first = bumped.activity_seq - len(rows) + 1
        return tuple(
            [
                await self._activity.stage(session, record.case_id, first + i, event)
                for i, event in enumerate(rows)
            ]
        )

    # -- reads -----------------------------------------------------------------

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

    async def decided_outcome(self, case_id: str) -> Optional[DecidedOutcome]:
        doc = await self._outcomes.find_one(
            {"_id": case_id, "status": "set"},
            projection={"outcome": 1, "plain_summary": 1, "decided_at": 1},
        )
        if doc is None or doc.get("outcome") is None:
            return None
        return DecidedOutcome.model_validate(
            {
                "outcome": doc["outcome"],
                "plain_summary": doc.get("plain_summary"),
                "decided_at": doc.get("decided_at"),
            }
        )

    async def conditions(self, case_id: str) -> list[ConditionRow]:
        docs = await self._views.conditions.collection.find(
            {"case_id": case_id},
            projection=_CONDITION_STAFF_ONLY,
            sort=[("due_date", ASCENDING), ("_id", ASCENDING)],
            limit=MAX_ROWS_PER_CASE,
        ).to_list()
        return [self._views.conditions.from_document(d) for d in docs]

    async def condition(
        self, case_id: str, condition_id: str
    ) -> Optional[ConditionRow]:
        doc = await self._views.conditions.collection.find_one(
            {"condition_id": condition_id, "case_id": case_id},
            projection=_CONDITION_STAFF_ONLY,
        )
        return self._views.conditions.from_document(doc) if doc is not None else None


class InMemoryPoFollowupStore:
    """The test twin: it reads and writes the same in-memory case, view, PO and
    outcome stores the routes use, applies a command in the same order the Mongo
    store does (validating before it changes anything, so a refusal leaves no
    trace) and puts the events it would enqueue on ``cases.events``.

    Args:
        cases: The in-memory case store (records, activity log, event list).
        views: The in-memory view store (field values, documents, threads).
        po: The in-memory PO store (the frozen submissions and the open-question
            counts the owner's list reads).
        outcomes: The in-memory outcome store (the outcome and the conditions).
    """

    def __init__(
        self,
        cases: InMemoryCaseStore,
        views: InMemoryViewStore,
        po: InMemoryPoStore,
        outcomes: InMemoryOutcomeStore,
    ) -> None:
        self._cases = cases
        self._views = views
        self._po = po
        self._outcomes = outcomes

    async def ensure_indexes(self) -> None:
        """Nothing to create."""

    async def commit(self, command: FollowupCommand) -> FollowupCommitted:
        record = self._cases.records.get(command.case_id)
        if record is None:
            raise NotFoundError(f"no such case {command.case_id}")
        if command.evidence is None:
            ensure_open(record)
        now = utcnow()
        events: list[Envelope] = []
        rows: list[UiEventIn] = list(command.activity)
        version: Optional[int] = None
        condition = self._plan_evidence(command)
        if command.evidence is not None and condition is None:
            return FollowupCommitted(record, replayed=True)
        thread = self._plan_answer(command)
        for doc in command.documents:
            if doc.doc_id in self._views.documents:
                raise ConflictError(
                    "a document with that id already exists on this case"
                )
        if condition is not None and command.evidence is not None:
            updated, envelope = condition
            self._outcomes.conditions[updated.condition_id] = updated
            events.append(envelope)
        changes: dict[str, Any] = {}
        if thread is not None:
            version = self._apply_answer(command, record, thread, now, events, rows)
            if version is not None:
                changes["current_submission_v"] = version
        base = record.activity_seq
        updated_record = record.model_copy(
            update={"activity_seq": base + len(rows), **changes}
        )
        self._cases.records[command.case_id] = updated_record
        entries = tuple(
            self._cases.activity.stage(command.case_id, base + i + 1, event)
            for i, event in enumerate(rows)
        )
        self._cases.events.extend(events)
        return FollowupCommitted(record, entries, version)

    def _apply_answer(
        self,
        command: FollowupCommand,
        record: CaseRecord,
        thread: ClarificationThreadRow,
        now: datetime,
        events: list[Envelope],
        rows: list[UiEventIn],
    ) -> Optional[int]:
        """Apply an answer: the thread, the values and documents it brings and,
        when it is the round's last, the frozen next submission. Returns that
        version, or ``None`` while the round stays open.
        """
        assert command.answer is not None
        self._views.threads[thread.thread_id] = thread.model_copy(
            update={"status": "answered", "answer": command.answer.answer}
        )
        for write in command.fields:
            self._write_field(command, write, now)
        for doc in command.documents:
            self._views.documents[doc.doc_id] = doc
        events.extend(command.events)
        closed = self._close_round(record, thread, command, now)
        self._po.open_threads[command.case_id] = len(
            [
                t
                for t in self._views.threads.values()
                if t.case_id == command.case_id and t.status == "awaiting_response"
            ]
        )
        if closed is None:
            return None
        submission, envelope = closed
        events.append(envelope)
        rows.extend(round_rows(command, thread, submission.version))
        return submission.version

    def _plan_answer(
        self, command: FollowupCommand
    ) -> Optional[ClarificationThreadRow]:
        if command.answer is None:
            return None
        thread = self._views.threads.get(command.answer.thread_id)
        if (
            thread is None
            or thread.case_id != command.case_id
            or thread.status != "awaiting_response"
        ):
            raise ThreadNotOpenError(
                "this question is not waiting for an answer",
                case_id=command.case_id,
                thread_id=command.answer.thread_id,
            )
        return thread

    def _plan_evidence(
        self, command: FollowupCommand
    ) -> Optional[tuple[ConditionRow, Envelope]]:
        write: Optional[EvidenceWrite] = command.evidence
        if write is None:
            return None
        row = self._outcomes.conditions.get(write.condition_id)
        if row is None or row.case_id != command.case_id:
            raise NotFoundError(f"no such condition {write.condition_id}")
        changes = evidence_changes(row, write)
        if changes is None:
            return None
        return row.model_copy(update=changes), condition_evidence_envelope(row, write)

    def _write_field(self, command: FollowupCommand, write: Any, now: datetime) -> None:
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

    def _close_round(
        self,
        record: CaseRecord,
        thread: ClarificationThreadRow,
        command: FollowupCommand,
        now: datetime,
    ) -> Optional[tuple[SubmissionVersionRow, Envelope]]:
        in_round = sorted(
            (
                t
                for t in self._views.threads.values()
                if t.case_id == command.case_id and t.round == thread.round
            ),
            key=lambda t: t.question_id,
        )
        if open_threads(in_round):
            return None
        answered = [t for t in in_round if t.status == "answered"]
        submission = round_submission(
            record=record,
            round_no=thread.round,
            answered=answered,
            field_rows=[
                r
                for r in self._views.field_values.values()
                if r.case_id == record.case_id
            ],
            documents=sorted(
                (
                    d
                    for d in self._views.documents.values()
                    if d.case_id == record.case_id
                ),
                key=lambda d: (d.uploaded_at, d.doc_id),
            ),
            now=now,
            by=command.actor,
        )
        self._po.submissions[submission.version_id] = submission
        for row in answered:
            self._views.threads[row.thread_id] = row.model_copy(
                update={"resulting_version": submission.version}
            )
        return submission, case_answered_envelope(
            record, submission, answered, command.actor
        )

    async def threads(self, case_id: str) -> list[ClarificationThreadRow]:
        rows = [t for t in self._views.threads.values() if t.case_id == case_id]
        return sorted(rows, key=lambda t: (t.round, t.question_id))

    async def get_thread(
        self, case_id: str, thread_id: str
    ) -> Optional[ClarificationThreadRow]:
        row = self._views.threads.get(thread_id)
        return row if row is not None and row.case_id == case_id else None

    async def decided_outcome(self, case_id: str) -> Optional[DecidedOutcome]:
        row = self._outcomes.outcomes.get(case_id)
        if row is None or row.status != "set" or row.outcome is None:
            return None
        return DecidedOutcome(
            outcome=row.outcome,
            plain_summary=row.plain_summary,
            decided_at=row.decided_at,
        )

    async def conditions(self, case_id: str) -> list[ConditionRow]:
        rows = [
            _stripped(c)
            for c in self._outcomes.conditions.values()
            if c.case_id == case_id
        ]
        return sorted(rows, key=lambda c: (c.due_date or "", c.condition_id))

    async def condition(
        self, case_id: str, condition_id: str
    ) -> Optional[ConditionRow]:
        row = self._outcomes.conditions.get(condition_id)
        return _stripped(row) if row is not None and row.case_id == case_id else None
