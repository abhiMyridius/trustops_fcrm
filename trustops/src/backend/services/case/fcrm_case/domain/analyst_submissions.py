"""The analyst's view of a case's frozen submissions.
Pure, no I/O.

Submission versions are what the owner froze: one per submit, per answered round
and per correction. They are read exactly as stored; the analyst never edits one
(rule 4). The diff compares any two of them (:func:`fcrm_case.domain.diffing.
diff_rows`) and is the same comparison the ``submission_diff`` tool reads, so a
re-run that re-proposes "only the changed fields" and the analyst's own diff
screen cannot disagree.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC

from fcrm_contracts.api.case_analyst import SubmissionVersionDiff
from fcrm_contracts.api.case_analyst_read import (
    SubmissionChange,
    SubmissionDiff,
    SubmissionField,
    SubmissionVersion,
    WasValue,
)
from fcrm_contracts.tools.submission_diff import SubmissionDiffOutput

from fcrm_case.domain.diffing import RowDiff
from fcrm_case.domain.fieldset import FieldDef
from fcrm_case.domain.po_rows import SubmissionVersionRow
from fcrm_case.domain.record import CaseRecord
from fcrm_case.domain.views import DocumentRow
from fcrm_case.po_views import source_ref


def _labels(required: Sequence[FieldDef]) -> dict[str, str]:
    return {f.field_id: f.label for f in required}


def _stamp(row: SubmissionVersionRow) -> str:
    return row.captured_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M")


def _by(record: CaseRecord, row: SubmissionVersionRow) -> str:
    """Who froze the version, by name: the owner's name when it was the owner."""
    if row.captured_by.sub == record.owner:
        return record.owner_name or record.owner
    return row.captured_by.sub


def version_label(record: CaseRecord, row: SubmissionVersionRow) -> str:
    """``v1 (as submitted 2026-10-01 09:40)``, ``v2 (after round 1, 14:05, Priya
    Nair)`` or ``v3 (owner correction, ...)``.
    """
    if row.reason == "round":
        clock = row.captured_at.astimezone(UTC).strftime("%H:%M")
        who = _by(record, row)
        return f"v{row.version} (after round {row.round or 1}, {clock}, {who})"
    if row.reason == "correction":
        return f"v{row.version} (owner correction, {_stamp(row)})"
    return f"v{row.version} (as submitted {_stamp(row)})"


def submission_version(
    record: CaseRecord,
    row: SubmissionVersionRow,
    documents: Mapping[str, DocumentRow],
) -> SubmissionVersion:
    """One frozen submission as the analyst reads it: every field with its value,
    origin and source, the original a corrected value replaced, and the documents.
    """
    labels = _labels(record.required_fields)
    fields = [
        SubmissionField(
            field_id=field_id,
            label=labels.get(field_id, field_id),
            value=value.value,
            origin=value.origin,
            source=source_ref(value.source, documents),
            was=WasValue(
                value=value.original.value or "",
                source=(
                    ref.label
                    if (ref := source_ref(value.original.source, documents))
                    else ""
                ),
            )
            if value.original is not None
            else None,
        )
        for field_id, value in row.field_values.items()
    ]
    return SubmissionVersion(
        version=row.version,
        label=version_label(record, row),
        at=row.captured_at.isoformat(),
        by=_by(record, row),
        fields=fields,
        documents=[
            documents[d].name if d in documents else d for d in row.document_ids
        ],
    )


def submission_diff(
    record: CaseRecord,
    diff: RowDiff,
    from_version: int,
    to_version: int,
    documents: Mapping[str, DocumentRow],
) -> SubmissionDiff:
    """The diff the Submission tab shows: each changed field with its label and
    its before and after value and origin, and the documents added.
    """
    labels = _labels(record.required_fields)
    return SubmissionDiff.model_validate(
        {
            "from": from_version,
            "to": to_version,
            "changed": [
                SubmissionChange(
                    field_id=c.field_id,
                    label=labels.get(c.field_id, c.field_id),
                    before=c.before,
                    after=c.after,
                    before_origin=c.before_origin,  # type: ignore[arg-type]
                    after_origin=c.after_origin,  # type: ignore[arg-type]
                )
                for c in diff.changes
            ],
            "added_documents": [
                documents[d].name if d in documents else d
                for d in diff.added_document_ids
            ],
        }
    )


def tool_diff(diff: RowDiff) -> SubmissionDiffOutput:
    """The diff in the ``submission_diff`` tool's shape: only fields whose *value*
    differs (an origin-only change is not something a re-run re-proposes).
    """
    return SubmissionDiffOutput(
        changed_fields=[
            SubmissionVersionDiff(field_id=c.field_id, before=c.before, after=c.after)
            for c in diff.changes
            if c.value_changed
        ]
    )
