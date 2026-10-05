"""S5's service routes over HTTP, with no Mongo: what H1 calls after an unlock
(supersede), a split (re-vote) and a decision (outcomes), and the
``Idempotency-Key`` every one of them honours (ADR-28: a step may run twice).

S5 records decisions people made; it makes none. A committee outcome is only
ever the tally's, a delegated one needs a person and a reason, and neither can
pre-empt the other.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from fcrm_contracts.enums import Role
from fcrm_decision.store_memory import InMemoryDecisionStore

ActAs = Callable[[Role, str], None]
CASE = "FCRM-2026-0151"
OTHER_CASE = "FCRM-2026-0140"
ANALYST_SUB = "u-riyer"
SERVICE_SUB = "svc:caseflow"
OKAFOR, MENDES, CHEN = "u-jokafor", "u-amendes", "u-lchen"
SEATS = (OKAFOR, MENDES, CHEN)
REASON = "The corridor review lowers the CTL-T01 rating, so the numbers change."
GOLDEN = ("approve_with_conditions", "approve_with_conditions", "defer")
CONDITIONS = [
    {"id": "CND-1", "text": "Corridor-scope vendor DD review completed and filed"},
    {"id": "CND-2", "text": "Enhanced monitoring rules on payout agents live"},
    {"id": "CND-3", "text": "Quarterly sample review of agent KYC files"},
]


def _service(act_as: ActAs) -> None:
    act_as(Role.SERVICE, SERVICE_SUB)


def _cast(cast_vote: Callable[..., Any], pack_id: str, ballot: tuple[str, ...]) -> None:
    for member, option in zip(SEATS, ballot, strict=True):
        assert cast_vote(pack_id, member, option).status_code == 201


def _committee_record(pack_id: str, **overrides: Any) -> dict[str, Any]:
    return {
        "case_id": CASE,
        "mode": "committee",
        "outcome": "approve_with_conditions",
        "pack_id": pack_id,
        "decided_by": "committee",
        "plain_summary": "Approved with three conditions.",
        "conditions": CONDITIONS,
        **overrides,
    }


def _delegated_record(**overrides: Any) -> dict[str, Any]:
    return {
        "case_id": OTHER_CASE,
        "mode": "delegated",
        "outcome": "approve",
        "decided_by": ANALYST_SUB,
        "reason": "Band 2: inside the analyst's delegated authority (R1).",
        "plain_summary": "Approved.",
        **overrides,
    }


# ── supersede ────────────────────────────────────────────────────────────


def test_supersede_voids_the_cast_votes_and_rerequests_them_for_the_next_version(
    decision_client: TestClient,
    act_as: ActAs,
    released_pack: Callable[..., dict[str, Any]],
    cast_vote: Callable[..., Any],
    decision_store: InMemoryDecisionStore,
) -> None:
    pack = released_pack(CASE)
    cast_vote(pack["pack_id"], OKAFOR, "approve")
    cast_vote(pack["pack_id"], MENDES, "defer")
    _service(act_as)

    response = decision_client.post(
        f"/packs/{pack['pack_id']}/supersede", json={"reason": REASON}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "superseded"
    assert body["voided_votes"] == 2
    assert sorted(body["rerequested"]) == [MENDES, OKAFOR]
    superseded, rerequested = decision_store.events[-2:]
    assert superseded.type == "pack.superseded"
    assert superseded.payload == {
        "pack_id": pack["pack_id"],
        "version": 1,
        "reason": REASON,
        "voided_votes": 2,
    }
    assert rerequested.type == "vote.rerequested"
    assert rerequested.payload["version"] == 2
    assert rerequested.payload["round"] == 1
    assert rerequested.payload["reason"] == REASON
    act_as(Role.ANALYST, ANALYST_SUB)
    view = decision_client.get(f"/packs/{pack['pack_id']}/votes").json()
    assert view["cast_count"] == 0
    assert (
        decision_client.get(f"/packs/{pack['pack_id']}").json()["status"]
        == "superseded"
    )


def test_supersede_a_pack_nobody_voted_on_announces_only_the_supersession(
    decision_client: TestClient,
    act_as: ActAs,
    released_pack: Callable[..., dict[str, Any]],
    decision_store: InMemoryDecisionStore,
) -> None:
    pack_id = released_pack(CASE)["pack_id"]
    _service(act_as)

    response = decision_client.post(
        f"/packs/{pack_id}/supersede", json={"reason": REASON}
    )

    assert response.json()["voided_votes"] == 0
    assert response.json()["rerequested"] == []
    assert [e.type for e in decision_store.events][-1] == "pack.superseded"


def test_supersede_an_unreleased_pack_is_allowed(
    decision_client: TestClient,
    act_as: ActAs,
    build_pack: Callable[..., dict[str, Any]],
) -> None:
    pack_id = build_pack(CASE)["pack_id"]
    _service(act_as)
    response = decision_client.post(
        f"/packs/{pack_id}/supersede", json={"reason": REASON}
    )
    assert response.status_code == 200


def test_supersede_twice_is_a_no_op_that_announces_once(
    decision_client: TestClient,
    act_as: ActAs,
    released_pack: Callable[..., dict[str, Any]],
    decision_store: InMemoryDecisionStore,
) -> None:
    pack_id = released_pack(CASE)["pack_id"]
    _service(act_as)
    decision_client.post(f"/packs/{pack_id}/supersede", json={"reason": REASON})
    count = len(decision_store.events)

    again = decision_client.post(f"/packs/{pack_id}/supersede", json={"reason": REASON})

    assert again.status_code == 200
    assert again.json()["voided_votes"] == 0
    assert len(decision_store.events) == count


def test_supersede_a_decided_pack_is_409(
    decision_client: TestClient,
    act_as: ActAs,
    released_pack: Callable[..., dict[str, Any]],
    cast_vote: Callable[..., Any],
) -> None:
    pack_id = released_pack(CASE)["pack_id"]
    _cast(cast_vote, pack_id, GOLDEN)
    _service(act_as)

    response = decision_client.post(
        f"/packs/{pack_id}/supersede", json={"reason": REASON}
    )

    assert response.status_code == 409
    assert response.json()["type"].endswith("/pack_decided")


@pytest.mark.parametrize("body", [{}, {"reason": "short"}, {"reason": "x" * 9}])
def test_supersede_needs_a_reason(
    decision_client: TestClient,
    act_as: ActAs,
    released_pack: Callable[..., dict[str, Any]],
    body: dict[str, str],
) -> None:
    pack_id = released_pack(CASE)["pack_id"]
    _service(act_as)
    assert (
        decision_client.post(f"/packs/{pack_id}/supersede", json=body).status_code
        == 422
    )


def test_supersede_an_unknown_pack_is_404(
    decision_client: TestClient, act_as: ActAs
) -> None:
    _service(act_as)
    response = decision_client.post(
        "/packs/pack_nope/supersede", json={"reason": REASON}
    )
    assert response.status_code == 404


# ── re-vote ──────────────────────────────────────────────────────────────


def test_revote_confirms_the_round_two_a_split_opened(
    decision_client: TestClient,
    act_as: ActAs,
    released_pack: Callable[..., dict[str, Any]],
    cast_vote: Callable[..., Any],
    decision_store: InMemoryDecisionStore,
) -> None:
    pack_id = released_pack(CASE)["pack_id"]
    _cast(cast_vote, pack_id, ("approve", "defer", "reject"))
    count = len(decision_store.events)
    _service(act_as)

    response = decision_client.post(
        f"/packs/{pack_id}/revote", json={"reason": "no_majority"}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["pack_id"], body["round"]) == (pack_id, 2)
    assert sorted(body["member_ids"]) == sorted(SEATS)
    assert len(decision_store.events) == count  # S5 announced it when it tallied


def test_revote_without_a_split_is_409(
    decision_client: TestClient,
    act_as: ActAs,
    released_pack: Callable[..., dict[str, Any]],
) -> None:
    pack_id = released_pack(CASE)["pack_id"]
    _service(act_as)
    response = decision_client.post(
        f"/packs/{pack_id}/revote", json={"reason": "no_majority"}
    )
    assert response.status_code == 409
    assert response.json()["type"].endswith("/no_revote_pending")


def test_revote_after_a_decision_is_409(
    decision_client: TestClient,
    act_as: ActAs,
    released_pack: Callable[..., dict[str, Any]],
    cast_vote: Callable[..., Any],
) -> None:
    pack_id = released_pack(CASE)["pack_id"]
    _cast(cast_vote, pack_id, GOLDEN)
    _service(act_as)
    response = decision_client.post(
        f"/packs/{pack_id}/revote", json={"reason": "no_majority"}
    )
    assert response.status_code == 409
    assert response.json()["type"].endswith("/pack_decided")


def test_revote_only_ever_confirms_no_majority(
    decision_client: TestClient,
    act_as: ActAs,
    released_pack: Callable[..., dict[str, Any]],
) -> None:
    pack_id = released_pack(CASE)["pack_id"]
    _service(act_as)
    response = decision_client.post(
        f"/packs/{pack_id}/revote", json={"reason": "defer"}
    )
    assert response.status_code == 422


# ── outcomes: committee ──────────────────────────────────────────────────


def test_outcomes_committee_records_exactly_what_the_tally_decided(
    decision_client: TestClient,
    act_as: ActAs,
    released_pack: Callable[..., dict[str, Any]],
    cast_vote: Callable[..., Any],
    decision_store: InMemoryDecisionStore,
) -> None:
    pack_id = released_pack(CASE)["pack_id"]
    _cast(cast_vote, pack_id, GOLDEN)
    _service(act_as)

    response = decision_client.post("/outcomes", json=_committee_record(pack_id))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["outcome"] == "approve_with_conditions"
    assert body["label"] == "Approved with conditions"
    assert body["mode"] == "committee"
    assert body["pack_id"] == pack_id
    assert body["recorded"] is True
    assert [c["id"] for c in body["conditions"]] == ["CND-1", "CND-2", "CND-3"]
    announced = decision_store.events[-1]
    assert announced.type == "outcome.set"
    assert announced.payload["mode"] == "committee"
    assert announced.payload["outcome"] == "approve_with_conditions"
    assert announced.payload["pack_id"] == pack_id
    assert len(announced.payload["conditions"]) == 3
    # GET /cases/{id}/outcome now carries what was recorded.
    act_as(Role.ANALYST, ANALYST_SUB)
    read = decision_client.get(f"/cases/{CASE}/outcome").json()
    assert read["recorded"] is True
    assert read["plain_summary"] == "Approved with three conditions."


def test_outcomes_committee_recording_it_again_announces_nothing_twice(
    decision_client: TestClient,
    act_as: ActAs,
    released_pack: Callable[..., dict[str, Any]],
    cast_vote: Callable[..., Any],
    decision_store: InMemoryDecisionStore,
) -> None:
    pack_id = released_pack(CASE)["pack_id"]
    _cast(cast_vote, pack_id, GOLDEN)
    _service(act_as)
    first = decision_client.post("/outcomes", json=_committee_record(pack_id))
    count = len(decision_store.events)

    again = decision_client.post("/outcomes", json=_committee_record(pack_id))

    assert again.status_code == 200
    assert again.json() == first.json()
    assert len(decision_store.events) == count
    different = decision_client.post(
        "/outcomes", json=_committee_record(pack_id, plain_summary="Changed my mind.")
    )
    assert different.status_code == 409
    assert different.json()["type"].endswith("/outcome_already_recorded")


@pytest.mark.parametrize("wrong", ["approve", "defer", "reject"])
def test_outcomes_committee_never_records_anything_but_the_tally(
    decision_client: TestClient,
    act_as: ActAs,
    released_pack: Callable[..., dict[str, Any]],
    cast_vote: Callable[..., Any],
    decision_store: InMemoryDecisionStore,
    wrong: str,
) -> None:
    pack_id = released_pack(CASE)["pack_id"]
    _cast(cast_vote, pack_id, GOLDEN)
    count = len(decision_store.events)
    _service(act_as)

    response = decision_client.post(
        "/outcomes", json=_committee_record(pack_id, outcome=wrong)
    )

    assert response.status_code == 409
    assert response.json()["type"].endswith("/outcome_mismatch")
    assert len(decision_store.events) == count


def test_outcomes_committee_needs_a_decisive_tally(
    decision_client: TestClient,
    act_as: ActAs,
    released_pack: Callable[..., dict[str, Any]],
    cast_vote: Callable[..., Any],
) -> None:
    untallied = released_pack(CASE)["pack_id"]
    _service(act_as)
    early = decision_client.post("/outcomes", json=_committee_record(untallied))
    assert early.status_code == 409
    assert early.json()["type"].endswith("/not_tallied")

    _cast(
        cast_vote, untallied, ("approve", "defer", "reject")
    )  # a split decides nothing
    _service(act_as)
    split = decision_client.post("/outcomes", json=_committee_record(untallied))
    assert split.status_code == 409
    assert split.json()["type"].endswith("/not_tallied")


def test_outcomes_committee_for_another_cases_pack_is_409(
    decision_client: TestClient,
    act_as: ActAs,
    released_pack: Callable[..., dict[str, Any]],
    cast_vote: Callable[..., Any],
) -> None:
    pack_id = released_pack(CASE)["pack_id"]
    _cast(cast_vote, pack_id, GOLDEN)
    _service(act_as)
    response = decision_client.post(
        "/outcomes", json=_committee_record(pack_id, case_id=OTHER_CASE)
    )
    assert response.status_code == 409
    assert response.json()["type"].endswith("/outcome_mismatch")


def test_outcomes_committee_unknown_pack_is_404(
    decision_client: TestClient, act_as: ActAs
) -> None:
    _service(act_as)
    response = decision_client.post("/outcomes", json=_committee_record("pack_nope"))
    assert response.status_code == 404


def test_outcomes_committee_names_its_pack(
    decision_client: TestClient, act_as: ActAs
) -> None:
    _service(act_as)
    body = _committee_record("x")
    del body["pack_id"]
    assert decision_client.post("/outcomes", json=body).status_code == 422


# ── outcomes: delegated ──────────────────────────────────────────────────


def test_outcomes_delegated_records_an_analysts_close_with_their_reason(
    decision_client: TestClient,
    act_as: ActAs,
    decision_store: InMemoryDecisionStore,
) -> None:
    _service(act_as)

    response = decision_client.post("/outcomes", json=_delegated_record())

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mode"] == "delegated"
    assert body["pack_id"] is None
    assert body["decided_by"] == ANALYST_SUB
    assert body["reason"].startswith("Band 2")
    announced = decision_store.events[-1]
    assert announced.type == "outcome.set"
    assert announced.payload["mode"] == "delegated"
    assert announced.payload["pack_id"] is None
    assert announced.payload["decided_by"] == ANALYST_SUB
    act_as(Role.ANALYST, ANALYST_SUB)
    assert (
        decision_client.get(f"/cases/{OTHER_CASE}/outcome").json()["mode"]
        == "delegated"
    )


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda r: r.pop("reason"), id="reason-missing"),
        pytest.param(lambda r: r.update(reason="too short"), id="reason-short"),
        pytest.param(lambda r: r.pop("decided_by"), id="decider-missing"),
        pytest.param(lambda r: r.update(pack_id="pack_x"), id="names-a-pack"),
    ],
)
def test_outcomes_delegated_needs_a_person_a_reason_and_no_pack(
    decision_client: TestClient,
    act_as: ActAs,
    mutate: Callable[[dict[str, Any]], Any],
) -> None:
    record = _delegated_record()
    mutate(record)
    _service(act_as)
    assert decision_client.post("/outcomes", json=record).status_code == 422


def test_outcomes_delegated_again_is_a_replay_unless_it_differs(
    decision_client: TestClient,
    act_as: ActAs,
    decision_store: InMemoryDecisionStore,
) -> None:
    _service(act_as)
    first = decision_client.post("/outcomes", json=_delegated_record())
    count = len(decision_store.events)

    same = decision_client.post("/outcomes", json=_delegated_record())
    other = decision_client.post("/outcomes", json=_delegated_record(outcome="reject"))

    assert same.status_code == 200
    assert same.json() == first.json()
    assert len(decision_store.events) == count
    assert other.status_code == 409
    assert other.json()["type"].endswith("/case_decided")


def test_outcomes_delegated_cannot_pre_empt_an_open_committee_pack(
    decision_client: TestClient,
    act_as: ActAs,
    released_pack: Callable[..., dict[str, Any]],
) -> None:
    released_pack(OTHER_CASE)
    _service(act_as)

    response = decision_client.post("/outcomes", json=_delegated_record())

    assert response.status_code == 409
    assert response.json()["type"].endswith("/committee_pending")


def test_release_of_a_pack_for_a_case_that_has_an_outcome_is_409(
    decision_client: TestClient,
    act_as: ActAs,
    build_pack: Callable[..., dict[str, Any]],
) -> None:
    pack_id = build_pack(OTHER_CASE)["pack_id"]  # built, never released
    _service(act_as)
    assert (
        decision_client.post("/outcomes", json=_delegated_record()).status_code == 200
    )

    act_as(Role.ANALYST, ANALYST_SUB)
    response = decision_client.post(f"/packs/{pack_id}/release")

    assert response.status_code == 409
    assert response.json()["type"].endswith("/case_decided")


def test_case_outcome_before_any_decision_is_404(
    decision_client: TestClient, act_as: ActAs
) -> None:
    act_as(Role.ANALYST, ANALYST_SUB)
    assert decision_client.get(f"/cases/{CASE}/outcome").status_code == 404


# ── Idempotency-Key ──────────────────────────────────────────────────────


def test_idempotency_a_replayed_build_returns_the_same_pack_once(
    decision_client: TestClient,
    act_as: ActAs,
    release_snapshot: Callable[..., dict[str, Any]],
) -> None:
    _service(act_as)
    body = {"case_id": CASE, "snapshot": release_snapshot(CASE)}
    headers = {"Idempotency-Key": "build-0151-1"}

    first = decision_client.post("/packs", json=body, headers=headers)
    again = decision_client.post("/packs", json=body, headers=headers)

    assert first.status_code == 201
    assert again.status_code == 200
    assert again.json() == first.json()


def test_idempotency_a_replayed_supersede_announces_once(
    decision_client: TestClient,
    act_as: ActAs,
    released_pack: Callable[..., dict[str, Any]],
    cast_vote: Callable[..., Any],
    decision_store: InMemoryDecisionStore,
) -> None:
    pack_id = released_pack(CASE)["pack_id"]
    cast_vote(pack_id, OKAFOR, "approve")
    _service(act_as)
    headers = {"Idempotency-Key": "step-unlock-1"}
    body = {"reason": REASON}

    first = decision_client.post(
        f"/packs/{pack_id}/supersede", json=body, headers=headers
    )
    count = len(decision_store.events)
    again = decision_client.post(
        f"/packs/{pack_id}/supersede", json=body, headers=headers
    )

    assert again.status_code == 200
    assert again.json() == first.json()  # the first answer, voided count and all
    assert again.json()["voided_votes"] == 1
    assert len(decision_store.events) == count


def test_idempotency_a_replayed_outcome_announces_once(
    decision_client: TestClient,
    act_as: ActAs,
    decision_store: InMemoryDecisionStore,
) -> None:
    _service(act_as)
    headers = {"Idempotency-Key": "step-delegated-1"}

    first = decision_client.post("/outcomes", json=_delegated_record(), headers=headers)
    again = decision_client.post("/outcomes", json=_delegated_record(), headers=headers)

    assert again.json() == first.json()
    assert [e.type for e in decision_store.events].count("outcome.set") == 1


def test_idempotency_a_replayed_revote_returns_the_first_answer(
    decision_client: TestClient,
    act_as: ActAs,
    released_pack: Callable[..., dict[str, Any]],
    cast_vote: Callable[..., Any],
) -> None:
    pack_id = released_pack(CASE)["pack_id"]
    _cast(cast_vote, pack_id, ("approve", "defer", "reject"))
    _service(act_as)
    headers = {"Idempotency-Key": "step-revote-1"}
    body = {"reason": "no_majority"}

    first = decision_client.post(f"/packs/{pack_id}/revote", json=body, headers=headers)
    # Round two has since decided; the replay still answers what step one saw.
    _cast(cast_vote, pack_id, ("approve", "approve", "defer"))
    _service(act_as)
    again = decision_client.post(f"/packs/{pack_id}/revote", json=body, headers=headers)

    assert first.status_code == 200
    assert again.json() == first.json()


def test_idempotency_a_key_reused_for_another_request_is_409(
    decision_client: TestClient,
    act_as: ActAs,
    released_pack: Callable[..., dict[str, Any]],
) -> None:
    one = released_pack(CASE)["pack_id"]
    two = released_pack(OTHER_CASE)["pack_id"]
    _service(act_as)
    headers = {"Idempotency-Key": "shared-key"}
    assert (
        decision_client.post(
            f"/packs/{one}/supersede", json={"reason": REASON}, headers=headers
        ).status_code
        == 200
    )

    reused = decision_client.post(
        f"/packs/{two}/supersede", json={"reason": REASON}, headers=headers
    )

    assert reused.status_code == 409
    assert reused.json()["type"].endswith("/idempotency_key_reused")


@pytest.mark.parametrize("key", ["", "has space", "x" * 256])
def test_idempotency_a_malformed_key_is_422(
    decision_client: TestClient,
    act_as: ActAs,
    released_pack: Callable[..., dict[str, Any]],
    key: str,
) -> None:
    pack_id = released_pack(CASE)["pack_id"]
    _service(act_as)
    response = decision_client.post(
        f"/packs/{pack_id}/supersede",
        json={"reason": REASON},
        headers={"Idempotency-Key": key},
    )
    assert response.status_code == 422
