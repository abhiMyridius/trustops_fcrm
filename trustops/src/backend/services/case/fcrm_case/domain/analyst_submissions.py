"""The analyst's view of a case's frozen submissions and the search of past cases.
Pure, no I/O.

Submission versions are what the owner froze: one per submit, per answered round
and per correction. They are read exactly as stored; the analyst never edits one
(rule 4). The diff compares any two of them (:func:`fcrm_case.domain.diffing.
diff_rows`) and is the same comparison the ``submission_diff`` tool reads, so a
re-run that re-proposes "only the changed fields" and the analyst's own diff
screen cannot disagree.

Case search ranks stored cases against a query by word overlap with the title
and by overlap of change types: a deterministic score, not a model's. The
``case_search`` tool reads it as precedent search; it never reads another case's
content, only its title, types and recorded outcome.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC
from typing import Final, Optional

from fcrm_contracts.api.case_analyst import SubmissionVersionDiff
from fcrm_contracts.api.case_analyst_read import (
    SubmissionChange,
    SubmissionDiff,
    SubmissionField,
    SubmissionVersion,
    WasValue,
)
from fcrm_contracts.tools.case_search import CaseSearchHit
from fcrm_contracts.tools.submission_diff import SubmissionDiffOutput

from fcrm_case.domain.diffing import RowDiff
from fcrm_case.domain.fieldset import FieldDef
from fcrm_case.domain.po_rows import SubmissionVersionRow
from fcrm_case.domain.record import CaseRecord
from fcrm_case.domain.views import DocumentRow
from fcrm_case.po_views import source_ref

#: The words of a title and of a query that count towards a match.
_WORD = re.compile(r"[a-z0-9]{3,}")
#: How much of the similarity is the title and how much the change types (when
#: the query names change types).
TITLE_WEIGHT: Final = 0.7
TYPE_WEIGHT: Final = 0.3


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


# -- case search -------------------------------------------------------------------


@dataclass(frozen=True)
class CaseCandidate:
    """A stored case the search may return: its title, types and outcome."""

    case_id: str
    title: str
    change_types: tuple[str, ...]
    outcome: Optional[str]


def words(text: str) -> frozenset[str]:
    """The lower-case words of ``text`` that count towards a match."""
    return frozenset(_WORD.findall(text.lower()))


def similarity(
    query_words: frozenset[str],
    wanted_types: frozenset[str],
    candidate: CaseCandidate,
) -> float:
    """How alike a case is to the query, 0 to 1: the share of the words of query
    and title they have in common, blended with the share of the wanted change
    types the case has when the query names any.
    """
    title = words(candidate.title)
    union = query_words | title
    overlap = len(query_words & title) / len(union) if union else 0.0
    if not wanted_types:
        return round(overlap, 4)
    shared = len(wanted_types & set(candidate.change_types)) / len(wanted_types)
    return round(TITLE_WEIGHT * overlap + TYPE_WEIGHT * shared, 4)


def rank_cases(
    query: str,
    change_types: Sequence[str],
    candidates: Sequence[CaseCandidate],
    top_k: int,
) -> list[CaseSearchHit]:
    """The best ``top_k`` candidates for ``query``, best first (ties by case id);
    a candidate with no similarity at all is not a hit.
    """
    query_words = words(query)
    wanted = frozenset(change_types)
    scored = [(similarity(query_words, wanted, c), c) for c in candidates]
    best = sorted(
        ((s, c) for s, c in scored if s > 0),
        key=lambda pair: (-pair[0], pair[1].case_id),
    )
    return [
        CaseSearchHit(case_id=c.case_id, title=c.title, similarity=s, outcome=c.outcome)
        for s, c in best[:top_k]
    ]
