"""BB-05, "votes hidden until all cast" (design doc numbering: "After 2 votes,
``GET votes`` ... shows own choice only; S5 DB has no reveal; third vote ->
all three visible in one response"; routing rule R2: quorum 3, votes hidden
until all are cast, rationale required on each vote).

Driven through the real S5 app (``fcrm_decision.main.create_app``) over its
in-memory store, as a third party would: only HTTP requests and responses, no
peeking at store internals.

The design table is met as written now that the web's shape is the contract
(ADR-30): before the third vote each member sees their **own** choice and
nothing else; every other member's choice and rationale is not in any response,
not merely masked; the third vote reveals the three votes and the tally
together. "S5 DB has no reveal" cannot be checked over HTTP, so the closest
HTTP-observable proofs are asserted: no outcome exists (``GET .../outcome`` is
409, the case's outcome 404) until the third vote has landed, and the peers'
choices appear in no response of any role.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from fcrm_contracts.enums import Role

pytestmark = pytest.mark.integration

RATIONALES = {
    "u-jokafor": "Exposure is bounded and the conditions close the evidence gap.",
    "u-amendes": "Channel risk is real but the conditions address it directly.",
    "u-lchen": "The corridor review is not yet complete, so I would defer.",
}
OPTION_WORDS = ("approve", "defer", "reject")


@pytest.fixture
def members(committee_member_ids: dict[str, str]) -> dict[str, str]:
    """The demo's three committee members, keyed by surname."""
    return {
        "okafor": committee_member_ids["Joseph Okafor"],
        "mendes": committee_member_ids["Ana Mendes"],
        "chen": committee_member_ids["Lin Chen"],
    }


def _read(
    client: TestClient,
    act_as: Callable[[Role, str], None],
    pack_id: str,
    role: Role,
    sub: str,
) -> Any:
    act_as(role, sub)
    return client.get(f"/packs/{pack_id}/votes")


def _assert_no_peer_content(text: str, *, allowed_own: tuple[str, ...] = ()) -> None:
    """Neither a peer's option word nor any rationale but the caller's own."""
    lowered = text.lower()
    for word in OPTION_WORDS:
        assert word not in lowered, f"{word!r} leaked before quorum"
    for user_id, rationale in RATIONALES.items():
        if user_id not in allowed_own:
            assert rationale not in text


def test_votes_are_hidden_until_the_third_lands_then_all_three_reveal_at_once(
    decision_client: TestClient,
    act_as: Callable[[Role, str], None],
    released_pack: Callable[..., dict[str, Any]],
    cast_vote: Callable[..., Any],
    members: dict[str, str],
    demo: dict[str, Any],
) -> None:
    pack_id = released_pack(demo["id"])["pack_id"]
    outcome_url = f"/packs/{pack_id}/outcome"

    first = cast_vote(
        pack_id,
        members["okafor"],
        "approve_with_conditions",
        rationale=RATIONALES["u-jokafor"],
    )
    assert first.status_code == 201
    assert first.json()["cast_n_of_3"] == 1
    _assert_no_peer_content(first.text)  # the receipt echoes nothing

    second = cast_vote(
        pack_id,
        members["mendes"],
        "approve_with_conditions",
        rationale=RATIONALES["u-amendes"],
    )
    assert second.json()["cast_n_of_3"] == 2
    _assert_no_peer_content(second.text)

    # Reading back, repeatedly, before quorum: each voter sees only their own.
    for _ in range(3):
        for voter, role in (
            (members["okafor"], Role.COMMITTEE),
            (members["mendes"], Role.COMMITTEE),
        ):
            read = _read(decision_client, act_as, pack_id, role, voter)
            body = read.json()
            assert read.status_code == 200
            assert body["revealed"] is False
            assert body["cast_count"] == 2
            assert body["mine"] is not None
            peers = [m for m in body["members"] if not m["is_you"]]
            assert all(m["choice"] is None and m["rationale"] is None for m in peers)
        # Chen has not voted, so nothing of anyone's vote is in his view; neither
        # is it in the analyst's.
        for role, sub in ((Role.COMMITTEE, members["chen"]), (Role.ANALYST, "u-riyer")):
            _assert_no_peer_content(
                _read(decision_client, act_as, pack_id, role, sub).text
            )

    # No outcome exists before the third vote lands.
    act_as(Role.ANALYST, "u-riyer")
    early_outcome = decision_client.get(outcome_url)
    assert early_outcome.status_code == 409
    assert early_outcome.json()["type"].endswith("/not_tallied")
    assert decision_client.get(f"/cases/{demo['id']}/outcome").status_code == 404

    third = cast_vote(
        pack_id, members["chen"], "defer", rationale=RATIONALES["u-lchen"]
    )
    assert third.status_code == 201
    assert third.json()["cast_n_of_3"] == 3

    revealed = _read(decision_client, act_as, pack_id, Role.ANALYST, "u-riyer").json()
    assert revealed["revealed"] is True
    assert revealed["tally"] == demo["tally"]  # 2 approve_with_conditions, 1 defer
    assert revealed["outcome"] == demo["outcome"]
    by_name = {m["name"]: m for m in revealed["members"]}
    assert by_name["Joseph Okafor"]["choice"] == "approve_with_conditions"
    assert by_name["Ana Mendes"]["choice"] == "approve_with_conditions"
    assert by_name["Lin Chen"]["choice"] == "defer"
    # Every revealed vote carries the rationale its member gave.
    assert by_name["Joseph Okafor"]["rationale"] == RATIONALES["u-jokafor"]
    assert by_name["Ana Mendes"]["rationale"] == RATIONALES["u-amendes"]
    assert by_name["Lin Chen"]["rationale"] == RATIONALES["u-lchen"]

    decided = decision_client.get(outcome_url)
    assert decided.status_code == 200
    assert decided.json()["outcome"] == demo["outcome"]


def test_a_member_cannot_vote_twice_to_change_a_hidden_vote(
    decision_client: TestClient,
    act_as: Callable[[Role, str], None],
    released_pack: Callable[..., dict[str, Any]],
    cast_vote: Callable[..., Any],
    members: dict[str, str],
) -> None:
    pack_id = released_pack("FCRM-2026-0151")["pack_id"]
    assert cast_vote(pack_id, members["okafor"], "reject").status_code == 201

    again = cast_vote(pack_id, members["okafor"], "approve")
    assert again.status_code == 409
    assert again.json()["type"].endswith("/already_voted")
    # The refusal leaks neither the earlier vote nor the attempted one.
    assert "reject" not in again.text
    assert "approve" not in again.text

    count = _read(decision_client, act_as, pack_id, Role.ANALYST, "u-riyer")
    assert count.json()["cast_count"] == 1


@pytest.mark.parametrize("blank", ["", "   ", "\n\t"])
def test_a_vote_without_a_rationale_is_refused_and_not_counted(
    decision_client: TestClient,
    act_as: Callable[[Role, str], None],
    released_pack: Callable[..., dict[str, Any]],
    cast_vote: Callable[..., Any],
    members: dict[str, str],
    blank: str,
) -> None:
    pack_id = released_pack("FCRM-2026-0151")["pack_id"]

    refused = cast_vote(pack_id, members["okafor"], "approve", rationale=blank)
    assert refused.status_code == 422

    count = _read(decision_client, act_as, pack_id, Role.ANALYST, "u-riyer")
    assert count.json()["cast_count"] == 0
    # The member may still vote properly afterwards: the refusal burned nothing.
    assert cast_vote(pack_id, members["okafor"], "approve").status_code == 201


def test_a_vote_with_the_rationale_field_missing_entirely_is_a_422(
    decision_client: TestClient,
    act_as: Callable[[Role, str], None],
    released_pack: Callable[..., dict[str, Any]],
    members: dict[str, str],
) -> None:
    pack_id = released_pack("FCRM-2026-0151")["pack_id"]
    act_as(Role.COMMITTEE, members["okafor"])
    response = decision_client.post(f"/packs/{pack_id}/votes", json={"vote": "approve"})
    assert response.status_code == 422
    count = _read(decision_client, act_as, pack_id, Role.ANALYST, "u-riyer")
    assert count.json()["cast_count"] == 0


def test_a_1_1_1_split_reveals_round_one_then_opens_a_fresh_hidden_round(
    decision_client: TestClient,
    act_as: Callable[[Role, str], None],
    released_pack: Callable[..., dict[str, Any]],
    cast_vote: Callable[..., Any],
    members: dict[str, str],
) -> None:
    """A three-way split is never resolved to any outcome (ADR-08, ADR-31):
    the round's votes are revealed, no outcome is set, and a new round opens in
    which votes are hidden again until all three are re-cast.
    """
    pack_id = released_pack("FCRM-2026-0151")["pack_id"]
    cast_vote(pack_id, members["okafor"], "approve")
    cast_vote(pack_id, members["mendes"], "defer")
    split = cast_vote(pack_id, members["chen"], "reject")
    assert split.status_code == 201
    assert split.json()["round"] == 1

    act_as(Role.ANALYST, "u-riyer")
    round_one = decision_client.get(
        f"/packs/{pack_id}/votes", params={"round": 1}
    ).json()
    assert round_one["outcome"] == "no_majority"
    assert round_one["tally"] == {"approve": 1, "defer": 1, "reject": 1}
    assert (
        decision_client.get(f"/packs/{pack_id}/outcome").json()["no_majority"] is True
    )

    # Round 2 is the current round: empty and hidden again.
    current = decision_client.get(f"/packs/{pack_id}/votes").json()
    assert (current["round"], current["cast_count"], current["revealed"]) == (
        2,
        0,
        False,
    )

    first_again = cast_vote(pack_id, members["okafor"], "approve")
    assert first_again.status_code == 201
    assert first_again.json()["round"] == 2
    assert first_again.json()["cast_n_of_3"] == 1
    _assert_no_peer_content(first_again.text)
