"""BB-01, "nothing auto-approved/rejected" (documented: BUILD-PLAN.md
line ~306's one-line summary calls this "no auto decision"; the fuller,
concrete check is ``docs/architecture/design/C-web-tests-ops-sdlc.md``
section 3.1(f)'s table: "Drive a case to a released pack; wait 60 s;
``outcome`` is null; cast 3 votes; outcome equals the majority; with a
1-1-1 split outcome is ``deferred``, never approve/reject").

Status today: the "drive a case to a released pack and wait 60 s"
half needs H1 (caseflow) and S5 (decision) HTTP routes, neither of which
exists yet -- not provable without a composed stack (see
``test_bb_not_yet_testable.py``). The half that *is* provable today, with
real code, is the pure arithmetic: ``fcrm_contracts.voting.tally_votes``
(via ``fcrm_decision.outcomes.tally_round``) must never manufacture an
outcome out of anything other than two matching votes, exhaustively, not
just for the one hypothetical case the package's own unit tests already
cover (``tests/unit/services/decision/test_packs_and_outcomes.py`` has
one property-shaped example; this file is the full 64-combination sweep).

One correction to the design doc's wording while writing this: a 1-1-1
split does not actually produce ``Outcome.DEFER`` in the code as it
stands today -- ``tally_round`` returns ``outcome=None, no_majority=True``,
and "deferred" would have to be *H1's* decision about what to do with a
``no_majority`` tally (re-request a round, per routing rule R6), not a
value ``fcrm_decision`` itself ever returns. H1's own mapping from
``no_majority`` to a case state is not built yet (pure state machine
only, no HTTP), so this file tests what the code actually does rather
than asserting the design doc's "is ``deferred``" phrasing as if it were
already implemented.
"""

from __future__ import annotations

from itertools import product
from typing import Any

import pytest
from fcrm_contracts.enums import Outcome
from fcrm_decision.outcomes import tally_round

pytestmark = pytest.mark.integration

VOTE_OPTIONS = [
    "approve",
    "approve_with_conditions",
    "defer",
    "reject",
]


@pytest.mark.parametrize("votes", list(product(VOTE_OPTIONS, repeat=3)))
def test_an_outcome_is_only_ever_produced_when_two_of_three_votes_agree(
    votes: tuple[str, str, str],
) -> None:
    """Exhaustive sweep of all 4**3 = 64 three-vote combinations: the
    tally must never report an ``outcome`` unless at least two of the
    three cast votes are identical, and whenever it does, that outcome is
    exactly the majority option -- there is no path through this function
    that invents a result nobody voted for.
    """
    result = tally_round(list(votes), round_number=1)
    counts = {option: votes.count(option) for option in set(votes)}
    majority = next((option for option, n in counts.items() if n >= 2), None)

    if majority is None:
        assert result.outcome is None
        assert result.no_majority is True
        assert result.next_round == 2
    else:
        assert result.outcome == Outcome(majority)
        assert result.no_majority is False
        assert result.next_round is None


def test_every_three_way_split_is_no_majority_never_approve_or_reject() -> None:
    """The specific scenario the docs call out by name: a 1-1-1 split
    (three distinct votes) must never auto-resolve to any of the four
    outcomes -- checked over every ordering of every 3-distinct-value
    combination drawn from the four vote options, not just one example.
    """
    from itertools import combinations, permutations

    splits = [
        perm for combo in combinations(VOTE_OPTIONS, 3) for perm in permutations(combo)
    ]
    assert len(splits) == 24  # C(4,3) * 3! orderings, sanity-checking the sweep itself

    for votes in splits:
        result = tally_round(list(votes), round_number=1)
        assert result.outcome is None, f"{votes} produced an outcome, must not"
        assert result.no_majority is True


def test_the_real_demo_vote_is_the_only_non_split_case_in_this_sweep_that_matters(
    demo: dict[str, Any],
) -> None:
    """Grounds the exhaustive sweep above in the one vote that actually
    happened: Okafor and Mendes both vote ``approve_with_conditions``,
    Chen defers -- two of three agree, so (and only so) an outcome is
    produced, matching the seed's stored ``demo["outcome"]`` exactly.
    """
    votes = [v["vote"] for v in demo["votes"]]
    result = tally_round(votes, round_number=1)
    assert result.outcome is not None
    assert result.outcome.value == demo["outcome"]
    assert result.no_majority is False


def test_fewer_or_more_than_three_cast_votes_is_also_never_an_outcome() -> None:
    """``tally_round`` is defined over exactly three votes (committee
    quorum, D-19/R2); malformed inputs (a dropped or duplicated vote, the
    kind of bug that would otherwise let a case close on two votes) must
    fail closed to ``no_majority``, never to an outcome.
    """
    for votes in ([], ["approve"], ["approve", "approve"], ["approve"] * 4):
        result = tally_round(votes, round_number=1)
        assert result.outcome is None
        assert result.no_majority is True
