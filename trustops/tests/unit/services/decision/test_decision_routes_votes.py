"""S5's vote routes over HTTP, with no Mongo: hidden votes, the tally, the
no-majority re-vote.

Product rules proved here, not just implied:

- **R2, hidden until all three are cast.** Before the third vote no response
  carries a peer's choice or rationale (asserted on the raw text, not only on
  the parsed fields); the caller always sees their own vote; the third vote
  reveals and tallies together.
- **ADR-08 / ADR-31.** Two identical votes decide; the golden demo (two
  approve-with-conditions, one defer) is approve with conditions; a 1-1-1 split
  is ``no_majority``, round two opens on the same pack and nothing is ever
  mapped to ``defer``.
- A vote needs a rationale, comes only from the verified caller, and a member
  votes once per round.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from fcrm_contracts.enums import Role
from fcrm_decision.store_memory import InMemoryDecisionStore

ActAs = Callable[[Role, str], None]
CASE = "FCRM-2026-0151"
ANALYST_SUB = "u-riyer"
SERVICE_SUB = "svc:caseflow"
OKAFOR, MENDES, CHEN = "u-jokafor", "u-amendes", "u-lchen"
SEATS = (OKAFOR, MENDES, CHEN)

RATIONALES = {
    OKAFOR: "Exposure is bounded and the conditions close the evidence gap.",
    MENDES: "Channel risk is real but the conditions address it directly.",
    CHEN: "The corridor review is not yet complete, so I would defer.",
}
OPTION_WORDS = ("approve", "defer", "reject")


def _get_votes(
    client: TestClient, act_as: ActAs, pack_id: str, role: Role, sub: str, **params: Any
) -> Any:
    act_as(role, sub)
    return client.get(f"/packs/{pack_id}/votes", params=params)


def _assert_no_vote_content(text: str) -> None:
    """Nothing in ``text`` could be a peer's choice or rationale."""
    lowered = text.lower()
    for word in OPTION_WORDS:
        assert word not in lowered, f"{word!r} leaked before the reveal"
    for rationale in RATIONALES.values():
        assert rationale not in text


def _cast_three(
    cast_vote: Callable[..., Any], pack_id: str, options: tuple[str, str, str]
) -> list[Any]:
    return [
        cast_vote(pack_id, member, option)
        for member, option in zip(SEATS, options, strict=True)
    ]


# ── the golden demo ──────────────────────────────────────────────────────


def test_votes_golden_demo_stay_hidden_until_the_third_then_reveal_together(
    decision_client: TestClient,
    act_as: ActAs,
    released_pack: Callable[..., dict[str, Any]],
    cast_vote: Callable[..., Any],
) -> None:
    pack_id = released_pack(CASE)["pack_id"]

    first = cast_vote(pack_id, OKAFOR, "approve_with_conditions", RATIONALES[OKAFOR])
    assert first.status_code == 201, first.text
    assert set(first.json()) == {"vote_id", "round", "cast_n_of_3"}  # no option echoed
    assert first.json()["round"] == 1
    assert first.json()["cast_n_of_3"] == 1

    # Okafor sees only his own choice; Mendes (who has not voted) sees none.
    own = _get_votes(decision_client, act_as, pack_id, Role.COMMITTEE, OKAFOR).json()
    assert own["revealed"] is False
    assert own["cast_count"] == 1
    assert own["mine"]["choice"] == "approve_with_conditions"
    assert [m["is_you"] for m in own["members"]] == [False, True, False]
    assert own["tally"] is None
    assert own["outcome"] is None
    peer = _get_votes(decision_client, act_as, pack_id, Role.COMMITTEE, MENDES)
    assert peer.json()["mine"] is None
    _assert_no_vote_content(peer.text)

    second = cast_vote(pack_id, MENDES, "approve_with_conditions", RATIONALES[MENDES])
    assert second.json()["cast_n_of_3"] == 2
    analyst = _get_votes(decision_client, act_as, pack_id, Role.ANALYST, ANALYST_SUB)
    assert analyst.json()["cast_count"] == 2
    assert analyst.json()["mine"] is None
    assert sorted(m["status"] for m in analyst.json()["members"]) == [
        "not_voted",
        "voted",
        "voted",
    ]
    _assert_no_vote_content(analyst.text)
    act_as(Role.COMMITTEE, CHEN)  # Chen has not voted yet either
    chen = decision_client.get(f"/packs/{pack_id}/votes")
    _assert_no_vote_content(chen.text)
    act_as(Role.COMMITTEE, MENDES)
    assert decision_client.get(f"/packs/{pack_id}/outcome").status_code == 409

    third = cast_vote(pack_id, CHEN, "defer", RATIONALES[CHEN])
    assert third.status_code == 201
    assert third.json()["cast_n_of_3"] == 3
    assert set(third.json()) == {"vote_id", "round", "cast_n_of_3"}

    revealed = _get_votes(decision_client, act_as, pack_id, Role.ANALYST, ANALYST_SUB)
    body = revealed.json()
    assert body["revealed"] is True
    assert body["cast_count"] == 3
    assert body["tally"] == {"approve_with_conditions": 2, "defer": 1}
    assert body["outcome"] == "approve_with_conditions"
    assert body["outcome_label"] == "Approved with conditions"
    seen = {m["name"]: (m["choice"], m["rationale"]) for m in body["members"]}
    assert seen == {
        "Ana Mendes": ("approve_with_conditions", RATIONALES[MENDES]),
        "Joseph Okafor": ("approve_with_conditions", RATIONALES[OKAFOR]),
        "Lin Chen": ("defer", RATIONALES[CHEN]),
    }
    decided = decision_client.get(f"/packs/{pack_id}/outcome").json()
    assert decided["outcome"] == "approve_with_conditions"
    assert decided["no_majority"] is False
    case_outcome = decision_client.get(f"/cases/{CASE}/outcome").json()
    assert case_outcome["outcome"] == "approve_with_conditions"
    assert case_outcome["mode"] == "committee"
    assert case_outcome["recorded"] is False  # H1 has not recorded it yet


def test_votes_after_the_tally_the_pack_is_decided_and_takes_no_more(
    decision_client: TestClient,
    act_as: ActAs,
    released_pack: Callable[..., dict[str, Any]],
    cast_vote: Callable[..., Any],
) -> None:
    pack_id = released_pack(CASE)["pack_id"]
    _cast_three(cast_vote, pack_id, ("approve_with_conditions",) * 2 + ("defer",))

    for late_member in ("u-a-fourth-member", CHEN):
        late = cast_vote(pack_id, late_member, "reject")
        assert late.status_code == 409
        assert late.json()["type"].endswith("/pack_decided")
    act_as(Role.ANALYST, ANALYST_SUB)
    assert decision_client.get(f"/packs/{pack_id}").json()["status"] == "decided"
    assert (
        decision_client.get(f"/packs/{pack_id}/outcome").json()["outcome"]
        == "approve_with_conditions"
    )


# ── what is never revealed early ─────────────────────────────────────────


def test_votes_nothing_leaks_before_the_reveal_in_responses_events_or_logs(
    decision_client: TestClient,
    act_as: ActAs,
    released_pack: Callable[..., dict[str, Any]],
    cast_vote: Callable[..., Any],
    decision_store: InMemoryDecisionStore,
    caplog: pytest.LogCaptureFixture,
) -> None:
    pack_id = released_pack(CASE)["pack_id"]
    caplog.set_level(logging.DEBUG)

    receipts = [
        cast_vote(pack_id, OKAFOR, "reject", RATIONALES[OKAFOR]),
        cast_vote(pack_id, MENDES, "defer", RATIONALES[MENDES]),
    ]
    responses = [r.text for r in receipts]
    for role, sub in (
        (Role.ANALYST, ANALYST_SUB),
        (Role.SERVICE, SERVICE_SUB),
        (Role.COMMITTEE, CHEN),
    ):
        responses.append(_get_votes(decision_client, act_as, pack_id, role, sub).text)
    act_as(Role.COMMITTEE, CHEN)
    responses.append(decision_client.get("/committee/queue").text)
    responses.append(decision_client.get(f"/packs/{pack_id}").text)
    responses.append(decision_client.get(f"/packs/{pack_id}/threads").text)

    # 1. responses: not a word of either vote, not the rationales.
    for text in responses:
        assert RATIONALES[OKAFOR] not in text
        assert RATIONALES[MENDES] not in text
    for text in responses[:5]:
        _assert_no_vote_content(text)

    # 2. events and 3. outbox rows (the in-memory outbox is the event list).
    pre_reveal = [e for e in decision_store.events if e.type != "pack.released"]
    assert [e.type for e in pre_reveal] == ["vote.cast", "vote.cast"]
    for event in pre_reveal:
        raw = event.model_dump_json()
        assert RATIONALES[OKAFOR] not in raw
        assert RATIONALES[MENDES] not in raw
        assert '"reject"' not in raw
        assert '"defer"' not in raw
        assert set(event.payload) == {
            "pack_id",
            "round",
            "member_id",
            "cast_count",
            "quorum",
        }

    # 4. log lines: counts, never content.
    assert any(r.getMessage() == "vote recorded" for r in caplog.records)
    for record in caplog.records:
        text = record.getMessage() + repr(record.__dict__)
        assert RATIONALES[OKAFOR] not in text
        assert RATIONALES[MENDES] not in text
        assert "'reject'" not in text
        assert "'defer'" not in text


def test_votes_the_third_vote_is_the_first_event_to_carry_the_options(
    act_as: ActAs,
    released_pack: Callable[..., dict[str, Any]],
    cast_vote: Callable[..., Any],
    decision_store: InMemoryDecisionStore,
) -> None:
    pack_id = released_pack(CASE)["pack_id"]
    _cast_three(cast_vote, pack_id, ("approve_with_conditions",) * 2 + ("defer",))

    types = [e.type for e in decision_store.events]
    assert types == [
        "pack.released",
        "vote.cast",
        "vote.cast",
        "vote.cast",
        "vote.tallied",
    ]
    tallied = decision_store.events[-1].payload
    assert tallied["vote_outcome"] == "approve_with_conditions"
    assert tallied["counts"] == {"approve_with_conditions": 2, "defer": 1}
    assert {v["member_id"]: v["vote"] for v in tallied["votes"]} == {
        OKAFOR: "approve_with_conditions",
        MENDES: "approve_with_conditions",
        CHEN: "defer",
    }


# ── refusals ─────────────────────────────────────────────────────────────


def test_votes_a_member_cannot_vote_twice_to_change_a_hidden_vote(
    decision_client: TestClient,
    act_as: ActAs,
    released_pack: Callable[..., dict[str, Any]],
    cast_vote: Callable[..., Any],
) -> None:
    pack_id = released_pack(CASE)["pack_id"]
    assert cast_vote(pack_id, OKAFOR, "reject").status_code == 201

    again = cast_vote(pack_id, OKAFOR, "approve")

    assert again.status_code == 409
    assert again.json()["type"].endswith("/already_voted")
    assert "reject" not in again.text  # neither the earlier vote nor the attempt
    count = _get_votes(decision_client, act_as, pack_id, Role.ANALYST, ANALYST_SUB)
    assert count.json()["cast_count"] == 1


@pytest.mark.parametrize("rationale", ["", "   ", "\n\t", "too short", " " * 30])
def test_votes_without_a_real_rationale_are_refused_and_not_counted(
    decision_client: TestClient,
    act_as: ActAs,
    released_pack: Callable[..., dict[str, Any]],
    cast_vote: Callable[..., Any],
    rationale: str,
) -> None:
    pack_id = released_pack(CASE)["pack_id"]

    refused = cast_vote(pack_id, OKAFOR, "approve", rationale=rationale)

    assert refused.status_code == 422
    count = _get_votes(decision_client, act_as, pack_id, Role.ANALYST, ANALYST_SUB)
    assert count.json()["cast_count"] == 0
    # The refusal burned nothing: the member may still vote properly.
    assert cast_vote(pack_id, OKAFOR, "approve").status_code == 201


def test_votes_with_the_rationale_missing_entirely_is_a_422(
    decision_client: TestClient,
    act_as: ActAs,
    released_pack: Callable[..., dict[str, Any]],
) -> None:
    pack_id = released_pack(CASE)["pack_id"]
    act_as(Role.COMMITTEE, OKAFOR)
    response = decision_client.post(f"/packs/{pack_id}/votes", json={"vote": "approve"})
    assert response.status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {"vote": "maybe", "rationale": "x" * 30},
        {"vote": "approve", "rationale": "x" * 30, "member_id": "u-lchen"},
        {"vote": "approve", "rationale": "x" * 30, "round": 1},
        {"option": "approve", "rationale": "x" * 30},
    ],
)
def test_votes_the_body_names_only_the_vote_and_the_rationale(
    decision_client: TestClient,
    act_as: ActAs,
    released_pack: Callable[..., dict[str, Any]],
    body: dict[str, Any],
) -> None:
    """Identity and round come from the token and the pack, never the body."""
    pack_id = released_pack(CASE)["pack_id"]
    act_as(Role.COMMITTEE, OKAFOR)
    assert decision_client.post(f"/packs/{pack_id}/votes", json=body).status_code == 422


def test_votes_on_a_pack_that_was_never_released_are_409(
    build_pack: Callable[..., dict[str, Any]], cast_vote: Callable[..., Any]
) -> None:
    pack_id = build_pack(CASE)["pack_id"]
    refused = cast_vote(pack_id, OKAFOR, "approve")
    assert refused.status_code == 409
    assert refused.json()["type"].endswith("/pack_not_released")


def test_votes_on_an_unknown_pack_are_404(cast_vote: Callable[..., Any]) -> None:
    assert cast_vote("pack_nope", OKAFOR, "approve").status_code == 404


def test_votes_a_superseded_pack_takes_no_vote_and_shows_none(
    decision_client: TestClient,
    act_as: ActAs,
    released_pack: Callable[..., dict[str, Any]],
    cast_vote: Callable[..., Any],
) -> None:
    pack_id = released_pack(CASE)["pack_id"]
    cast_vote(pack_id, OKAFOR, "approve")
    act_as(Role.SERVICE, SERVICE_SUB)
    decision_client.post(
        f"/packs/{pack_id}/supersede", json={"reason": "a number changed, unlocked"}
    )

    refused = cast_vote(pack_id, MENDES, "approve")

    assert refused.status_code == 409
    assert refused.json()["type"].endswith("/pack_superseded")
    shown = _get_votes(decision_client, act_as, pack_id, Role.ANALYST, ANALYST_SUB)
    assert shown.json()["cast_count"] == 0  # voided (kept), not counted


def test_votes_get_of_a_round_the_pack_has_not_reached_is_404(
    decision_client: TestClient,
    act_as: ActAs,
    released_pack: Callable[..., dict[str, Any]],
) -> None:
    pack_id = released_pack(CASE)["pack_id"]
    missing = _get_votes(
        decision_client, act_as, pack_id, Role.ANALYST, ANALYST_SUB, round=2
    )
    assert missing.status_code == 404
    zero = _get_votes(
        decision_client, act_as, pack_id, Role.ANALYST, ANALYST_SUB, round=0
    )
    assert zero.status_code == 422


# ── ADR-31: a 1-1-1 split is no majority, never defer ────────────────────

SPLITS = [
    ("approve", "approve_with_conditions", "defer"),
    ("approve", "approve_with_conditions", "reject"),
    ("approve", "defer", "reject"),
    ("approve_with_conditions", "defer", "reject"),
]


@pytest.mark.parametrize("split", SPLITS)
def test_votes_a_three_way_split_is_no_majority_and_opens_round_two(
    decision_client: TestClient,
    act_as: ActAs,
    released_pack: Callable[..., dict[str, Any]],
    cast_vote: Callable[..., Any],
    decision_store: InMemoryDecisionStore,
    split: tuple[str, str, str],
) -> None:
    pack_id = released_pack(CASE)["pack_id"]

    receipts = _cast_three(cast_vote, pack_id, split)

    assert [r.status_code for r in receipts] == [201, 201, 201]
    assert receipts[2].json()["round"] == 1
    # Round 1 is kept and revealed; nothing was decided, certainly not defer.
    one = _get_votes(
        decision_client, act_as, pack_id, Role.ANALYST, ANALYST_SUB, round=1
    )
    body = one.json()
    assert body["revealed"] is True
    assert body["outcome"] == "no_majority"
    assert body["outcome_label"] == "No majority · re-vote requested (ADR-08)"
    assert body["tally"] == {option: 1 for option in split}
    assert sorted(m["choice"] for m in body["members"]) == sorted(split)
    verdict = decision_client.get(f"/packs/{pack_id}/outcome").json()
    assert verdict["no_majority"] is True
    assert verdict["outcome"] is None
    assert decision_client.get(f"/cases/{CASE}/outcome").status_code == 404
    assert decision_client.get(f"/packs/{pack_id}").json()["status"] == "released"
    # Round 2 is the current round: empty and hidden again.
    current = _get_votes(
        decision_client, act_as, pack_id, Role.ANALYST, ANALYST_SUB
    ).json()
    assert current["round"] == 2
    assert current["cast_count"] == 0
    assert current["revealed"] is False
    assert current["outcome"] is None
    # And exactly the events ADR-31 names, in order.
    types = [e.type for e in decision_store.events]
    assert types[-2:] == ["vote.no_majority", "vote.rerequested"]
    assert "vote.tallied" not in types
    again = decision_store.events[-1].payload
    assert again["round"] == 2
    assert again["reason"] == "no_majority"
    assert sorted(again["member_ids"]) == sorted(SEATS)


def test_votes_round_two_votes_again_hidden_and_can_decide(
    decision_client: TestClient,
    act_as: ActAs,
    released_pack: Callable[..., dict[str, Any]],
    cast_vote: Callable[..., Any],
) -> None:
    pack_id = released_pack(CASE)["pack_id"]
    _cast_three(cast_vote, pack_id, ("approve", "defer", "reject"))

    first_again = cast_vote(pack_id, OKAFOR, "approve_with_conditions")
    assert first_again.status_code == 201  # round 1 vote did not block round 2
    assert first_again.json()["round"] == 2
    assert first_again.json()["cast_n_of_3"] == 1
    _assert_no_vote_content(
        _get_votes(decision_client, act_as, pack_id, Role.ANALYST, ANALYST_SUB).text
    )
    cast_vote(pack_id, MENDES, "approve_with_conditions")
    third = cast_vote(pack_id, CHEN, "defer")
    assert third.json()["round"] == 2

    now = _get_votes(decision_client, act_as, pack_id, Role.ANALYST, ANALYST_SUB).json()
    assert now["round"] == 2
    assert now["outcome"] == "approve_with_conditions"
    # Round 1 is still there, still a split.
    one = _get_votes(
        decision_client, act_as, pack_id, Role.ANALYST, ANALYST_SUB, round=1
    )
    assert one.json()["outcome"] == "no_majority"


def test_votes_a_split_opens_a_system_thread_for_the_discussion(
    decision_client: TestClient,
    act_as: ActAs,
    released_pack: Callable[..., dict[str, Any]],
    cast_vote: Callable[..., Any],
) -> None:
    pack_id = released_pack(CASE)["pack_id"]
    _cast_three(cast_vote, pack_id, ("approve", "defer", "reject"))

    act_as(Role.COMMITTEE, MENDES)
    threads = decision_client.get(f"/packs/{pack_id}/threads").json()["items"]

    assert len(threads) == 1
    assert threads[0]["from"] == "S5 decision"
    assert threads[0]["question"].startswith(
        "No majority: 1 approve / 1 defer / 1 reject"
    )
    assert threads[0]["answer"] is None


# ── the approve / approve-with-conditions refinement (proposed) ─────────


@pytest.mark.parametrize(
    ("ballot", "expected"),
    [
        (("approve", "approve", "approve_with_conditions"), "approve_with_conditions"),
        (
            ("approve_with_conditions", "approve_with_conditions", "approve"),
            "approve_with_conditions",
        ),
        (("approve", "approve", "defer"), "approve"),
        (("reject", "reject", "approve_with_conditions"), "reject"),
        (("defer", "defer", "defer"), "defer"),
    ],
)
def test_votes_two_identical_votes_decide_with_the_approve_family_refinement(
    decision_client: TestClient,
    act_as: ActAs,
    released_pack: Callable[..., dict[str, Any]],
    cast_vote: Callable[..., Any],
    ballot: tuple[str, str, str],
    expected: str,
) -> None:
    pack_id = released_pack(CASE)["pack_id"]
    _cast_three(cast_vote, pack_id, ballot)

    shown = _get_votes(decision_client, act_as, pack_id, Role.ANALYST, ANALYST_SUB)

    assert shown.json()["outcome"] == expected
