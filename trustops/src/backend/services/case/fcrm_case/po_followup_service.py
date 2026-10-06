"""The product owner's follow-up use cases after submission (scenes 11 and 19):
read the questions FCRM asked and answer them, read the plain outcome, read their
conditions and evidence their own.

Each write resolves the owner's case, decides what the action does and hands the
store ONE :class:`~fcrm_case.domain.po_followup.FollowupCommand`; the store commits
the thread, the values and documents the answer brings, the frozen next submission
(when the answer closes the round), the activity rows and the outbox events
together. Rows are published to the case stream only after that commit.

Rules this module holds:

* **Ownership.** Another owner's case is a 404 on a read (it does not exist to
  you) and a 403 on a write; a discarded draft reads as missing.
* **An answer closes a question once.** The same answer again replays; a different
  one is a 409 (the question is no longer open). The answer that closes the round
  creates the next submission (v2) in the same transaction as the answer and tells
  Caseflow (``case.answered``) which fields changed and which documents were
  added; Caseflow re-reads and re-runs on those.
* **The owner's words are checked, the analyst's are not repeated.** What the
  owner types may not name a reserved term (their own page would be refused by the
  response guard); what staff wrote shows only when it is safe to.
* **Closed is closed, except for conditions.** An answer to a closed case is a
  409 ``case_closed``; evidence on a condition is still taken (rule 5).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Optional

from fcrm_common.errors import ForbiddenError, NotFoundError, ValidationError
from fcrm_common.time import utcnow
from fcrm_contracts.api.case_internal import UiEventIn
from fcrm_contracts.api.case_po import ConditionPO, InboxPO, OutcomePO
from fcrm_contracts.api.case_po_followup import (
    ConditionEvidenceBody,
    ThreadAnswerBody,
)
from fcrm_contracts.common import Page, Principal
from fcrm_contracts.events.envelope import Envelope

from fcrm_case.domain.clarifications import ClarificationThreadRow, ThreadAnswerRow
from fcrm_case.domain.po_commands import FieldWrite
from fcrm_case.domain.po_followup import (
    EvidenceWrite,
    FollowupCommand,
    ThreadAnswerWrite,
    po_row,
    staff_row,
)
from fcrm_case.domain.record import CaseRecord
from fcrm_case.domain.views import ConditionRow, DocumentRow, FieldSource
from fcrm_case.errors import ensure_open
from fcrm_case.po_followup_store import (
    FollowupCommitted,
    PoFollowupStore,
    ThreadNotOpenError,
)
from fcrm_case.po_followup_views import (
    condition_po,
    inbox_view,
    outcome_view,
)
from fcrm_case.po_text import clean, reject_reserved_terms
from fcrm_case.po_uploads import DocumentIntake
from fcrm_case.sse import ActivityWriter
from fcrm_case.store import CaseStore
from fcrm_case.views_store import ViewStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Added:
    """The documents an answer stored: the rows, their ``case.document_stored``
    events and the activity rows that say so.
    """

    rows: list[DocumentRow]
    events: list[Envelope]
    activity: list[UiEventIn]


class PoFollowupService:
    """The owner's follow-up operations over S1's stores.

    Args:
        cases: The case store (the case document).
        views: The view store (documents).
        store: The follow-up store (atomic writes, the questions, the outcome).
        writer: Publishes committed activity rows to the case stream.
        intake: Validates and stores the documents an answer adds.
    """

    def __init__(
        self,
        *,
        cases: CaseStore,
        views: ViewStore,
        store: PoFollowupStore,
        writer: ActivityWriter,
        intake: DocumentIntake,
    ) -> None:
        self._cases = cases
        self._views = views
        self._store = store
        self._writer = writer
        self._intake = intake

    # -- plumbing ----------------------------------------------------------------

    async def _owned(
        self, case_id: str, principal: Principal, *, write: bool
    ) -> CaseRecord:
        record = await self._cases.get(case_id, with_versions=False)
        if record is None or record.discarded_at is not None:
            raise NotFoundError(f"no such case {case_id}")
        if record.owner != principal.sub:
            if write:
                raise ForbiddenError("Only the owner of a case may change it.")
            raise NotFoundError(f"no such case {case_id}")
        return record

    def _publish(self, committed: FollowupCommitted) -> None:
        for entry in committed.entries:
            self._writer.publish(entry)

    # -- the inbox (scene 11) ----------------------------------------------------

    async def inbox(self, principal: Principal, case_id: str) -> InboxPO:
        """The questions FCRM asked, by round, with the owner's answers."""
        record = await self._owned(case_id, principal, write=False)
        return inbox_view(record, await self._store.threads(case_id))

    async def answer(
        self,
        principal: Principal,
        case_id: str,
        thread_id: str,
        body: ThreadAnswerBody,
    ) -> InboxPO:
        """Answer one question. The answer, the values and documents it brings and,
        when it is the round's last, submission v2 and ``case.answered`` are stored
        together.

        Raises:
            NotFoundError: No such question on the case.
            ThreadNotOpenError: The question was answered differently, or resolved.
            fcrm_case.errors.CaseClosedError: The case is closed.
            ValidationError: A word is reserved, a field is not the case's, or a
                document is not one the case may take.
        """
        record = await self._owned(case_id, principal, write=True)
        ensure_open(record)
        thread = await self._store.get_thread(case_id, thread_id)
        if thread is None:
            raise NotFoundError(f"no such question {thread_id}")
        text = clean(body.text)
        reject_reserved_terms(text, "Your answer", loc="body.text")
        if thread.status != "awaiting_response":
            if self._same_answer(thread, text, body):
                return await self.inbox(principal, case_id)
            raise ThreadNotOpenError(
                "this question is not waiting for an answer",
                case_id=case_id,
                thread_id=thread_id,
            )
        writes = self._field_writes(record, thread, body)
        existing = await self._views.list_documents(case_id)
        added = await self._documents(record, thread, body, existing, principal)
        now = utcnow()
        answer = ThreadAnswerRow(
            text=text,
            documents=list(dict.fromkeys(clean(n) for n in body.documents)),
            document_ids=[row.doc_id for row in added.rows],
            field_ids=[w.field_id for w in writes],
            at=now,
            by=principal.sub,
        )
        committed = await self._store.commit(
            FollowupCommand(
                case_id=case_id,
                actor=principal.sub,
                answer=ThreadAnswerWrite(thread_id, answer),
                fields=writes,
                documents=added.rows,
                events=added.events,
                activity=[
                    po_row(
                        principal.sub,
                        "thread.answered",
                        "You answered a question from FCRM",
                        {"round": thread.round},
                    ),
                    staff_row(
                        principal.sub,
                        "thread.answered",
                        f"The owner answered question {thread.question_id} "
                        f"(round {thread.round})",
                        {"round": thread.round, "thread_id": thread_id},
                    ),
                    *added.activity,
                ],
            )
        )
        self._publish(committed)
        logger.info(
            "question answered",
            extra={
                "case_id": case_id,
                "thread_id": thread_id,
                "round": thread.round,
                "documents": len(added.rows),
                "submission_version": committed.submission_version,
            },
        )
        return await self.inbox(principal, case_id)

    @staticmethod
    def _same_answer(
        thread: ClarificationThreadRow, text: str, body: ThreadAnswerBody
    ) -> bool:
        """Whether ``body`` says again what the thread already holds."""
        held = thread.answer
        if held is None:
            return False
        names = {clean(n).lower() for n in body.documents}
        return held.text == text and {d.lower() for d in held.documents} == names

    @staticmethod
    def _field_writes(
        record: CaseRecord, thread: ClarificationThreadRow, body: ThreadAnswerBody
    ) -> list[FieldWrite]:
        """The values the owner sets with the answer: each must be a field of the
        case, and none may name a reserved term.
        """
        labels = {d.field_id: d.label for d in record.required_fields}
        writes: list[FieldWrite] = []
        for item in body.field_values:
            if item.field_id not in labels:
                raise ValidationError(
                    f"{item.field_id} is not a field of case {record.case_id}.",
                    errors=[{"loc": "body.field_values", "msg": "not a field"}],
                )
            reject_reserved_terms(item.value, "Your answer", loc="body.field_values")
            writes.append(
                FieldWrite(
                    item.field_id,
                    labels[item.field_id],
                    item.value,
                    "answered",
                    FieldSource(question_id=thread.question_id, label="owner answer"),
                )
            )
        return writes

    async def _documents(
        self,
        record: CaseRecord,
        thread: ClarificationThreadRow,
        body: ThreadAnswerBody,
        existing: Sequence[DocumentRow],
        principal: Principal,
    ) -> _Added:
        """Store the documents the answer names that the case does not hold yet
        (the same checks as any upload); one the case already holds is a
        reference, not a second copy.
        """
        held = {d.name.lower() for d in existing}
        fresh = [n for n in dict.fromkeys(clean(n) for n in body.documents) if n]
        fresh = [n for n in fresh if n.lower() not in held]
        if not fresh:
            return _Added([], [], [])
        files = await self._intake.demo_files(fresh, existing)
        stored = await self._intake.store(
            record,
            existing,
            files,
            actor=principal.sub,
            now=utcnow(),
            raised_in_round=thread.round,
        )
        return _Added(stored.rows, stored.events, [d.event for d in stored.activity])

    # -- the outcome and the conditions (scene 19) -------------------------------

    async def outcome(self, principal: Principal, case_id: str) -> OutcomePO:
        """The plain outcome: the label, the owner-facing summary, the date and the
        conditions in words. No score, band, rating, vote or rationale.
        """
        await self._owned(case_id, principal, write=False)
        decided = await self._store.decided_outcome(case_id)
        conditions = await self._store.conditions(case_id) if decided else []
        return outcome_view(decided, conditions, principal.sub)

    async def conditions_page(
        self, principal: Principal, case_id: str
    ) -> Page[ConditionPO]:
        """The owner's conditions in words (the owner's branch of ``GET
        conditions``).
        """
        await self._owned(case_id, principal, write=False)
        rows = await self._store.conditions(case_id)
        items = [condition_po(r, principal.sub) for r in rows]
        return Page[ConditionPO](items=items, next_cursor=None, total=len(items))

    async def evidence(
        self,
        principal: Principal,
        case_id: str,
        condition_id: str,
        body: ConditionEvidenceBody,
    ) -> ConditionPO:
        """Attach evidence to one of the owner's own conditions: its status becomes
        ``evidence_submitted`` and FCRM reviews it. Allowed on a closed case (rule 5);
        the same evidence again replays.

        Raises:
            NotFoundError: No such condition on the case.
            ForbiddenError: The condition is not the owner's.
            fcrm_case.errors.ConditionClosedError: The condition is closed.
        """
        await self._owned(case_id, principal, write=True)
        row = await self._store.condition(case_id, condition_id)
        if row is None:
            raise NotFoundError(f"no such condition {condition_id}")
        if row.owner_id != principal.sub:
            raise ForbiddenError("You can evidence only your own conditions.")
        note = clean(body.note)
        reject_reserved_terms(note, "Your note", loc="body.note")
        label = row.ref or row.condition_id
        now = utcnow()
        committed = await self._store.commit(
            FollowupCommand(
                case_id=case_id,
                actor=principal.sub,
                evidence=EvidenceWrite(condition_id, note, principal.sub, now),
                activity=[
                    po_row(
                        principal.sub,
                        "condition.updated",
                        "You sent evidence for a condition",
                    ),
                    staff_row(
                        principal.sub,
                        "condition.updated",
                        f"The owner sent evidence for condition {label}",
                        {"condition_id": condition_id},
                    ),
                ],
            )
        )
        self._publish(committed)
        fresh: Optional[ConditionRow] = await self._store.condition(
            case_id, condition_id
        )
        return condition_po(fresh or row, principal.sub)
