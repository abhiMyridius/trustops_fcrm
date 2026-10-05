"""Maps the stored :class:`~fcrm_case.domain.record.CaseRecord` onto the
analyst and internal wire contracts (``fcrm_contracts.api.case_analyst`` and
``case_internal``).

The product owner's shapes are not here: they are built field by field from
stored rows in :mod:`fcrm_case.po_views`, so the PO never passes through a shape
that has a slot for a score. What remains is what the analyst lane replaces with
the workbench, and H1's record.
"""

from __future__ import annotations

from collections.abc import Sequence

from fcrm_contracts.api.case_analyst import (
    CaseDetailAnalyst,
    CaseListItemAnalyst,
    SubmissionVersion,
    SubmissionVersionDiff,
)
from fcrm_contracts.api.case_internal import (
    CaseDetailInternal,
    CaseRecordInternal,
    DocumentInternal,
    FieldValueInternal,
    ItemDecisionInternal,
    OwnerRef,
    ReviewItemInternal,
)
from fcrm_contracts.api.case_shared import CompletionState, FieldValuePO

from fcrm_case.domain.completion import compute_completion
from fcrm_case.domain.diffing import diff_submission_versions
from fcrm_case.domain.record import CaseRecord
from fcrm_case.domain.views import (
    DocumentRow,
    FieldOrigin,
    FieldValueRow,
    ReviewItemRow,
)


def completion_state(record: CaseRecord) -> CompletionState:
    """The one completion computation (``compute_completion``), mapped to
    the wire shape every role's detail response carries.
    """
    result = compute_completion(record.required_fields, record.filled_field_ids)
    return CompletionState(
        required_count=result.required_count,
        filled_count=result.filled_count,
        pct=result.pct,
        threshold_pct=record.completion_threshold,
        missing_always_required=result.missing_always_required,
        all_always_required_filled=result.all_always_required_filled,
    )


def _fields_po(record: CaseRecord) -> list[FieldValuePO]:
    """One ``FieldValuePO`` per required field, in the case's pinned
    field-set order, whether or not it has been filled yet.
    """
    fields = []
    for field_def in record.required_fields:
        stored = record.fields.get(field_def.field_id)
        fields.append(
            FieldValuePO(
                field_id=field_def.field_id,
                label=field_def.label,
                value=stored.value if stored else None,
                source=stored.source if stored else None,
                how=stored.how if stored else None,
            )
        )
    return fields


def _submission_versions(record: CaseRecord) -> list[SubmissionVersion]:
    """Each stored snapshot, diffed against the one immediately before it
    (version 1's diff is against an empty snapshot, i.e. every field it
    set). Diffing two arbitrary versions is ``GET .../versions/diff``,
    not this.
    """
    versions = record.submission_versions
    result = []
    for i, version in enumerate(versions):
        if i == 0:
            diff = [
                SubmissionVersionDiff(field_id=field_id, before=None, after=after)
                for field_id, after in version.field_values.items()
                if after is not None
            ]
        else:
            diff = diff_submission_versions(
                versions, versions[i - 1].version, version.version
            )
        result.append(
            SubmissionVersion(
                version=version.version,
                created_at=version.created_at,
                created_by=version.created_by,
                diff=diff,
            )
        )
    return result


def to_case_detail_analyst(record: CaseRecord) -> CaseDetailAnalyst:
    """The analyst/committee-visible case detail: every PO field, plus the
    assignment and (once wired to S2/H1's own stores) the score and review
    items. Neither this service nor this pass own scoring or review-item
    storage, so both stay ``None``/empty here -- not a leak, an honest gap
    (see the task report's "what remains unverified").
    """
    return CaseDetailAnalyst(
        case_id=record.case_id,
        title=record.title,
        state=record.state,
        unit=record.unit,
        owner=record.owner,
        analyst_id=record.analyst_id,
        change_types=record.change_types,
        completion=completion_state(record),
        fields=_fields_po(record),
        documents=[],
        clarifications=[],
        conditions=[],
        score=None,
        review_items=[],
        submission_versions=_submission_versions(record),
        outcome_summary=record.outcome_summary,
        created_at=record.created_at,
        submitted_at=record.submitted_at,
        decided_at=record.decided_at,
    )


def to_case_detail_internal(record: CaseRecord) -> CaseDetailInternal:
    """What H1 and other services read over a service-account bearer
    token: the analyst shape plus the pinned versions this case runs
    against.
    """
    analyst = to_case_detail_analyst(record)
    return CaseDetailInternal(
        **analyst.model_dump(),
        fieldset_v=record.fieldset_v,
        pinned_versions=record.pinned_versions,
    )


def to_case_list_item_analyst(record: CaseRecord) -> CaseListItemAnalyst:
    """The analyst/committee queue row."""
    return CaseListItemAnalyst(
        case_id=record.case_id,
        title=record.title,
        state=record.state,
        change_types=record.change_types,
        analyst_id=record.analyst_id,
        created_at=record.created_at,
        submitted_at=record.submitted_at,
        open_clarification_count=0,
    )


#: The ``how`` of a legacy embedded field (``CaseRecord.fields``) as an origin.
_LEGACY_ORIGINS: dict[str, FieldOrigin] = {
    "extracted": "extracted",
    "reused": "reused",
    "answered": "answered",
    "owner_corrected": "owner_declared",
}


def to_document_internal(row: DocumentRow) -> DocumentInternal:
    """A stored document as H1 and the document-reading tool see it."""
    return DocumentInternal(
        doc_id=row.doc_id,
        case_id=row.case_id,
        name=row.name,
        status=row.status,
        bucket=row.bucket,
        key=row.key,
        size_bytes=row.size_bytes,
        content_type=row.content_type,
        uploaded_at=row.uploaded_at,
        raised_in_round=row.raised_in_round,
        canonical_key=row.canonical_key,
        anchored_key=row.anchored_key,
        pages=row.pages,
        guard1=row.guard1,
    )


def to_review_item_internal(row: ReviewItemRow) -> ReviewItemInternal:
    """A stored review item as H1 reads it back."""
    decided = (
        ItemDecisionInternal(
            value=row.decided.value,
            reason=row.decided.reason,
            by=row.decided.by,
            at=row.decided.at,
        )
        if row.decided is not None
        else None
    )
    return ReviewItemInternal(
        item_id=row.item_id,
        kind=row.kind,
        status=row.status,
        proposal_id=row.proposal_id,
        gate_id=row.gate_id,
        subject=row.subject,
        proposed=row.proposed,
        source_refs=row.source_refs,
        input_fields=row.input_fields,
        supersedes=row.supersedes,
        run_id=row.run_id,
        decided=decided,
    )


def _field_internal(row: FieldValueRow) -> FieldValueInternal:
    """A stored field value: the original (what it held before a person
    changed it) is the oldest history entry.
    """
    return FieldValueInternal(
        field_id=row.field_id,
        label=row.label,
        value=row.value,
        origin=row.origin,
        source=row.source.as_doc_ref() if row.source is not None else None,
        original_value=row.history[0].value if row.history else None,
    )


def _legacy_field_internal(record: CaseRecord, field_id: str) -> FieldValueInternal:
    """A value written through the legacy ``PUT /cases/{id}/fields/{id}`` route
    (embedded in the case document) as an internal field.
    """
    stored = record.fields[field_id]
    return FieldValueInternal(
        field_id=field_id,
        label=stored.label,
        value=stored.value,
        origin=_LEGACY_ORIGINS.get(stored.how, "owner_declared"),
    )


def _internal_fields(
    record: CaseRecord, rows: Sequence[FieldValueRow]
) -> list[FieldValueInternal]:
    """Every required field in the case's pinned order (a stored value, a legacy
    embedded one, or an ``open`` placeholder), then any stored value for a field
    that is not (yet) required, by id.
    """
    stored = {row.field_id: row for row in rows}
    fields: list[FieldValueInternal] = []
    for definition in record.required_fields:
        row = stored.pop(definition.field_id, None)
        if row is not None:
            fields.append(_field_internal(row))
        elif definition.field_id in record.filled_field_ids:
            fields.append(_legacy_field_internal(record, definition.field_id))
        else:
            fields.append(
                FieldValueInternal(
                    field_id=definition.field_id,
                    label=definition.label,
                    origin="open",
                )
            )
    fields.extend(_field_internal(stored[field_id]) for field_id in sorted(stored))
    return fields


def to_case_record_internal(
    record: CaseRecord,
    field_rows: Sequence[FieldValueRow],
    documents: Sequence[DocumentRow],
) -> CaseRecordInternal:
    """The case as H1's steps and H2's ``case_record`` tool read it: identity,
    pins, every field with its origin, the documents. No score: the score has
    its own route.
    """
    return CaseRecordInternal(
        case_id=record.case_id,
        title=record.title,
        unit=record.unit,
        owner=OwnerRef(id=record.owner, name=record.owner_name or record.owner),
        analyst=(
            OwnerRef(
                id=record.analyst_id, name=record.analyst_name or record.analyst_id
            )
            if record.analyst_id is not None
            else None
        ),
        state=record.state,
        run_id=record.run_id,
        change_types=list(record.change_types),
        domains=list(record.domains),
        pins=record.pinned_versions,
        fieldset_v=record.fieldset_v,
        required_field_ids=[f.field_id for f in record.required_fields],
        fields=_internal_fields(record, field_rows),
        documents=[to_document_internal(d) for d in documents],
        submission_version=record.current_submission_v,
    )
