"""ADR-08's tally rule (with ADR-31's 1-1-1 reading), in the one shared place
both S5 and H1 import it from.

The expectation table covers every multiset of three votes over the four
options: 4 unanimous ballots, 12 two-one ballots and 4 three-way splits, 20 in
all. Order never matters, so every permutation of each is checked against the
same row. Rows are data, not a second implementation of the rule.
"""

from __future__ import annotations

from itertools import combinations_with_replacement, permutations

import pytest
from fcrm_contracts.enums import Outcome
from fcrm_contracts.voting import QUORUM, Tally, TallyRule, tally, tally_votes

A, C, D, R = "approve", "approve_with_conditions", "defer", "reject"

#: (sorted ballot) -> (outcome, rule). ``None`` outcome means no_majority.
EXPECTED: dict[tuple[str, ...], tuple[Outcome | None, TallyRule]] = {
    # 3-0: the unanimous option.
    (A, A, A): (Outcome.APPROVE, TallyRule.UNANIMOUS),
    (C, C, C): (Outcome.APPROVE_WITH_CONDITIONS, TallyRule.UNANIMOUS),
    (D, D, D): (Outcome.DEFER, TallyRule.UNANIMOUS),
    (R, R, R): (Outcome.REJECT, TallyRule.UNANIMOUS),
    # 2-1 inside the approve family (proposed): conditions are never dropped.
    (A, A, C): (Outcome.APPROVE_WITH_CONDITIONS, TallyRule.APPROVE_FAMILY),
    (A, C, C): (Outcome.APPROVE_WITH_CONDITIONS, TallyRule.MAJORITY),
    # 2-1 across a defer or reject: the plain majority option.
    (A, A, D): (Outcome.APPROVE, TallyRule.MAJORITY),
    (A, A, R): (Outcome.APPROVE, TallyRule.MAJORITY),
    (A, D, D): (Outcome.DEFER, TallyRule.MAJORITY),
    (A, R, R): (Outcome.REJECT, TallyRule.MAJORITY),
    (C, C, D): (Outcome.APPROVE_WITH_CONDITIONS, TallyRule.MAJORITY),
    (C, C, R): (Outcome.APPROVE_WITH_CONDITIONS, TallyRule.MAJORITY),
    (C, D, D): (Outcome.DEFER, TallyRule.MAJORITY),
    (C, R, R): (Outcome.REJECT, TallyRule.MAJORITY),
    (D, D, R): (Outcome.DEFER, TallyRule.MAJORITY),
    (D, R, R): (Outcome.REJECT, TallyRule.MAJORITY),
    # 1-1-1: never an outcome, and in particular never defer (ADR-31).
    (A, C, D): (None, TallyRule.NO_MAJORITY),
    (A, C, R): (None, TallyRule.NO_MAJORITY),
    (A, D, R): (None, TallyRule.NO_MAJORITY),
    (C, D, R): (None, TallyRule.NO_MAJORITY),
}

ALL_BALLOTS = list(combinations_with_replacement(sorted({A, C, D, R}), 3))


def _key(ballot: tuple[str, ...]) -> tuple[str, ...]:
    order = {A: 0, C: 1, D: 2, R: 3}
    return tuple(sorted(ballot, key=order.__getitem__))


def test_the_expectation_table_covers_every_three_vote_multiset() -> None:
    assert len(ALL_BALLOTS) == 20
    assert {_key(ballot) for ballot in ALL_BALLOTS} == set(EXPECTED)


@pytest.mark.parametrize("ballot", sorted(EXPECTED))
def test_tally_every_ordering_of_a_ballot_gives_the_same_result(
    ballot: tuple[str, ...],
) -> None:
    outcome, rule = EXPECTED[ballot]
    for ordering in set(permutations(ballot)):
        result = tally(list(ordering))
        assert result.outcome == outcome, ordering
        assert result.no_majority is (outcome is None), ordering
        assert result.rule is rule, ordering


@pytest.mark.parametrize("ballot", [b for b, (o, _) in EXPECTED.items() if o is None])
def test_tally_a_three_way_split_is_never_defer_or_any_outcome(
    ballot: tuple[str, ...],
) -> None:
    for ordering in set(permutations(ballot)):
        result = tally(list(ordering))
        assert result.outcome is None
        assert result.outcome != Outcome.DEFER
        assert result.no_majority is True


def test_tally_the_golden_demo_two_conditions_one_defer_is_conditional_approval() -> (
    None
):
    result = tally([C, C, D])
    assert result.outcome is Outcome.APPROVE_WITH_CONDITIONS
    assert result.counts == {"approve_with_conditions": 2, "defer": 1}
    assert result.no_majority is False


def test_tally_counts_list_each_option_cast_in_option_order() -> None:
    assert tally([R, A, D]).counts == {"approve": 1, "defer": 1, "reject": 1}
    assert tally([D, D, D]).counts == {"defer": 3}


def test_tally_the_approve_family_refinement_never_merges_across_a_defer() -> None:
    """``A, C, D`` has two approve-family votes but three different options:
    it is a 1-1-1 split, not an approval.
    """
    result = tally([A, C, D])
    assert result.outcome is None
    assert result.rule is TallyRule.NO_MAJORITY


@pytest.mark.parametrize("votes", [[], [A], [A, A], [A, A, A, A], [C, C, D, D]])
def test_tally_a_round_without_exactly_three_votes_fails_closed(
    votes: list[str],
) -> None:
    result = tally(votes)
    assert result.outcome is None
    assert result.no_majority is True
    assert result.rule is TallyRule.NO_MAJORITY


def test_tally_an_unknown_option_is_refused() -> None:
    with pytest.raises(ValueError, match="maybe"):
        tally([A, A, "maybe"])


def test_tally_is_frozen_and_the_quorum_is_three() -> None:
    result = tally([A, A, A])
    assert isinstance(result, Tally)
    assert QUORUM == 3
    with pytest.raises(ValueError, match="frozen"):
        result.outcome = Outcome.REJECT  # type: ignore[misc]


def test_tally_votes_matches_tally_for_every_ballot() -> None:
    for ballot in ALL_BALLOTS:
        outcome, no_majority = tally_votes(list(ballot))
        result = tally(list(ballot))
        expected = result.outcome.value if result.outcome else None
        assert outcome == expected
        assert no_majority is result.no_majority


def test_tally_votes_keeps_the_legacy_pairs() -> None:
    assert tally_votes([C, C, D]) == ("approve_with_conditions", False)
    assert tally_votes([A, A, A]) == ("approve", False)
    assert tally_votes([A, D, R]) == (None, True)
    assert tally_votes([A, A]) == (None, True)
