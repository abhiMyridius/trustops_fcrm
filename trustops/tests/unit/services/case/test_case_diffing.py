"""``fcrm_case.domain.diffing``: comparing two submission-version
snapshots, including a non-adjacent pair.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fcrm_case.domain.diffing import (
    SubmissionVersionNotFoundError,
    diff_submission_versions,
)
from fcrm_case.domain.record import SubmissionVersionRecord

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _version(number: int, **field_values: str | None) -> SubmissionVersionRecord:
    return SubmissionVersionRecord(
        version=number, created_at=NOW, created_by="u-po", field_values=field_values
    )


def test_diffing_adjacent_versions_reports_only_changed_fields() -> None:
    versions = [
        _version(1, F01="Priya Nair", F02=None),
        _version(2, F01="Priya Nair", F02="Q1 2027"),
    ]
    changed = diff_submission_versions(versions, 1, 2)
    assert len(changed) == 1
    assert changed[0].field_id == "F02"
    assert changed[0].before is None
    assert changed[0].after == "Q1 2027"


def test_diffing_non_adjacent_versions_skips_intermediate_history() -> None:
    versions = [
        _version(1, F01="a"),
        _version(2, F01="b"),
        _version(3, F01="c"),
    ]
    changed = diff_submission_versions(versions, 1, 3)
    assert len(changed) == 1
    assert changed[0].before == "a"
    assert changed[0].after == "c"


def test_diffing_identical_snapshots_reports_no_changes() -> None:
    versions = [_version(1, F01="a"), _version(2, F01="a")]
    assert diff_submission_versions(versions, 1, 2) == []


def test_diffing_an_unknown_version_raises() -> None:
    versions = [_version(1, F01="a")]
    with pytest.raises(SubmissionVersionNotFoundError):
        diff_submission_versions(versions, 1, 2)
