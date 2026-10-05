"""BB-05, "votes hidden until all cast" (design doc numbering: "After 2 votes,
``GET votes`` ... shows own choice only; S5 DB has no reveal; third vote ->
all three visible in one response"; routing rule R2: quorum 3, votes hidden
until all are cast, rationale required on each vote).

Driven through the real S5 app (``fcrm_decision.main.create_app``) over its
in-memory store, as a third party would: only HTTP requests and responses,
no peeking at store internals.

Two honest differences from the design-doc wording, found while writing
this and asserted as they actually are rather than as the table words them:

- The table says each member sees "own choice only" before quorum. S5 is
  stricter and the route docstring says so on purpose: before quorum *no*
  response carries any vote content, not even the caller's own vote read
  back. (S5 now does know the caller -- ``member_id`` is bound to the
  verified principal, D-23 -- but still chooses not to echo a vote back.)
  What is asserted: before the third vote, every response is a bare count
  and nothing identifying an option, a member or a rationale appears
  anywhere in it.
- "S5 DB has no reveal" cannot be checked over HTTP: the store is not
  reachable from a black-box client. The closest HTTP-observable proof is
  asserted instead -- no outcome exists (``GET .../outcome`` is 409) until
  the third vote has landed.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from fcrm_contracts.enums import Role

pytestmark = pytest.mark.integration

HIDDEN_KEYS = {"pack_id", "round", "votes_cast", "quorum"}

RATIONALES = {
    "u-jokafor": "Exposure is bounded and the conditions close the evidence gap.",
    "u-amendes": "Channel risk is real but the conditions address it directly.",
    "u-lchen": "The corridor review is not yet complete, so I would defer.",
}


@pytest.fixture
def members(committee_member_ids: dict[str, str]) -> dict[str, str]:
    """The demo's three committee members, keyed by surname."""
    return {
        "okafor": committee_member_ids["Joseph Okafor"],
        "mendes": committee_member_ids["Ana Mendes"],
        "chen": committee_member_ids["Lin Chen"],
    }


def _assert_bare_count(response_text: str, body: dict[str, Any]) -> None:
    """No vote content of any kind: only the four count keys, and none of
    the strings a leak would have to contain anywhere in the raw body.
    """
    assert set(body) == HIDDEN_KEYS
    for leaked in (
        "approve",
        "defer",
        "reject",
        "rationale",
        "member",
        *RATIONALES,
        *RATIONALES.values(),
    ):
        assert leaked not in response_text, f"{leaked!r} leaked before quorum"


def test_votes_are_a_bare_count_until_the_third_lands_then_all_three_reveal_at_once(
    decision_client: TestClient,
    build_pack: Callable[..., dict[str, Any]],
    cast_vote: Callable[..., Any],
    members: dict[str, str],
    demo: dict[str, Any],
) -> None:
    pack = build_pack(demo["id"], recommendation=demo["recommendation"]["outcome"])
    pack_id = pack["pack_id"]
    votes_url = f"/packs/{pack_id}/votes"
    outcome_url = f"/packs/{pack_id}/outcome"

    first = cast_vote(
        pack_id,
        members["okafor"],
        "approve_with_conditions",
        rationale=RATIONALES["u-jokafor"],
    )
    assert first.status_code == 200
    _assert_bare_count(first.text, first.json())
    assert first.json()["votes_cast"] == 1
    assert first.json()["quorum"] == 3

    second = cast_vote(
        pack_id,
        members["mendes"],
        "approve_with_conditions",
        rationale=RATIONALES["u-amendes"],
    )
    assert second.status_code == 200
    _assert_bare_count(second.text, second.json())
    assert second.json()["votes_cast"] == 2

    # Reading back, repeatedly, before quorum: still only the count -- even
    # for the two members who have already voted.
    for _ in range(3):
        read = decision_client.get(votes_url)
        assert read.status_code == 200
        _assert_bare_count(read.text, read.json())
        assert read.json()["votes_cast"] == 2

    # No outcome exists before the third vote lands.
    early_outcome = decision_client.get(outcome_url)
    assert early_outcome.status_code == 409
    assert early_outcome.json()["type"].endswith("/conflict")

    third = cast_vote(
        pack_id, members["chen"], "defer", rationale=RATIONALES["u-lchen"]
    )
    assert third.status_code == 200
    # The completing vote returns the outcome, never the hidden votes.
    assert third.json() == {
        "case_id": demo["id"],
        "pack_id": pack_id,
        "round": 1,
        "outcome": demo["outcome"],
        "no_majority": False,
    }

    revealed = decision_client.get(votes_url)
    assert revealed.status_code == 200
    body = revealed.json()
    assert {vote["member_id"] for vote in body["votes"]} == set(members.values())
    by_member = {vote["member_id"]: vote for vote in body["votes"]}
    assert by_member[members["okafor"]]["option"] == "approve_with_conditions"
    assert by_member[members["mendes"]]["option"] == "approve_with_conditions"
    assert by_member[members["chen"]]["option"] == "defer"
    # Every revealed vote carries the rationale its member gave.
    for user_id, rationale in RATIONALES.items():
        assert by_member[user_id]["rationale"] == rationale
    assert body["tally"] == demo["tally"]  # 2 approve_with_conditions, 1 defer

    decided = decision_client.get(outcome_url)
    assert decided.status_code == 200
    assert decided.json()["outcome"] == demo["outcome"]


def test_a_member_cannot_vote_twice_to_change_a_hidden_vote(
    build_pack: Callable[..., dict[str, Any]],
    cast_vote: Callable[..., Any],
    decision_client: TestClient,
    members: dict[str, str],
) -> None:
    pack_id = build_pack("FCRM-2026-0151")["pack_id"]
    assert cast_vote(pack_id, members["okafor"], "reject").status_code == 200

    again = cast_vote(pack_id, members["okafor"], "approve")
    assert again.status_code == 409
    # The refusal leaks neither the earlier vote nor the attempted one.
    assert "reject" not in again.text
    assert "approve" not in again.text

    count = decision_client.get(f"/packs/{pack_id}/votes").json()["votes_cast"]
    assert count == 1


@pytest.mark.parametrize("blank", ["", "   ", "\n\t"])
def test_a_vote_without_a_rationale_is_refused_and_not_counted(
    build_pack: Callable[..., dict[str, Any]],
    cast_vote: Callable[..., Any],
    decision_client: TestClient,
    members: dict[str, str],
    blank: str,
) -> None:
    pack_id = build_pack("FCRM-2026-0151")["pack_id"]

    refused = cast_vote(pack_id, members["okafor"], "approve", rationale=blank)
    assert refused.status_code == 409
    assert refused.json()["type"].endswith("/rule_violation")

    assert decision_client.get(f"/packs/{pack_id}/votes").json()["votes_cast"] == 0
    # The member may still vote properly afterwards: the refusal burned nothing.
    assert cast_vote(pack_id, members["okafor"], "approve").status_code == 200


def test_a_vote_with_the_rationale_field_missing_entirely_is_a_422(
    build_pack: Callable[..., dict[str, Any]],
    decision_client: TestClient,
    act_as: Callable[[Role, str], None],
    members: dict[str, str],
) -> None:
    pack_id = build_pack("FCRM-2026-0151")["pack_id"]
    act_as(Role.COMMITTEE, members["okafor"])
    response = decision_client.post(
        f"/packs/{pack_id}/votes",
        json={
            "pack_id": pack_id,
            "member_id": members["okafor"],
            "round": 1,
            "option": "approve",
        },
    )
    assert response.status_code == 422
    assert decision_client.get(f"/packs/{pack_id}/votes").json()["votes_cast"] == 0


def test_a_1_1_1_split_reveals_round_one_then_opens_a_fresh_hidden_round(
    decision_client: TestClient,
    build_pack: Callable[..., dict[str, Any]],
    cast_vote: Callable[..., Any],
    members: dict[str, str],
) -> None:
    """A three-way split is never resolved to any outcome (ADR-08): the
    round's votes are revealed, no outcome is set, and a new round opens in
    which votes are hidden again until all three are re-cast.
    """
    pack_id = build_pack("FCRM-2026-0151")["pack_id"]
    cast_vote(pack_id, members["okafor"], "approve")
    cast_vote(pack_id, members["mendes"], "defer")
    split = cast_vote(pack_id, members["chen"], "reject")
    assert split.status_code == 200
    assert split.json()["outcome"] is None
    assert split.json()["no_majority"] is True

    round_one = decision_client.get(f"/packs/{pack_id}/votes", params={"round": 1})
    assert round_one.status_code == 200
    assert round_one.json()["tally"] == {"approve": 1, "defer": 1, "reject": 1}

    # Round 2 is the current round and starts empty and hidden.
    current = decision_client.get(f"/packs/{pack_id}/votes")
    assert current.json() == {
        "pack_id": pack_id,
        "round": 2,
        "votes_cast": 0,
        "quorum": 3,
    }
    # A late round-1 vote is refused (the round moved on).
    stale = cast_vote(pack_id, members["okafor"], "approve", round_=1)
    assert stale.status_code == 409

    first_again = cast_vote(pack_id, members["okafor"], "approve", round_=2)
    assert first_again.status_code == 200
    _assert_bare_count(first_again.text, first_again.json())
    assert first_again.json()["round"] == 2
    assert first_again.json()["votes_cast"] == 1
