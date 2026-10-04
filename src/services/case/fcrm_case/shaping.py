"""Maps the stored :class:`~fcrm_case.domain.record.CaseRecord` onto the
three role-shaped wire contracts (``fcrm_contracts.api.case_po``/
``case_analyst``/``case_internal``).

This is the one place that split happens. ``CaseDetailPO`` has no field a
bug here could even assign a score into -- :func:`to_case_detail_po`
builds it field by field from data that was never score-shaped to begin
with, rather than building the analyst shape first and trying to strip it
down (CLAUDE.md's "the PO never sees scores" rule is enforced by
``CaseDetailPO``'s own schema, not by this function's care).
"""

from __future__ import annotations

from fcrm_contracts.api.case_analyst import (
    CaseDetailAnalyst,
    CaseListItemAnalyst,
    SubmissionVersion,
    SubmissionVersionDiff,
)
from fcrm_contracts.api.case_internal import CaseDetailInternal
from fcrm_contracts.api.case_po import (
    CaseDetailPO,
    CaseListItemPO,
    CompletionState,
    FieldValuePO,
)

from fcrm_case.domain.completion import compute_completion
from fcrm_case.domain.diffing import diff_submission_versions
from fcrm_case.domain.record import CaseRecord


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


def to_case_detail_po(record: CaseRecord) -> CaseDetailPO:
    """The PO-visible case detail. No field here can carry a score, band or
    rating -- ``CaseDetailPO`` has none (see the module docstring).
    """
    completion = completion_state(record)
    return CaseDetailPO(
        case_id=record.case_id,
        title=record.title,
        state=record.state,
        unit=record.unit,
        owner=record.owner,
        change_types=record.change_types,
        completion_pct=completion.pct,
        threshold_pct=completion.threshold_pct,
        required_field_count=completion.required_count,
        required_fields_filled=completion.filled_count,
        fields=_fields_po(record),
        documents=[],
        clarifications=[],
        conditions=[],
        outcome_summary=record.outcome_summary,
        created_at=record.created_at,
        submitted_at=record.submitted_at,
        decided_at=record.decided_at,
    )


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


def to_case_list_item_po(record: CaseRecord) -> CaseListItemPO:
    """The PO's case list row."""
    return CaseListItemPO(
        case_id=record.case_id,
        title=record.title,
        state=record.state,
        change_types=record.change_types,
        created_at=record.created_at,
        submitted_at=record.submitted_at,
        open_clarification_count=0,
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
