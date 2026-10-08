"""The owner's follow-up writes after submission (scenes 11 and 19) as plain data,
and the pure steps behind them: freezing the next submission version when a round
is answered, the ``case.answered`` event, and the rules for evidence on a
condition.

An answer is applied in ONE transaction by :mod:`fcrm_case.po_followup_store`: the
thread, the values the owner set, the documents they added, the frozen submission
(when their answer closes the round), the activity rows and the outbox events
commit together or not at all. The store decides *inside* the transaction whether
an answer closes the round, so two owners' answers arriving together cannot both
think the other is still open.

Pure, no I/O. Nothing here carries a score, a band or a rating.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Optional

from fcrm_common.errors import ConflictError
from fcrm_common.events import build_envelope
from fcrm_contracts.api.case_internal import UiActor, UiEventIn
from fcrm_contracts.enums import Role
from fcrm_contracts.events.envelope import Envelope
from fcrm_contracts.events.payloads import (
    CaseAnswered,
    CaseEvent,
    CaseQuestionAnswered,
)

from fcrm_case.domain.clarifications import (
    ClarificationThreadRow,
    ThreadAnswerRow,
    added_documents_of,
    changed_fields_of,
)
from fcrm_case.domain.po_commands import FieldWrite
from fcrm_case.domain.po_rows import (
    CapturedBy,
    SnapshotCompletion,
    SubmissionVersionRow,
    snapshot_values,
    submission_version_id,
)
from fcrm_case.domain.record import CaseRecord
from fcrm_case.domain.views import ConditionRow, DocumentRow, FieldValueRow
from fcrm_case.errors import ConditionClosedError

_PO_COMPONENT: Final = "S1"


@dataclass(frozen=True)
class ThreadAnswerWrite:
    """The owner's answer to one question."""

    thread_id: str
    answer: ThreadAnswerRow


@dataclass(frozen=True)
class EvidenceWrite:
    """The evidence note the owner attached to one of their conditions."""

    condition_id: str
    note: str
    by: str
    at: datetime


@dataclass(frozen=True)
class FollowupCommand:
    """Everything one follow-up action of the owner's changes, applied together.

    Attributes:
        case_id: The case.
        actor: The owner's user id.
        answer: The answer to one question, or ``None``.
        fields: Values the owner set with the answer.
        documents: Documents the owner added with it (their
            ``case.document_stored`` events are in ``events``).
        evidence: Evidence on a condition, or ``None``.
        events: Events enqueued on a first application (``case.document_stored``).
        activity: Activity rows, staged in the same transaction.
    """

    case_id: str
    actor: str
    answer: Optional[ThreadAnswerWrite] = None
    fields: Sequence[FieldWrite] = ()
    documents: Sequence[DocumentRow] = ()
    evidence: Optional[EvidenceWrite] = None
    events: Sequence[Envelope] = ()
    activity: Sequence[UiEventIn] = ()


def po_actor(sub: str) -> UiActor:
    """The owner as an activity actor."""
    return UiActor(id=sub, role=Role.PO.value, component=_PO_COMPONENT)


def po_row(
    sub: str,
    type_: str,
    summary: str,
    detail: Optional[dict[str, Any]] = None,
) -> UiEventIn:
    """An activity row the owner sees (visibility ``po``)."""
    return UiEventIn(
        type=type_,
        visibility="po",
        summary=summary,
        detail=detail or {},
        actor=po_actor(sub),
    )


def staff_row(
    sub: str,
    type_: str,
    summary: str,
    detail: Optional[dict[str, Any]] = None,
) -> UiEventIn:
    """An activity row only staff see (visibility ``analyst``), written on the
    owner's behalf.
    """
    return UiEventIn(
        type=type_,
        visibility="analyst",
        summary=summary,
        detail=detail or {},
        actor=po_actor(sub),
    )


def round_submission(
    *,
    record: CaseRecord,
    round_no: int,
    answered: Sequence[ClarificationThreadRow],
    field_rows: Sequence[FieldValueRow],
    documents: Sequence[DocumentRow],
    now: datetime,
    by: str,
) -> SubmissionVersionRow:
    """The next frozen submission, taken when the last question of a round is
    answered: every required field as it stands now, every document the case holds,
    and which fields the round's answers changed.
    """
    version = (record.current_submission_v or 1) + 1
    values = snapshot_values(
        record.required_fields, {r.field_id: r for r in field_rows}
    )
    return SubmissionVersionRow(
        version_id=submission_version_id(record.case_id, version),
        case_id=record.case_id,
        version=version,
        captured_at=now,
        captured_by=CapturedBy(sub=by, role=Role.PO.value),
        reason="round",
        round=round_no,
        change_types=[t.value for t in record.change_types],
        field_values=values,
        document_ids=[d.doc_id for d in documents],
        changed_field_ids=changed_fields_of(answered),
        completion=SnapshotCompletion(
            filled=sum(1 for v in values.values() if v.origin != "open"),
            required=len(values),
        ),
    )


def case_answered_envelope(
    record: CaseRecord,
    submission: SubmissionVersionRow,
    answered: Sequence[ClarificationThreadRow],
    by: str,
) -> Envelope:
    """``case.answered``: the round is answered and submission ``version`` is
    frozen; Caseflow reads the changed fields and the added documents and re-runs
    research and assessment on them.
    """
    payload = CaseAnswered(
        case_id=record.case_id,
        actor=by,
        round=submission.round or 1,
        submission_version=submission.version,
        changed_field_ids=list(submission.changed_field_ids),
        added_document_ids=added_documents_of(answered),
        thread_ids=[t.thread_id for t in answered],
    )
    return build_envelope(
        "case.answered",
        payload.model_dump(mode="json"),
        case_id=record.case_id,
        run_id=record.run_id,
    )


def answer_submission(
    *,
    record: CaseRecord,
    changed_field_ids: Sequence[str],
    field_rows: Sequence[FieldValueRow],
    documents: Sequence[DocumentRow],
    now: datetime,
    by: str,
) -> SubmissionVersionRow:
    """The next frozen submission after an ad-hoc answer changed a value or
    added a document (ADR-73): every required field as it stands now, every
    document the case holds, and which fields the answer changed.
    """
    version = (record.current_submission_v or 1) + 1
    values = snapshot_values(
        record.required_fields, {r.field_id: r for r in field_rows}
    )
    return SubmissionVersionRow(
        version_id=submission_version_id(record.case_id, version),
        case_id=record.case_id,
        version=version,
        captured_at=now,
        captured_by=CapturedBy(sub=by, role=Role.PO.value),
        reason="answer",
        round=None,
        change_types=[t.value for t in record.change_types],
        field_values=values,
        document_ids=[d.doc_id for d in documents],
        changed_field_ids=list(changed_field_ids),
        completion=SnapshotCompletion(
            filled=sum(1 for v in values.values() if v.origin != "open"),
            required=len(values),
        ),
    )


def case_question_answered_envelope(
    *,
    record: CaseRecord,
    thread: ClarificationThreadRow,
    changed_field_ids: Sequence[str],
    added_document_ids: Sequence[str],
    version: Optional[int],
    by: str,
) -> Envelope:
    """``case.question_answered``: the owner answered an ad-hoc question; Caseflow
    records it and, when a value changed, re-runs research and assessment on it
    in review or flags it to the analyst elsewhere (ADR-73).
    """
    payload = CaseQuestionAnswered(
        case_id=record.case_id,
        actor=by,
        thread_id=thread.thread_id,
        question_id=thread.question_id,
        changed_field_ids=list(changed_field_ids),
        added_document_ids=list(added_document_ids),
        submission_version=version,
    )
    return build_envelope(
        "case.question_answered",
        payload.model_dump(mode="json"),
        case_id=record.case_id,
        run_id=record.run_id,
    )


def answer_rows(
    actor: str,
    thread: ClarificationThreadRow,
    changed_labels: Sequence[str],
    version: int,
) -> list[UiEventIn]:
    """The activity rows an ad-hoc answer that froze ``version`` adds: the
    owner's own line and the analyst's, which names the fields that changed.
    """
    what = ", ".join(changed_labels) if changed_labels else "documents added"
    return [
        po_row(
            actor,
            "submission.created",
            f"Your answer is saved as version {version}",
            {"submission_version": version},
        ),
        staff_row(
            actor,
            "submission.created",
            f"The owner's answer to question {thread.question_id} changed {what}: "
            f"submission v{version} saved",
            {
                "thread_id": thread.thread_id,
                "submission_version": version,
            },
        ),
    ]


def condition_evidence_envelope(row: ConditionRow, write: EvidenceWrite) -> Envelope:
    """``condition.updated``: the owner evidenced a condition (its status is now
    ``evidence_submitted``); the same event the analyst's own update publishes.
    """
    return build_envelope(
        "condition.updated",
        CaseEvent(
            case_id=row.case_id,
            actor=write.by,
            data={
                "condition_id": row.condition_id,
                "ref": row.ref,
                "status": "evidence_submitted",
                "at": write.at.isoformat(),
            },
        ).model_dump(mode="json"),
        case_id=row.case_id,
    )


def evidence_changes(
    row: ConditionRow, write: EvidenceWrite
) -> Optional[dict[str, Any]]:
    """The ``$set`` that records ``write`` on ``row``, or ``None`` when the owner
    already said exactly this (a replay).

    Raises:
        ConditionClosedError: The condition is closed.
        ConflictError: The owner already sent different evidence and FCRM has not
            yet reviewed it.
    """
    if row.status == "closed":
        raise ConditionClosedError(
            f"condition {row.condition_id} is closed", condition_id=row.condition_id
        )
    if row.status == "evidence_submitted":
        if row.evidence_note == write.note:
            return None
        raise ConflictError(
            "Evidence was already sent for this condition and FCRM is reviewing it.",
            condition_id=row.condition_id,
        )
    return {
        "status": "evidence_submitted",
        "evidence_note": write.note,
        "evidence_at": write.at,
        "evidence_by": write.by,
        "updated_at": write.at,
        "updated_by": write.by,
    }
