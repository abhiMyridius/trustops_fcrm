"""Diffing two submission-version snapshots.

Design B's ``submission_diff`` tool (``fcrm_contracts.tools.submission_diff``)
and the analyst's version-history screen both want the same thing: which
fields changed between two submission versions. Each
``SubmissionVersionRecord`` already holds a full ``field_values`` snapshot
(``fcrm_case.domain.record``), so a diff between any two versions -- not
just adjacent ones -- is pure comparison, no replay needed.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Optional

from fcrm_contracts.api.case_analyst import SubmissionVersionDiff

from fcrm_case.domain.po_rows import SnapshotValue, SubmissionVersionRow
from fcrm_case.domain.record import SubmissionVersionRecord


class SubmissionVersionNotFoundError(ValueError):
    """Raised with the missing version number(s)."""


def diff_submission_versions(
    versions: list[SubmissionVersionRecord], from_version: int, to_version: int
) -> list[SubmissionVersionDiff]:
    """Fields whose value differs between ``from_version`` and
    ``to_version``'s snapshots, in ``field_values`` key order.

    Raises:
        SubmissionVersionNotFoundError: Either version number is not among
            ``versions``.
    """
    by_number = {v.version: v for v in versions}
    missing = [v for v in (from_version, to_version) if v not in by_number]
    if missing:
        raise SubmissionVersionNotFoundError(
            f"submission version(s) not found: {missing}"
        )
    before = by_number[from_version].field_values
    after = by_number[to_version].field_values
    field_ids = sorted({*before.keys(), *after.keys()})
    return [
        SubmissionVersionDiff(
            field_id=field_id, before=before.get(field_id), after=after.get(field_id)
        )
        for field_id in field_ids
        if before.get(field_id) != after.get(field_id)
    ]


@dataclass(frozen=True)
class FieldChange:
    """One field that differs between two frozen submissions: its value and where
    that value came from, before and after (``open`` when nothing was filled).
    """

    field_id: str
    before: Optional[str]
    after: Optional[str]
    before_origin: str
    after_origin: str

    @property
    def value_changed(self) -> bool:
        """Whether the *value* differs (an origin-only change does not)."""
        return self.before != self.after


@dataclass(frozen=True)
class RowDiff:
    """What changed between two frozen submissions: the fields, and the documents
    the later one holds that the earlier one did not.
    """

    changes: list[FieldChange]
    added_document_ids: list[str]


def diff_rows(
    rows: Sequence[SubmissionVersionRow], from_version: int, to_version: int
) -> RowDiff:
    """The fields (in field-id order) whose value or origin differs between two
    frozen submissions, and the documents added since; any two versions compare,
    not just adjacent ones.

    Raises:
        SubmissionVersionNotFoundError: Either version number is not among ``rows``.
    """
    by_number = {row.version: row for row in rows}
    missing = [v for v in (from_version, to_version) if v not in by_number]
    if missing:
        raise SubmissionVersionNotFoundError(
            f"submission version(s) not found: {missing}"
        )
    before, after = by_number[from_version], by_number[to_version]
    changes: list[FieldChange] = []
    for field_id in sorted({*before.field_values, *after.field_values}):
        was = before.field_values.get(field_id) or SnapshotValue(origin="open")
        now = after.field_values.get(field_id) or SnapshotValue(origin="open")
        if was.value != now.value or was.origin != now.origin:
            changes.append(
                FieldChange(field_id, was.value, now.value, was.origin, now.origin)
            )
    known = set(before.document_ids)
    return RowDiff(changes, [d for d in after.document_ids if d not in known])
