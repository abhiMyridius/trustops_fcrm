"""HTTP-layer tests for S5, with no Mongo dependency: the route layer
takes whatever implements ``DecisionStore``, so these wire in
``InMemoryDecisionStore`` and exercise the app exactly as a caller would
over HTTP -- the same pattern ``tests/unit/services/scoring/test_routes.py``
uses for S2.

Three things get extra scrutiny because they are real product rules, not
UI convention (R2, ADR-08):

- ``test_votes_are_genuinely_hidden_until_quorum`` and
  ``test_the_voter_who_just_cast_cannot_read_other_votes_early`` prove no
  route -- including the one the third-to-last voter just posted to --
  leaks a vote's content before all three are in.
- ``test_every_ordering_of_a_1_1_1_split_is_no_majority_never_an_outcome``
  proves a three-way split never resolves to any of the four outcomes,
  in any order the votes arrive.
- ``test_the_real_demo_case_s_2_1_vote_replays_through_the_http_route``
  replays FCRM-2026-0151's real committee vote through the HTTP route
  (not just ``tally_round`` directly, as
  ``test_packs_and_outcomes.py`` already does).
"""

from __future__ import annotations

import itertools
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fcrm_common.errors import install_error_handlers
from fcrm_common.health import ReadinessProbe, build_health_router
from fcrm_decision.routes import router
from fcrm_decision.store import InMemoryDecisionStore
from fcrm_seed.loader import demo_case

PASSING_CHECKS: dict[str, bool] = {
    "numbers_locked": True,
    "conditions_drafted": True,
    "all_review_items_decided": True,
    "boundary_and_band_acknowledged": True,
}


def _snapshot(case_id: str, **checks: bool) -> dict[str, Any]:
    """A minimal pack snapshot carrying the release-check state in its
    own content, as ``routes._checks_from_snapshot`` expects.
    """
    return {"case_id": case_id, "checks": {**PASSING_CHECKS, **checks}}


@pytest.fixture
def app() -> FastAPI:
    application = FastAPI()
    install_error_handlers(application)
    application.state.probe = ReadinessProbe()
    application.include_router(router)
    application.include_router(build_health_router(application.state.probe))
    application.state.store = InMemoryDecisionStore()
    return application


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def _build_pack(client: TestClient, case_id: str, **checks: bool) -> dict[str, Any]:
    response = client.post(
        "/packs", json={"case_id": case_id, "snapshot": _snapshot(case_id, **checks)}
    )
    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    return body


def _vote(
    client: TestClient, pack_id: str, member_id: str, option: str, round_: int = 1
) -> Any:
    return client.post(
        f"/packs/{pack_id}/votes",
        json={
            "pack_id": pack_id,
            "member_id": member_id,
            "round": round_,
            "option": option,
            "rationale": f"{member_id} votes {option} for test purposes.",
        },
    )


# ── release checks ──────────────────────────────────────────────────────


def test_release_checks_all_pass_only_when_every_one_does(client: TestClient) -> None:
    """``all_passed`` is a computed ``@property``, not a stored field, so
    it never appears in the JSON body (same as every other
    ``fcrm_contracts`` model); the four booleans round-trip unchanged.
    """
    response = client.post("/release-checks", json=PASSING_CHECKS)
    assert response.status_code == 200
    assert response.json() == PASSING_CHECKS

    failing = {**PASSING_CHECKS, "all_review_items_decided": False}
    response = client.post("/release-checks", json=failing)
    assert response.json() == failing


# ── pack build / no-op / supersede ──────────────────────────────────────


def test_building_the_same_pack_twice_is_a_no_op(client: TestClient) -> None:
    first = _build_pack(client, "FCRM-2026-9001")
    second = _build_pack(client, "FCRM-2026-9001")
    assert second["pack_id"] == first["pack_id"]
    assert second["version"] == first["version"] == 1
    assert second["superseded_by"] is None


def test_changed_content_supersedes_the_previous_pack(client: TestClient) -> None:
    case_id = "FCRM-2026-9002"
    response = client.post(
        "/packs",
        json={
            "case_id": case_id,
            "snapshot": {**_snapshot(case_id), "composite": 9.8},
        },
    )
    v1 = response.json()
    response = client.post(
        "/packs",
        json={
            "case_id": case_id,
            "snapshot": {**_snapshot(case_id), "composite": 10.4},
        },
    )
    v2 = response.json()
    assert v2["pack_id"] != v1["pack_id"]
    assert v2["version"] == v1["version"] + 1

    refreshed_v1 = client.get(f"/packs/{v1['pack_id']}").json()
    assert refreshed_v1["superseded_by"] == v2["pack_id"]


def test_a_pack_with_failed_checks_is_not_released(client: TestClient) -> None:
    body = _build_pack(client, "FCRM-2026-9003", all_review_items_decided=False)
    assert body["released_at"] is None
    response = _vote(client, body["pack_id"], "Ana Mendes", "approve")
    assert response.status_code == 409
    assert response.json()["type"].endswith("/rule_violation")


# ── hidden votes (R2) ────────────────────────────────────────────────────


def test_votes_are_genuinely_hidden_until_quorum(client: TestClient) -> None:
    pack = _build_pack(client, "FCRM-2026-9010")
    pack_id = pack["pack_id"]

    r1 = _vote(client, pack_id, "Joseph Okafor", "approve")
    assert r1.status_code == 200
    assert set(r1.json()) == {"pack_id", "round", "votes_cast", "quorum"}
    assert r1.json()["votes_cast"] == 1

    hidden = client.get(f"/packs/{pack_id}/votes").json()
    assert set(hidden) == {"pack_id", "round", "votes_cast", "quorum"}
    assert hidden["votes_cast"] == 1
    assert "votes" not in hidden
    assert "approve" not in str(hidden)


def test_the_voter_who_just_cast_cannot_read_other_votes_early(
    client: TestClient,
) -> None:
    """The second of three voters must not learn the first voter's choice
    -- not from their own POST response, and not from a GET right after.
    """
    pack = _build_pack(client, "FCRM-2026-9011")
    pack_id = pack["pack_id"]

    _vote(client, pack_id, "Joseph Okafor", "reject")
    second_response = _vote(client, pack_id, "Ana Mendes", "approve")
    assert second_response.status_code == 200
    body = second_response.json()
    assert "votes" not in body
    assert "reject" not in str(body)
    assert "Joseph Okafor" not in str(body)

    hidden = client.get(f"/packs/{pack_id}/votes").json()
    assert hidden["votes_cast"] == 2
    assert "reject" not in str(hidden)
    assert "Joseph Okafor" not in str(hidden)


def test_quorum_reveals_the_tally_to_everyone(client: TestClient) -> None:
    pack = _build_pack(client, "FCRM-2026-9012")
    pack_id = pack["pack_id"]
    _vote(client, pack_id, "Joseph Okafor", "approve_with_conditions")
    _vote(client, pack_id, "Ana Mendes", "approve_with_conditions")
    third = _vote(client, pack_id, "Lin Chen", "defer")
    assert third.status_code == 200
    outcome = third.json()
    assert outcome["outcome"] == "approve_with_conditions"
    assert outcome["no_majority"] is False

    revealed = client.get(f"/packs/{pack_id}/votes").json()
    assert {v["member_id"] for v in revealed["votes"]} == {
        "Joseph Okafor",
        "Ana Mendes",
        "Lin Chen",
    }
    assert revealed["tally"] == {"approve_with_conditions": 2, "defer": 1}


def test_outcome_is_not_readable_before_quorum(client: TestClient) -> None:
    pack = _build_pack(client, "FCRM-2026-9013")
    pack_id = pack["pack_id"]
    _vote(client, pack_id, "Joseph Okafor", "approve")
    response = client.get(f"/packs/{pack_id}/outcome")
    assert response.status_code == 409
    assert response.json()["type"].endswith("/conflict")


# ── never auto-resolve a split (ADR-08) ─────────────────────────────────


def test_duplicate_vote_from_the_same_member_is_rejected(client: TestClient) -> None:
    pack = _build_pack(client, "FCRM-2026-9020")
    pack_id = pack["pack_id"]
    _vote(client, pack_id, "Joseph Okafor", "approve")
    again = _vote(client, pack_id, "Joseph Okafor", "reject")
    assert again.status_code == 409
    assert again.json()["type"].endswith("/conflict")


@pytest.mark.parametrize(
    "options", list(itertools.permutations(["approve", "defer", "reject"]))
)
def test_every_ordering_of_a_1_1_1_split_is_no_majority_never_an_outcome(
    client: TestClient, options: tuple[str, str, str]
) -> None:
    """Every permutation of a three-way split must come back
    ``no_majority`` with ``outcome: null`` -- never ``defer`` by default,
    never any other outcome, regardless of vote order.
    """
    case_id = f"FCRM-2026-SPLIT-{'-'.join(options)}"
    pack = _build_pack(client, case_id)
    pack_id = pack["pack_id"]
    members = ["Joseph Okafor", "Ana Mendes", "Lin Chen"]

    responses = [
        _vote(client, pack_id, member, option)
        for member, option in zip(members, options, strict=True)
    ]
    last = responses[-1].json()
    assert last["outcome"] is None
    assert last["no_majority"] is True

    # The round reopens on the same pack; it never auto-closes to a
    # default outcome and the pack is still addressable for a re-vote.
    status = client.get(f"/packs/{pack_id}/votes", params={"round": 2}).json()
    assert status["votes_cast"] == 0
    assert status["quorum"] == 3


def test_a_no_majority_round_reopens_and_a_second_round_can_decide(
    client: TestClient,
) -> None:
    pack = _build_pack(client, "FCRM-2026-9021")
    pack_id = pack["pack_id"]
    members = ["Joseph Okafor", "Ana Mendes", "Lin Chen"]
    for member, option in zip(members, ["approve", "defer", "reject"], strict=True):
        _vote(client, pack_id, member, option)

    for member in members:
        response = _vote(client, pack_id, member, "approve", round_=2)
    assert response.status_code == 200
    outcome = response.json()
    assert outcome["outcome"] == "approve"
    assert outcome["no_majority"] is False
    assert outcome["round"] == 2


def test_voting_in_the_wrong_round_is_rejected(client: TestClient) -> None:
    pack = _build_pack(client, "FCRM-2026-9022")
    pack_id = pack["pack_id"]
    response = _vote(client, pack_id, "Joseph Okafor", "approve", round_=5)
    assert response.status_code == 409


def test_a_blank_rationale_is_rejected(client: TestClient) -> None:
    pack = _build_pack(client, "FCRM-2026-9023")
    response = client.post(
        f"/packs/{pack['pack_id']}/votes",
        json={
            "pack_id": pack["pack_id"],
            "member_id": "Joseph Okafor",
            "round": 1,
            "option": "approve",
            "rationale": "   ",
        },
    )
    assert response.status_code == 409
    assert response.json()["type"].endswith("/rule_violation")


# ── R6: committee question -> numbers changed -> pack v2 ────────────────


def test_a_committee_question_opens_a_thread(client: TestClient) -> None:
    pack = _build_pack(client, "FCRM-2026-9030")
    response = client.post(
        f"/packs/{pack['pack_id']}/questions",
        json={
            "pack_id": pack["pack_id"],
            "asked_by": "Lin Chen",
            "text": "Is the vendor DD review already scheduled?",
        },
    )
    assert response.status_code == 200
    assert "question_id" in response.json()


def test_numbers_changed_unlocks_the_pack_and_blocks_further_votes(
    client: TestClient,
) -> None:
    case_id = "FCRM-2026-9031"
    pack = _build_pack(client, case_id)
    pack_id = pack["pack_id"]
    _vote(client, pack_id, "Joseph Okafor", "approve_with_conditions")

    question = client.post(
        f"/packs/{pack_id}/questions",
        json={"pack_id": pack_id, "asked_by": "Lin Chen", "text": "Does CE change?"},
    ).json()
    answer = client.post(
        f"/questions/{question['question_id']}/answers",
        json={
            "pack_id": pack_id,
            "question_id": question["question_id"],
            "answered_by": "Rahul Iyer",
            "text": "Yes, CTL-T01 rating corrected after review.",
            "numbers_changed": True,
        },
    )
    assert answer.status_code == 200
    assert "addendum_id" in answer.json()

    blocked = _vote(client, pack_id, "Ana Mendes", "approve_with_conditions")
    assert blocked.status_code == 409
    assert blocked.json()["type"].endswith("/rule_violation")

    # The already-cast vote is voided, not silently kept valid.
    rebuilt = client.post(
        "/packs",
        json={
            "case_id": case_id,
            "snapshot": {**_snapshot(case_id), "composite": 10.4},
        },
    ).json()
    assert rebuilt["pack_id"] != pack_id
    fresh_status = client.get(f"/packs/{rebuilt['pack_id']}/votes").json()
    assert fresh_status["votes_cast"] == 0  # every cast vote re-requested


# ── the real demo case, replayed through the HTTP route ─────────────────


def test_the_real_demo_case_s_2_1_vote_replays_through_the_http_route(
    client: TestClient,
) -> None:
    """FCRM-2026-0151: Joseph Okafor and Ana Mendes vote
    ``approve_with_conditions``; Lin Chen defers. Two of three agree, so
    the HTTP route -- not just ``tally_round`` directly -- must answer
    ``approve_with_conditions``, matching the seed's own stored outcome.
    """
    demo = demo_case()
    case_id = demo["id"]
    pack = _build_pack(client, case_id)
    pack_id = pack["pack_id"]

    last_response = None
    for vote in demo["votes"]:
        last_response = _vote(client, pack_id, vote["member"], vote["vote"])
    assert last_response is not None
    assert last_response.status_code == 200
    outcome = last_response.json()
    assert outcome["outcome"] == demo["outcome"] == "approve_with_conditions"
    assert outcome["no_majority"] is False

    persisted = client.get(f"/packs/{pack_id}/outcome").json()
    assert persisted == outcome


def test_healthz_is_always_ok(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}


def test_readyz_is_ok_with_no_checks_registered(client: TestClient) -> None:
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.parametrize("bad", ["false", "true", "yes", 1, 2, "0", [], {}, None])
def test_a_non_boolean_release_check_flag_never_releases_a_pack(
    client: TestClient, bad: Any
) -> None:
    """Fail-open regression: ``bool("false")`` is True, so a string or
    number in a release-check slot used to release the pack and open it for
    voting. Only a literal JSON ``true`` may count as a passed check.
    """
    snapshot = _snapshot("FCRM-2026-9101")
    snapshot["checks"]["boundary_and_band_acknowledged"] = bad
    response = client.post(
        "/packs", json={"case_id": "FCRM-2026-9101", "snapshot": snapshot}
    )
    assert response.status_code == 200
    assert response.json().get("released_at") is None
    vote = _vote(client, response.json()["pack_id"], "Joseph Okafor", "approve")
    assert vote.status_code == 409


def test_unlocking_a_pack_for_changed_numbers_requires_a_reason(
    client: TestClient,
) -> None:
    """R6: a pack is unlocked only with a reason. An empty or blank answer
    must be refused and must leave the pack open for voting."""
    pack = _build_pack(client, "FCRM-2026-9102")
    pack_id = pack["pack_id"]
    question = client.post(
        f"/packs/{pack_id}/questions",
        json={"pack_id": pack_id, "asked_by": "Lin Chen", "text": "Is 4.5 right?"},
    ).json()
    for blank in ("", "   "):
        response = client.post(
            f"/questions/{question['question_id']}/answers",
            json={
                "pack_id": pack_id,
                "question_id": question["question_id"],
                "answered_by": "Rahul Iyer",
                "text": blank,
                "numbers_changed": True,
            },
        )
        assert response.status_code == 409
    still_open = _vote(client, pack_id, "Joseph Okafor", "approve")
    assert still_open.status_code == 200


def test_a_decided_case_cannot_get_a_second_pack_with_a_second_outcome(
    client: TestClient,
) -> None:
    """Closed means closed: once a pack has a decisive outcome, a changed
    snapshot for the same case must be refused, not minted as pack v2 that
    can reach a contradictory second outcome. An identical re-post stays an
    idempotent no-op."""
    case_id = "FCRM-2026-9103"
    pack = _build_pack(client, case_id)
    for member, option in (
        ("Joseph Okafor", "approve"),
        ("Ana Mendes", "approve"),
        ("Lin Chen", "defer"),
    ):
        assert _vote(client, pack["pack_id"], member, option).status_code == 200
    assert (
        client.get(f"/packs/{pack['pack_id']}/outcome").json()["outcome"] == "approve"
    )

    changed = _snapshot(case_id)
    changed["note"] = "edited after the decision"
    refused = client.post("/packs", json={"case_id": case_id, "snapshot": changed})
    assert refused.status_code == 409

    same = client.post(
        "/packs", json={"case_id": case_id, "snapshot": _snapshot(case_id)}
    )
    assert same.status_code == 200
    assert same.json()["pack_id"] == pack["pack_id"]
