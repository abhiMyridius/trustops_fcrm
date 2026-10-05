"""Field-set resolution, completion arithmetic, and the four submit
checks — verified against the demo case's real field counts
(docs/requirements/research/06-walkthrough-seed-extraction.md §3.3:
9/18 -> 12/18 -> 15/18, i.e. 50% -> 67% -> 83%).
"""

from __future__ import annotations

import pytest
from fcrm_case.domain.completion import compute_completion
from fcrm_case.domain.fieldset import resolve_required_fields
from fcrm_case.domain.submit_checks import evaluate_submit_checks
from fcrm_case.seedmodel import field_set_from_seed
from fcrm_seed.loader import load_raw


@pytest.fixture(scope="module")
def field_set():
    return field_set_from_seed(load_raw()["field_set"])


@pytest.fixture(scope="module")
def demo_required_fields(field_set):
    """The demo case's three change types: vendor_onboarding, new_geography,
    new_product."""
    return resolve_required_fields(
        ["vendor_onboarding", "new_geography", "new_product"], field_set
    )


def test_resolving_the_demo_s_three_change_types_gives_18_fields(
    demo_required_fields,
) -> None:
    assert len(demo_required_fields) == 18
    always_required = [f for f in demo_required_fields if f.always_required]
    assert len(always_required) == 6


def test_field_ids_are_the_expected_union_with_no_duplicates(
    demo_required_fields,
) -> None:
    ids = [f.field_id for f in demo_required_fields]
    assert len(ids) == len(set(ids))
    # always-required core
    for expected in ["F01", "F02", "F03", "F04", "F05", "F06"]:
        assert expected in ids
    # vendor_onboarding pack
    for expected in ["F10", "F11", "F12"]:
        assert expected in ids
    # new_geography pack
    for expected in ["F20", "F21", "F22", "F23"]:
        assert expected in ids
    # new_product pack
    for expected in ["F30", "F31", "F32", "F33", "F34"]:
        assert expected in ids
    # a field from an unselected type must not appear
    assert "F40" not in ids  # new_feature
    assert "F60" not in ids  # new_segment


def test_resolving_a_single_change_type_gives_only_its_own_pack_plus_core(
    field_set,
) -> None:
    resolved = resolve_required_fields(["new_feature"], field_set)
    assert len(resolved) == 6 + 3  # core + F40, F41, F42


def test_an_unknown_change_type_contributes_no_extra_fields(field_set) -> None:
    resolved = resolve_required_fields(["not_a_real_type"], field_set)
    assert len(resolved) == 6  # core only


class TestCompletionAtEachDemoStage:
    """The three stages in docs/requirements/research/
    06-walkthrough-seed-extraction.md §3.3: 9/18 -> 12/18 -> 15/18."""

    FILLED_AFTER_EXTRACTION = {
        "F01", "F02", "F03", "F04", "F05", "F06", "F20", "F23", "F12",
    }  # fmt: skip
    FILLED_AFTER_REUSE = FILLED_AFTER_EXTRACTION | {"F31", "F33", "F34"}
    FILLED_AT_SUBMIT = FILLED_AFTER_REUSE | {"F10", "F32", "F21"}

    def test_after_extraction_9_of_18_50_percent(self, demo_required_fields) -> None:
        result = compute_completion(demo_required_fields, self.FILLED_AFTER_EXTRACTION)
        assert result.required_count == 18
        assert result.filled_count == 9
        assert result.pct == pytest.approx(0.5)
        assert result.all_always_required_filled is True

    def test_after_reuse_12_of_18_67_percent(self, demo_required_fields) -> None:
        result = compute_completion(demo_required_fields, self.FILLED_AFTER_REUSE)
        assert result.filled_count == 12
        assert result.pct == pytest.approx(12 / 18)

    def test_at_submit_15_of_18_83_percent(self, demo_required_fields) -> None:
        result = compute_completion(demo_required_fields, self.FILLED_AT_SUBMIT)
        assert result.filled_count == 15
        assert result.pct == pytest.approx(15 / 18, abs=0.001)
        # F11, F22, F30 are still open per the seed (reasons given in the
        # walkthrough) -- none of them is always-required, so completion
        # can legitimately clear threshold without them.
        assert result.all_always_required_filled is True


def test_completion_with_zero_required_fields_is_vacuously_complete() -> None:
    result = compute_completion([], set())
    assert result.pct == 1.0
    assert result.all_always_required_filled is True


class TestSubmitChecks:
    def test_all_four_pass_at_the_demo_s_83_percent(self, demo_required_fields) -> None:
        completion = compute_completion(
            demo_required_fields, TestCompletionAtEachDemoStage.FILLED_AT_SUBMIT
        )
        checks = evaluate_submit_checks(
            completion,
            completion_threshold=0.8,
            no_open_blocking_gaps=True,
            owner_reviewed_corrections=True,
        )
        assert checks.all_passed is True

    def test_below_threshold_fails_the_completion_check_only(
        self, demo_required_fields
    ) -> None:
        completion = compute_completion(
            demo_required_fields, TestCompletionAtEachDemoStage.FILLED_AFTER_EXTRACTION
        )
        checks = evaluate_submit_checks(
            completion,
            completion_threshold=0.8,
            no_open_blocking_gaps=True,
            owner_reviewed_corrections=True,
        )
        assert checks.completion_at_least_threshold is False
        assert checks.all_always_required_filled is True
        assert checks.all_passed is False

    def test_a_missing_always_required_field_fails_even_above_threshold(self) -> None:
        """A case could clear 80% overall while still missing one
        always-required field (e.g. go-live date) -- that must still
        block submission; completion percentage alone is not the gate."""
        from fcrm_case.domain.fieldset import FieldDef

        fields = [
            FieldDef(field_id=f"F{i}", label=f"f{i}", always_required=i < 6)
            for i in range(18)
        ]
        filled = {
            f"F{i}" for i in range(18) if i != 2
        }  # miss F2, an always-required field
        completion = compute_completion(fields, filled)

        checks = evaluate_submit_checks(
            completion,
            completion_threshold=0.8,
            no_open_blocking_gaps=True,
            owner_reviewed_corrections=True,
        )
        assert completion.pct == pytest.approx(17 / 18)
        assert checks.completion_at_least_threshold is True
        assert checks.all_always_required_filled is False
        assert checks.all_passed is False

    def test_an_open_blocking_gap_fails_regardless_of_completion(
        self, demo_required_fields
    ) -> None:
        completion = compute_completion(
            demo_required_fields, TestCompletionAtEachDemoStage.FILLED_AT_SUBMIT
        )
        checks = evaluate_submit_checks(
            completion,
            completion_threshold=0.8,
            no_open_blocking_gaps=False,
            owner_reviewed_corrections=True,
        )
        assert checks.all_passed is False
