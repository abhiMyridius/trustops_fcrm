"""ADR-08's tally rule, in the one shared place both S5 and H1 import it
from."""

from __future__ import annotations

from fcrm_contracts.voting import tally_votes


def test_two_of_three_identical_votes_wins() -> None:
    outcome, no_majority = tally_votes(
        ["approve_with_conditions", "approve_with_conditions", "defer"]
    )
    assert outcome == "approve_with_conditions"
    assert no_majority is False


def test_all_three_identical_is_a_clean_win() -> None:
    outcome, no_majority = tally_votes(["approve", "approve", "approve"])
    assert outcome == "approve"
    assert no_majority is False


def test_a_three_way_split_is_no_majority_never_an_outcome() -> None:
    outcome, no_majority = tally_votes(["approve", "defer", "reject"])
    assert outcome is None
    assert no_majority is True


def test_fewer_than_three_votes_is_also_no_majority() -> None:
    outcome, no_majority = tally_votes(["approve", "approve"])
    assert outcome is None
    assert no_majority is True
