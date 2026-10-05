"""BB-01, "nothing auto-approved/rejected" (documented: BUILD-PLAN.md
line ~306's one-line summary calls this "no auto decision"; the fuller,
concrete check is ``docs/architecture/design/C-web-tests-ops-sdlc.md``
section 3.1(f)'s table: "Drive a case to a released pack; wait 60 s;
``outcome`` is null; cast 3 votes; outcome equals the majority; with a
1-1-1 split the round is ``no_majority`` and a re-vote opens, never
approve/reject and never ``deferred``" -- ADR-08, ADR-31).

Two halves, both provable now that S5 has real routes:

- the **HTTP half**, through the real S5 app over its in-memory store: a
  released pack with no votes has no outcome (nothing decides it, however long
  it waits); three votes decide it as the majority; a 1-1-1 split decides
  nothing and opens round two.
- the **arithmetic half**: ``fcrm_contracts.voting.tally`` (via
  ``fcrm_decision.outcomes.tally_round``) must never manufacture an outcome out
  of anything other than two matching votes, exhaustively over all 64
  three-vote combinations. The one refinement (proposed): a 2-1 inside the
  approve family (``approve`` and ``approve_with_conditions`` only) resolves to
  ``approve_with_conditions``, so conditions are never dropped; it never merges
  across a defer or a reject.
"""

from __future__ import annotations

from collections.abc import Callable
from itertools import combinations, permutations, product
from typing import Any

import pytest
from fastapi.testclient import TestClient
from fcrm_contracts.enums import Outcome, Role
from fcrm_decision.outcomes import tally_round

pytestmark = pytest.mark.integration

VOTE_OPTIONS = [
    "approve",
    "approve_with_conditions",
    "defer",
    "reject",
]
APPROVE_FAMILY = {"approve", "approve_with_conditions"}
SEATS = ("u-jokafor", "u-amendes", "u-lchen")
CASE = "FCRM-2026-0151"


# ── the HTTP half ────────────────────────────────────────────────────────


def test_a_released_pack_with_no_votes_has_no_outcome_however_long_it_waits(
    decision_client: TestClient,
    act_as: Callable[[Role, str], None],
    released_pack: Callable[..., dict[str, Any]],
) -> None:
    pack_id = released_pack(CASE)["pack_id"]

    act_as(Role.ANALYST, "u-riyer")
    assert decision_client.get(f"/packs/{pack_id}/outcome").status_code == 409
    assert decision_client.get(f"/cases/{CASE}/outcome").status_code == 404
    pack = decision_client.get(f"/packs/{pack_id}").json()
    assert pack["status"] == "released"  # not decided, not approved, not rejected


def test_three_votes_decide_the_majority_and_nothing_else(
    decision_client: TestClient,
    act_as: Callable[[Role, str], None],
    released_pack: Callable[..., dict[str, Any]],
    cast_vote: Callable[..., Any],
    demo: dict[str, Any],
) -> None:
    pack_id = released_pack(CASE)["pack_id"]
    for member, option in zip(
        SEATS,
        ("approve_with_conditions", "approve_with_conditions", "defer"),
        strict=True,
    ):
        assert cast_vote(pack_id, member, option).status_code == 201

    act_as(Role.ANALYST, "u-riyer")
    decided = decision_client.get(f"/packs/{pack_id}/outcome").json()
    assert decided["outcome"] == demo["outcome"]
    assert decided["no_majority"] is False


@pytest.mark.parametrize("split", list(combinations(VOTE_OPTIONS, 3)))
def test_a_three_way_split_decides_nothing_and_opens_round_two(
    decision_client: TestClient,
    act_as: Callable[[Role, str], None],
    released_pack: Callable[..., dict[str, Any]],
    cast_vote: Callable[..., Any],
    split: tuple[str, str, str],
) -> None:
    pack_id = released_pack(CASE)["pack_id"]
    for member, option in zip(SEATS, split, strict=True):
        assert cast_vote(pack_id, member, option).status_code == 201

    act_as(Role.ANALYST, "u-riyer")
    verdict = decision_client.get(f"/packs/{pack_id}/outcome").json()
    assert verdict["outcome"] is None  # never approve, reject or deferred
    assert verdict["no_majority"] is True
    assert decision_client.get(f"/cases/{CASE}/outcome").status_code == 404
    votes = decision_client.get(f"/packs/{pack_id}/votes").json()
    assert votes["round"] == 2
    assert votes["cast_count"] == 0


# ── the arithmetic half ──────────────────────────────────────────────────


@pytest.mark.parametrize("votes", list(product(VOTE_OPTIONS, repeat=3)))
def test_an_outcome_is_only_ever_produced_when_two_of_three_votes_agree(
    votes: tuple[str, str, str],
) -> None:
    """Exhaustive sweep of all 4**3 = 64 three-vote combinations: the tally
    must never report an ``outcome`` unless at least two of the three cast
    votes are identical, and whenever it does, that outcome is the majority
    option (or, inside the approve family, ``approve_with_conditions``).
    """
    result = tally_round(list(votes), round_number=1)
    counts = {option: votes.count(option) for option in set(votes)}
    majority = next((option for option, n in counts.items() if n >= 2), None)

    if majority is None:
        assert result.outcome is None
        assert result.no_majority is True
        assert result.next_round == 2
    else:
        expected = (
            "approve_with_conditions" if set(votes) == APPROVE_FAMILY else majority
        )
        assert result.outcome == Outcome(expected)
        assert result.no_majority is False
        assert result.next_round is None


def test_every_three_way_split_is_no_majority_never_an_outcome() -> None:
    """The specific scenario the docs call out by name (ADR-31): a 1-1-1
    split (three distinct votes) must never resolve to any of the four
    outcomes, whichever order the votes arrive in.
    """
    splits = [
        perm for combo in combinations(VOTE_OPTIONS, 3) for perm in permutations(combo)
    ]
    assert len(splits) == 24  # C(4,3) * 3! orderings, sanity-checking the sweep

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
