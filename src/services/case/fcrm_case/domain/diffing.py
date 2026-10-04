"""Diffing two submission-version snapshots.

Design B's ``submission_diff`` tool (``fcrm_contracts.tools.submission_diff``)
and the analyst's version-history screen both want the same thing: which
fields changed between two submission versions. Each
``SubmissionVersionRecord`` already holds a full ``field_values`` snapshot
(``fcrm_case.domain.record``), so a diff between any two versions -- not
just adjacent ones -- is pure comparison, no replay needed.
"""

from __future__ import annotations

from fcrm_contracts.api.case_analyst import SubmissionVersionDiff

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
