"""Stored rows to the product owner's response models
(:mod:`fcrm_contracts.api.case_po`).

Every function here builds a PO DTO field by field from rows that never held a
score, band, rating or harness identifier, so the shape cannot carry one. (The
fences: the contract's own field names, ``po_guard``'s scan of every response,
and the review items this module reads: only the intake kinds, ``category`` and
``gap``, never a factor, control or critic item.) Pure, no I/O.

Rounding: a percentage is ``filled / required`` rounded half up, as the
server's one figure; the client never recomputes it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import datetime
from typing import Final, Literal, Optional

from fcrm_common.field_match import field_for_question
from fcrm_common.time import add_business_days
from fcrm_contracts.api.case_po import (
    ActivityEventPO,
    AssistantStatus,
    CasePO,
    CasePOListItem,
    CategoryProposalPO,
    Completion,
    DocumentOut,
    DocumentStatus,
    DocumentsViewPO,
    GapQuestionPO,
    IntakeViewPO,
    Integration,
    Origin,
    OriginalValue,
    PrecedentPO,
    RequiredCard,
    ReuseFieldPO,
    ReviewRowPO,
    ReviewViewPO,
    SourceRef,
    SubmitCheck,
    SubmitResult,
    ValueAlternativePO,
    ValuePO,
)
from fcrm_contracts.enums import ChangeType, ItemStatus
from fcrm_contracts.sla import CASE_SLA_BUSINESS_DAYS

from fcrm_case.domain.activity import ActivityEntry
from fcrm_case.domain.completion import CompletionResult, compute_completion
from fcrm_case.domain.fieldset import FieldDef, FieldSetData, resolve_required_fields
from fcrm_case.domain.po_rows import IntegrationDef, IntegrationRow
from fcrm_case.domain.po_stage import (
    CaseSummary,
    has_due_date,
    lifecycle_steps,
    needs_response,
    next_action,
    po_stage,
    stage_label,
)
from fcrm_case.domain.record import CaseRecord
from fcrm_case.domain.submit_checks import SubmitChecks
from fcrm_case.domain.views import (
    DocumentRow,
    FieldOrigin,
    FieldSource,
    FieldValueRow,
    IntakeViewRow,
    ReviewItemRow,
)
from fcrm_case.reference import change_type_labels

#: Working days FCRM aims to take from a submission to a decision-ready case (the
#: case SLA Rules & Routing shows).
TARGET_BUSINESS_DAYS: Final = CASE_SLA_BUSINESS_DAYS

BYTES_PER_MB: Final = 1024 * 1024

_DOC_STATUS: Final[dict[str, DocumentStatus]] = {
    "stored": "stored",
    "extracted": "read",
    "quarantined": "scanned",
    "failed": "stored",
}

_ITEM_STATUS: Final[dict[ItemStatus, str]] = {
    ItemStatus.PROPOSED: "proposed",
    ItemStatus.CONFIRMED: "confirmed",
    ItemStatus.REMOVED: "removed",
}


# -- figures ---------------------------------------------------------------------


def percent(filled: int, required: int) -> int:
    """``filled / required`` as a whole percentage, rounded half up (integer
    arithmetic: no binary-float surprise at ``.5``). No required field is 0.
    """
    if required <= 0:
        return 0
    return (filled * 200 + required) // (2 * required)


def effective_required(record: CaseRecord, field_set: FieldSetData) -> list[FieldDef]:
    """The fields the owner sees as required: the always-required core plus the
    fields of each change type she has confirmed. A type still waiting for a
    decision adds nothing until it is confirmed (its values are kept, only not
    shown), and a removed type is gone.
    """
    return resolve_required_fields([t.value for t in record.change_types], field_set)


def completion_result(
    required: Sequence[FieldDef], filled_ids: frozenset[str] | set[str]
) -> CompletionResult:
    """The one completion calculation over ``required``."""
    return compute_completion(list(required), set(filled_ids))


def completion_dto(
    required: Sequence[FieldDef],
    filled_ids: frozenset[str] | set[str],
    threshold: float,
    field_set: FieldSetData,
) -> Completion:
    """The ``Completion`` the screens draw their ring from."""
    result = completion_result(required, filled_ids)
    always_total = len(field_set.always_required)
    return Completion(
        filled=result.filled_count,
        required=result.required_count,
        pct=percent(result.filled_count, result.required_count),
        threshold_pct=round(threshold * 100),
        always_required_filled=max(
            0, always_total - len(result.missing_always_required)
        ),
        always_required_total=always_total,
    )


# -- sources ---------------------------------------------------------------------


def source_ref(
    source: Optional[FieldSource], docs: Mapping[str, DocumentRow]
) -> Optional[SourceRef]:
    """A value's provenance in the owner's words: a span in a named document
    (``name.pdf p.4 l.12``), or the label the source carries (``reused ·
    FCRM-2026-0118``, ``owner declared``). ``None`` when nothing says where the
    value came from.
    """
    if source is None:
        return None
    document = docs.get(source.doc_id) if source.doc_id is not None else None
    if source.page is not None and source.line_start is not None:
        where = f"p.{source.page} l.{source.line_start}"
        name = document.name if document is not None else None
        return SourceRef(
            label=f"{name} {where}" if name else where,
            document=name,
            doc_id=source.doc_id,
            page=source.page,
            line=source.line_start,
        )
    if source.label:
        return SourceRef(label=source.label)
    return None


# -- cases -----------------------------------------------------------------------


def _date(moment: Optional[datetime]) -> Optional[str]:
    return moment.date().isoformat() if moment is not None else None


def _due(record: CaseRecord, stage: str) -> Optional[str]:
    if record.submitted_at is None or not has_due_date(stage):
        return None
    return add_business_days(record.submitted_at, TARGET_BUSINESS_DAYS).isoformat()


def completion_for(
    record: CaseRecord, summary: CaseSummary, field_set: FieldSetData
) -> Completion:
    """The completion figures of a case from its summary, over the confirmed
    change types only (the core six until one is confirmed).
    """
    required = effective_required(record, field_set)
    return completion_dto(
        required, summary.filled_field_ids, record.completion_threshold, field_set
    )


def list_item(
    record: CaseRecord, summary: CaseSummary, field_set: FieldSetData
) -> CasePOListItem:
    """One row of the owner's queue. A case past intake that holds no stored
    values (it predates this system) has no completion to show.
    """
    stage = po_stage(record)
    completion = completion_for(record, summary, field_set)
    shown = stage == "draft" or summary.has_values
    return CasePOListItem(
        id=record.case_id,
        title=record.title,
        change_types=list(record.change_types),
        stage=stage,
        stage_label=stage_label(stage),
        next_action=next_action(
            stage, _owner_summary(record, summary, field_set), manual=_is_manual(record)
        ),
        analyst_name=record.analyst_name,
        submitted=_date(record.submitted_at),
        completion_pct=completion.pct if shown else None,
        due=_due(record, stage),
        needs_response=needs_response(stage),
    )


def case_po(
    record: CaseRecord, summary: CaseSummary, field_set: FieldSetData
) -> CasePO:
    """The case header and overview figures for its owner."""
    stage = po_stage(record)
    return CasePO(
        id=record.case_id,
        title=record.title,
        unit=record.unit,
        owner_name=record.owner_name or record.owner,
        change_types=list(record.change_types),
        stage=stage,
        stage_label=stage_label(stage),
        lifecycle=lifecycle_steps(stage),
        created=record.created_at.date().isoformat(),
        submitted=_date(record.submitted_at),
        analyst_name=record.analyst_name,
        next_action=next_action(
            stage, _owner_summary(record, summary, field_set), manual=_is_manual(record)
        ),
        completion=completion_for(record, summary, field_set),
        closed=stage == "closed",
        inbox_open=summary.open_threads,
        intake_mode=record.intake_mode,
    )


# -- documents -------------------------------------------------------------------


def guard_line(row: DocumentRow) -> Optional[str]:
    """The one line about what was done to a file before any AI read it. Only
    counts and the verdict, never a matched value.
    """
    if row.status == "failed":
        return "Could not be read. Upload another version."
    guard = row.guard1
    if guard is None:
        return None
    verdict = str(guard.get("status", "clean"))
    masked = guard.get("pii_total", 0)
    if verdict == "quarantined":
        return (
            "Scanned · not read: the file contains instructions the system will "
            "not follow. Upload another version."
        )
    if verdict == "flagged":
        return "Scanned · flagged for the analyst · the text was passed on"
    if isinstance(masked, int) and masked > 0:
        noun = "field" if masked == 1 else "fields"
        return f"Scanned · {masked} personal-data {noun} masked before any AI read it"
    return "Scanned · clean · no personal data found"


def document_out(row: DocumentRow) -> DocumentOut:
    """One uploaded file as the owner sees it."""
    return DocumentOut(
        id=row.doc_id,
        name=row.name,
        pages=row.pages or 0,
        size_mb=round(row.size_bytes / BYTES_PER_MB, 2),
        status=_DOC_STATUS.get(row.status, "stored"),
        guard_line=guard_line(row),
        added_in=(
            f"v{row.raised_in_round + 1}" if row.raised_in_round is not None else None
        ),
        uploaded_at=row.uploaded_at,
    )


def assistant_status(
    docs: Sequence[DocumentRow],
    intake: Optional[IntakeViewRow],
    categories: Sequence[ReviewItemRow],
) -> AssistantStatus:
    """What the reading assistant is doing, as one line the page polls."""
    if not docs:
        return AssistantStatus(
            state="waiting",
            line=(
                "Waiting for documents. Upload what you already have; the "
                "assistant reads it first."
            ),
        )
    unread = [d for d in docs if d.status == "stored"]
    if unread:
        more = f" and {len(unread) - 1} more" if len(unread) > 1 else ""
        return AssistantStatus(state="reading", line=f"Reading {unread[0].name}{more}")
    if intake is None:
        if all(d.status in ("failed", "quarantined") for d in docs):
            return AssistantStatus(
                state="done",
                line="None of your files could be read. Upload another version.",
            )
        return AssistantStatus(
            state="reading", line="Reading your documents and proposing values"
        )
    return AssistantStatus(
        state="done",
        line=(
            f"Done. {intake.candidates_found} values found (including other "
            f"possible readings of the same field), {len(categories)} "
            "change types proposed. Review them on the Overview tab."
        ),
    )


def documents_view(
    docs: Sequence[DocumentRow],
    intake: Optional[IntakeViewRow],
    categories: Sequence[ReviewItemRow],
    completion: Completion,
    *,
    sharepoint_connected: bool,
) -> DocumentsViewPO:
    """The Documents tab."""
    return DocumentsViewPO(
        documents=[document_out(d) for d in docs],
        assistant=assistant_status(docs, intake, categories),
        sharepoint_connected=sharepoint_connected,
        completion=completion,
    )


# -- intake ----------------------------------------------------------------------


def _origin(row: Optional[FieldValueRow]) -> Origin:
    """The origin to show: a stored row with no value is still ``open``."""
    if row is None or row.value is None or not row.value.strip():
        return "open"
    origin: FieldOrigin = row.origin
    return origin


def _alternatives_po(
    row: Optional[FieldValueRow], origin: str, docs: Mapping[str, DocumentRow]
) -> list[ValueAlternativePO]:
    """The other values found for an extracted field, each with its place in a
    document; none once the owner has corrected or answered it."""
    if row is None or origin != "extracted":
        return []
    shown: list[ValueAlternativePO] = []
    for alternative in row.alternatives:
        place = source_ref(alternative.source, docs)
        if place is not None:
            shown.append(ValueAlternativePO(value=alternative.value, source=place))
    return shown


def _value_po(
    definition: FieldDef,
    row: Optional[FieldValueRow],
    docs: Mapping[str, DocumentRow],
) -> ValuePO:
    origin = _origin(row)
    return ValuePO(
        field_id=definition.field_id,
        label=definition.label,
        value=row.value if row is not None and origin != "open" else None,
        origin=origin,
        source=source_ref(row.source, docs)
        if row is not None and origin != "open"
        else None,
        candidates=row.candidates
        if row is not None and origin == "extracted"
        else None,
        alternatives=_alternatives_po(row, origin, docs),
        always_required=definition.always_required,
    )


def _category_po(
    item: ReviewItemRow, docs: Mapping[str, DocumentRow]
) -> CategoryProposalPO:
    spans = item.source_refs
    source = None
    if spans:
        first = spans[0]
        source = source_ref(
            FieldSource(
                doc_id=first.get("doc_id"),
                page=first.get("page"),
                line_start=first.get("line_start"),
                line_end=first.get("line_end"),
            ),
            docs,
        )
    label = str(item.subject.get("label") or item.subject.get("change_type"))
    return CategoryProposalPO(
        type=ChangeType(str(item.subject["change_type"])),
        label=label,
        status=_ITEM_STATUS.get(item.status, "proposed"),  # type: ignore[arg-type]
        source=source,
        reason=item.decided.reason
        if item.status == ItemStatus.REMOVED and item.decided
        else None,
        origin="assistant",
    )


def _owner_type_po(change_type: ChangeType) -> CategoryProposalPO:
    """A change type the owner put on the case herself (ADR-75): confirmed by
    her, with no source in a document.
    """
    return CategoryProposalPO(
        type=change_type,
        label=change_type_labels().get(change_type.value, change_type.value),
        status="confirmed",
        source=None,
        reason=None,
        origin="owner",
    )


def change_types_po(
    record: CaseRecord,
    categories: Sequence[ReviewItemRow],
    docs: Mapping[str, DocumentRow],
) -> list[CategoryProposalPO]:
    """Every change type the Overview lists, each once: the assistant's
    proposals (whatever the owner decided), then the types the owner added
    that the assistant never proposed, in the order she added them.
    """
    proposed = [_category_po(c, docs) for c in categories]
    seen = {c.type for c in proposed}
    owned = [_owner_type_po(t) for t in record.change_types if t not in seen]
    return [*proposed, *owned]


def _is_manual(record: CaseRecord) -> bool:
    return record.intake_mode == "manual"


def gap_field(item: ReviewItemRow, labels: Mapping[str, str]) -> Optional[str]:
    """The field a gap question asks about: the one it names, else the one its
    text asks for (``labels``: field id -> label), else ``None`` (D-128)."""
    named = item.subject.get("field_id")
    if named:
        return str(named)
    return field_for_question(str(item.subject.get("text") or ""), labels)


def _gap_po(
    item: ReviewItemRow,
    rows: Mapping[str, FieldValueRow],
    labels: Optional[Mapping[str, str]] = None,
) -> GapQuestionPO:
    subject = item.subject
    question_id = str(subject.get("question_id", item.item_id))
    resolved = gap_field(item, labels or {})
    field_id = resolved or question_id
    label = (
        str(subject.get("label") or field_id)
        if subject.get("field_id") or resolved is None
        else (labels or {}).get(resolved, resolved)
    )
    answered = item.status != ItemStatus.PROPOSED
    row = rows.get(field_id)
    answer = row.value if answered and row is not None else None
    if answered and answer is None and item.decided is not None:
        raw = (item.decided.value or {}).get("value")
        answer = str(raw) if raw is not None else None
    return GapQuestionPO(
        id=question_id,
        field_id=field_id,
        label=label,
        question=str(subject.get("text") or ""),
        blocking=bool(subject.get("blocking")),
        answered=answered,
        value=answer,
    )


#: The id prefix of a question the Overview asks for a required field the
#: assistant asked nothing about (``ask_F11``); its answer fills the field.
FIELD_QUESTION_PREFIX: Final = "ask_"


def field_question_id(field_id: str) -> str:
    """The id of the question asked for ``field_id`` when no gap names it."""
    return f"{FIELD_QUESTION_PREFIX}{field_id}"


def field_of_question(question_id: str) -> Optional[str]:
    """The field a :func:`field_question_id` question fills, else ``None``."""
    if not question_id.startswith(FIELD_QUESTION_PREFIX):
        return None
    return question_id.removeprefix(FIELD_QUESTION_PREFIX) or None


def _plain_question(label: str) -> str:
    """A question built from a field's label (the field set carries no
    question text): ``Go-live date`` reads ``Please provide the go-live date.``
    A label that opens with an acronym keeps its case.
    """
    if len(label) > 1 and label[1].islower():
        label = label[0].lower() + label[1:]
    return f"Please provide the {label}."


def _field_question_po(
    definition: FieldDef, row: Optional[FieldValueRow]
) -> GapQuestionPO:
    answered = _origin(row) != "open"
    return GapQuestionPO(
        id=field_question_id(definition.field_id),
        field_id=definition.field_id,
        label=definition.label,
        question=_plain_question(definition.label),
        blocking=definition.always_required,
        answered=answered,
        value=row.value if answered and row is not None else None,
    )


def _answered_here(row: Optional[FieldValueRow], field_id: str) -> bool:
    """Whether ``row`` holds the owner's answer to the field's own question."""
    return (
        _origin(row) == "answered"
        and row is not None
        and row.source is not None
        and row.source.question_id == field_question_id(field_id)
    )


def questions_po(
    required: Sequence[FieldDef],
    gaps: Sequence[ReviewItemRow],
    rows: Mapping[str, FieldValueRow],
) -> list[GapQuestionPO]:
    """The questions for the owner, over the confirmed required set: each
    required field with no value, asked by the assistant's question when it
    wrote one (its own blocking flag) or by a plain one built from the label
    (blocking for an always-required field), and every question already
    answered. A question on a field of a type not confirmed is not shown (a
    pending type's returns when it is confirmed), nor an unanswered one whose
    field was filled another way. A question that names no field is tied to the
    field it asks about (:func:`gap_field`), and a field is asked once: by the
    question already answered, else the one that named it, else the first
    (D-128). A question tied to no field always shows.
    """
    labels = {d.field_id: d.label for d in required}
    fields = {gap.item_id: gap_field(gap, labels) for gap in gaps}
    chosen: dict[str, ReviewItemRow] = {}
    for gap in gaps:
        field_id = fields[gap.item_id]
        if field_id and (
            field_id not in chosen or _gap_rank(gap) < _gap_rank(chosen[field_id])
        ):
            chosen[field_id] = gap
    asked: set[str] = set()
    shown: list[GapQuestionPO] = []
    for gap in gaps:
        field_id = fields[gap.item_id] or ""
        if field_id:
            asked.add(field_id)
            if chosen[field_id] is not gap or field_id not in labels:
                continue
            if gap.status == ItemStatus.PROPOSED and _origin(rows.get(field_id)) != (
                "open"
            ):
                continue
        shown.append(_gap_po(gap, rows, labels))
    for definition in required:
        if definition.field_id in asked:
            continue
        row = rows.get(definition.field_id)
        if _origin(row) == "open" or _answered_here(row, definition.field_id):
            shown.append(_field_question_po(definition, row))
    return shown


def _gap_rank(gap: ReviewItemRow) -> tuple[bool, bool, str]:
    """Which of several questions on one field the owner sees: an answered one
    first, then one that named its field, then the smallest question id."""
    return (
        gap.status == ItemStatus.PROPOSED,
        not gap.subject.get("field_id"),
        str(gap.subject.get("question_id", gap.item_id)),
    )


def blocking_questions_open(
    record: CaseRecord, summary: CaseSummary, field_set: FieldSetData
) -> int:
    """How many blocking questions the Overview shows unanswered, from a case
    summary (the same rule as :func:`questions_po`).
    """
    blocking = {f: b for f, b in summary.open_gaps if f}
    unfielded = sum(1 for f, b in summary.open_gaps if not f and b)
    return unfielded + sum(
        1
        for d in effective_required(record, field_set)
        if d.field_id not in summary.filled_field_ids
        and blocking.get(d.field_id, d.always_required)
    )


def _owner_summary(
    record: CaseRecord, summary: CaseSummary, field_set: FieldSetData
) -> CaseSummary:
    """``summary`` with its blocking-question count taken over the confirmed
    required set, once the assistant has read the documents.
    """
    if not summary.intake_ready:
        return summary
    return replace(
        summary,
        blocking_gaps_open=blocking_questions_open(record, summary, field_set),
    )


def _precedent_po(
    intake: IntakeViewRow, rows: Sequence[FieldValueRow]
) -> Optional[PrecedentPO]:
    raw = intake.precedent
    if raw is None:
        return None
    case_id = str(raw["case_id"])
    reused = any(
        r.origin == "reused"
        and r.source is not None
        and r.source.from_case_id == case_id
        for r in rows
    )
    return PrecedentPO(
        case_id=case_id,
        title=str(raw["title"]),
        unit=str(raw.get("unit") or ""),
        closed_month=str(raw["closed_month"]),
        fields=[
            ReuseFieldPO(
                field_id=str(f["field_id"]),
                label=str(f["label"]),
                value=str(f.get("value") or ""),
                reusable=bool(f["reusable"]),
                reason=f.get("reason"),
            )
            for f in raw.get("fields", [])
        ],
        reused=reused,
    )


def intake_view(
    record: CaseRecord,
    field_set: FieldSetData,
    *,
    rows: Sequence[FieldValueRow],
    categories: Sequence[ReviewItemRow],
    gaps: Sequence[ReviewItemRow],
    docs: Sequence[DocumentRow],
    intake: Optional[IntakeViewRow],
) -> IntakeViewPO:
    """The Overview tab: the proposed change types, every required value with
    its source, the precedent and the questions. Nothing is shown before the
    assistant has pushed its proposals (``ready``). A proposed type's values
    join the list when the owner confirms it.
    """
    by_field = {r.field_id: r for r in rows}
    by_doc = {d.doc_id: d for d in docs}
    pending = [
        str(c.subject["change_type"])
        for c in categories
        if c.status == ItemStatus.PROPOSED and "change_type" in c.subject
    ]
    required = effective_required(record, field_set)
    filled = {fid for fid, r in by_field.items() if _origin(r) != "open"}
    completion = completion_dto(
        required, filled, record.completion_threshold, field_set
    )
    # A case the owner typed up herself (the manual path, or a type she added
    # before the assistant read anything) is ready as soon as it holds a type.
    ready = intake is not None or bool(record.change_types)
    # The assistant's questions follow the confirmed types once it has read
    # the documents; a case typed up by hand has only its Values list.
    if intake is not None:
        questions = questions_po(required, gaps, by_field)
    else:
        questions = [_gap_po(g, by_field) for g in gaps] if ready else []
    return IntakeViewPO(
        ready=ready,
        change_types=change_types_po(record, categories, by_doc) if ready else [],
        values=(
            [_value_po(d, by_field.get(d.field_id), by_doc) for d in required]
            if ready
            else []
        ),
        precedent=_precedent_po(intake, rows) if intake is not None else None,
        gaps=questions,
        completion=completion,
        required_fields=(
            len(record.required_fields) if ready and not pending else None
        ),
        field_set_version=f"v{record.fieldset_v}",
        candidates_found=intake.candidates_found if intake is not None else 0,
    )


# -- review and submit -----------------------------------------------------------


def _review_row(
    definition: FieldDef,
    row: Optional[FieldValueRow],
    docs: Mapping[str, DocumentRow],
) -> ReviewRowPO:
    origin = _origin(row)
    original = None
    if row is not None and row.history and row.history[0].value is not None:
        first = row.history[0]
        original = OriginalValue(
            value=first.value or "", source=source_ref(first.source, docs)
        )
    return ReviewRowPO(
        field_id=definition.field_id,
        label=definition.label,
        value=row.value if row is not None and origin != "open" else None,
        origin=origin,
        source=source_ref(row.source, docs)
        if row is not None and origin != "open"
        else None,
        original=original if origin != "open" else None,
        alternatives=_alternatives_po(row, origin, docs),
        always_required=definition.always_required,
    )


def blocked_reason(completion: Completion) -> Optional[str]:
    """Why the owner cannot submit yet, from the completion alone (the
    submit checks give the full list).
    """
    if completion.pct >= completion.threshold_pct:
        return None
    needed = max(
        1,
        -(-completion.threshold_pct * completion.required // 100) - completion.filled,
    )
    return (
        f"Completion {completion.pct}% is below {completion.threshold_pct}%: "
        f"{needed} more field(s) needed."
    )


def review_view(
    record: CaseRecord,
    field_set: FieldSetData,
    *,
    rows: Sequence[FieldValueRow],
    docs: Sequence[DocumentRow],
) -> ReviewViewPO:
    """The Review & submit tab: every required value, its origin and the
    original of a corrected one, and the card that says what is required
    (the confirmed change types' fields only).
    """
    by_field = {r.field_id: r for r in rows}
    by_doc = {d.doc_id: d for d in docs}
    required = effective_required(record, field_set)
    filled = {fid for fid, r in by_field.items() if _origin(r) != "open"}
    completion = completion_dto(
        required, filled, record.completion_threshold, field_set
    )
    return ReviewViewPO(
        rows=[_review_row(d, by_field.get(d.field_id), by_doc) for d in required],
        required_card=RequiredCard(
            field_set_version=f"v{record.fieldset_v}",
            required=len(required),
            always_required=len(field_set.always_required),
            threshold_pct=completion.threshold_pct,
            labels=[f"{d.field_id} {d.label}".strip() for d in required],
        ),
        completion=completion,
        submitted=record.submitted_at is not None,
        submit_blocked_reason=blocked_reason(completion),
    )


def submit_result(
    checks: SubmitChecks,
    *,
    submission_version: Optional[int] = None,
    message: Optional[str] = None,
) -> SubmitResult:
    """The four checks as the owner sees them."""
    return SubmitResult(
        ok=checks.can_submit,
        checks=[
            SubmitCheck(
                id=c.id, label=c.label, ok=c.ok, blocking=c.blocking, detail=c.detail
            )
            for c in checks.checks
        ],
        submission_version=submission_version,
        message=message,
    )


# -- activity, integrations ------------------------------------------------------

_AI_TYPES: Final = ("document.", "intake.")
_FCRM_TYPES: Final = ("clarification.", "outcome.", "condition.", "thread.")


def activity_event(entry: ActivityEntry, owner: str) -> ActivityEventPO:
    """One activity row as the owner reads it: who did it (you, the assistant,
    FCRM, or the system) and the plain sentence. Never the actor's id, the
    component or the audit sequence.
    """
    actor: Literal["You", "FCRM", "System", "AI"]
    if entry.actor.id == owner or entry.actor.role == "fcrm_po":
        actor = "You"
    elif entry.type.startswith(_AI_TYPES):
        actor = "AI"
    elif entry.type.startswith(_FCRM_TYPES) or entry.actor.role == "fcrm_analyst":
        actor = "FCRM"
    else:
        actor = "System"
    return ActivityEventPO(
        id=f"ev_{entry.seq}", at=entry.at, actor=actor, text=entry.summary
    )


def integration_dto(
    definition: IntegrationDef, row: Optional[IntegrationRow]
) -> Integration:
    """An integration with this owner's choice (the catalogue default when they
    made none).
    """
    status = row.status if row is not None else definition.default_status
    scope = row.scope if row is not None else definition.default_scope
    return Integration(
        id=definition.id,
        name=definition.name,
        kind=definition.kind,
        status=status,
        description=definition.description,
        scope=scope,
        disconnect_consequence=definition.disconnect_consequence,
    )
