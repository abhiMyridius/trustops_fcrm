"""S5's pack routes over HTTP, with no Mongo: build (H1), read (analyst and
committee), list, and release (the analyst's gate).

What is pinned here:

- a pack is frozen when built and **building never releases it**; the hash is
  the SHA-256 of the canonical JSON of the snapshot as sent, reproducible by a
  third party;
- the same content is the same pack (idempotent), different content while the
  case's pack is live is refused until it is superseded (R6);
- a snapshot that is not a pack body is a 422 naming what is wrong, and a
  release check that is not a literal JSON boolean fails closed;
- a committee member never sees a pack that was not released;
- release is 409 unless all four checks passed, naming the failing ones.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from fcrm_contracts.enums import Role
from fcrm_decision.store_memory import InMemoryDecisionStore

ActAs = Callable[[Role, str], None]
CASE = "FCRM-2026-0151"
SERVICE_SUB = "svc:caseflow"
ANALYST_SUB = "u-riyer"
CHECKS = (
    "locked",
    "all_items_reviewed",
    "boundary_and_band_acknowledged",
    "business_summary_present",
)


def _sha256_of_canonical_json(snapshot: dict[str, Any]) -> str:
    """The published hash recipe, re-implemented from its description: a
    verifier needs nothing from the service's own code to reproduce it.
    """
    canonical = json.dumps(
        snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _reversed_key_order(value: Any) -> Any:
    """The same JSON value with every object's keys in reverse order."""
    if isinstance(value, dict):
        return {k: _reversed_key_order(v) for k, v in reversed(list(value.items()))}
    if isinstance(value, list):
        return [_reversed_key_order(item) for item in value]
    return value


def _post_pack(
    client: TestClient, act_as: ActAs, snapshot: dict[str, Any], **headers: str
) -> Any:
    act_as(Role.SERVICE, SERVICE_SUB)
    return client.post(
        "/packs",
        json={"case_id": snapshot["case_id"], "snapshot": snapshot},
        headers=headers,
    )


# ── build ────────────────────────────────────────────────────────────────


def test_build_pack_freezes_an_unreleased_pack_v1(
    decision_client: TestClient,
    act_as: ActAs,
    release_snapshot: Callable[..., dict[str, Any]],
) -> None:
    snapshot = release_snapshot(CASE)
    response = _post_pack(decision_client, act_as, snapshot)

    assert response.status_code == 201, response.text
    pack = response.json()
    assert pack["version"] == 1
    assert pack["status"] == "built"
    assert pack["released_at"] is None  # building never releases
    assert pack["case_id"] == CASE
    assert pack["score"]["composite"] == 10.4
    assert pack["score"]["band"] == 3
    assert pack["content_hash"] == _sha256_of_canonical_json(snapshot)
    assert pack["addenda"] == []


def test_build_pack_hash_is_stable_across_ten_reads(
    decision_client: TestClient,
    build_pack: Callable[..., dict[str, Any]],
    act_as: ActAs,
) -> None:
    built = build_pack(CASE)
    act_as(Role.ANALYST, ANALYST_SUB)
    reads = [decision_client.get(f"/packs/{built['pack_id']}") for _ in range(10)]
    assert all(read.status_code == 200 for read in reads)
    assert all(read.json() == built for read in reads)


def test_build_pack_same_content_is_the_same_pack_even_with_keys_reordered(
    decision_client: TestClient,
    act_as: ActAs,
    release_snapshot: Callable[..., dict[str, Any]],
) -> None:
    snapshot = release_snapshot(CASE)
    first = _post_pack(decision_client, act_as, snapshot)
    assert first.status_code == 201

    for variant in (snapshot, _reversed_key_order(snapshot)):
        again = _post_pack(decision_client, act_as, variant)
        assert again.status_code == 200, again.text
        assert again.json() == first.json()


def test_build_pack_different_content_while_the_pack_is_live_is_409(
    decision_client: TestClient,
    act_as: ActAs,
    release_snapshot: Callable[..., dict[str, Any]],
) -> None:
    snapshot = release_snapshot(CASE)
    assert _post_pack(decision_client, act_as, snapshot).status_code == 201

    edited = {**snapshot, "business_summary": "One more character. "}
    refused = _post_pack(decision_client, act_as, edited)

    assert refused.status_code == 409
    assert refused.json()["type"].endswith("/pack_not_superseded")


def test_build_pack_after_supersede_mints_v2_and_links_v1_to_it(
    decision_client: TestClient,
    act_as: ActAs,
    build_pack: Callable[..., dict[str, Any]],
    release_snapshot: Callable[..., dict[str, Any]],
) -> None:
    v1 = build_pack(CASE)
    act_as(Role.SERVICE, SERVICE_SUB)
    superseded = decision_client.post(
        f"/packs/{v1['pack_id']}/supersede",
        json={"reason": "the corridor review changed the channel rating"},
    )
    assert superseded.status_code == 200, superseded.text

    corrected = release_snapshot(CASE, business_summary="Corrected after review.")
    v2 = _post_pack(decision_client, act_as, corrected)

    assert v2.status_code == 201, v2.text
    body = v2.json()
    assert body["version"] == 2
    assert body["pack_id"] != v1["pack_id"]
    assert body["content_hash"] == _sha256_of_canonical_json(corrected)
    # v1 stays readable, with its own hash, superseded.
    act_as(Role.ANALYST, ANALYST_SUB)
    old = decision_client.get(f"/packs/{v1['pack_id']}").json()
    assert old["status"] == "superseded"
    assert old["content_hash"] == v1["content_hash"]


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda s: s.pop("score"), id="score-missing"),
        pytest.param(lambda s: s.pop("checks"), id="checks-missing"),
        pytest.param(lambda s: s["checks"].pop("locked"), id="one-check-missing"),
        pytest.param(
            lambda s: s["checks"].update(locked="true"), id="check-is-a-string"
        ),
        pytest.param(lambda s: s["checks"].update(locked=1), id="check-is-an-int"),
        pytest.param(lambda s: s["checks"].update(locked=None), id="check-is-null"),
        pytest.param(lambda s: s["score"].update(band=7), id="band-out-of-range"),
    ],
)
def test_build_pack_refuses_a_snapshot_that_is_not_a_pack_body(
    decision_client: TestClient,
    act_as: ActAs,
    release_snapshot: Callable[..., dict[str, Any]],
    mutate: Callable[[dict[str, Any]], Any],
) -> None:
    snapshot = release_snapshot(CASE)
    mutate(snapshot)

    response = _post_pack(decision_client, act_as, snapshot)

    assert response.status_code == 422, response.text
    assert response.json()["type"].endswith("/validation_failed")
    assert response.json()["errors"]


def test_build_pack_refuses_a_snapshot_for_another_case(
    decision_client: TestClient,
    act_as: ActAs,
    release_snapshot: Callable[..., dict[str, Any]],
) -> None:
    act_as(Role.SERVICE, SERVICE_SUB)
    response = decision_client.post(
        "/packs",
        json={"case_id": "FCRM-2026-0140", "snapshot": release_snapshot(CASE)},
    )
    assert response.status_code == 422
    assert response.json()["errors"][0]["loc"] == "snapshot.case_id"


def test_build_pack_service_without_a_snapshot_is_422(
    decision_client: TestClient, act_as: ActAs
) -> None:
    act_as(Role.SERVICE, SERVICE_SUB)
    response = decision_client.post("/packs", json={"case_id": CASE})
    assert response.status_code == 422


def test_build_pack_for_a_case_that_has_an_outcome_is_409(
    decision_client: TestClient,
    act_as: ActAs,
    release_snapshot: Callable[..., dict[str, Any]],
) -> None:
    act_as(Role.SERVICE, SERVICE_SUB)
    closed = decision_client.post(
        "/outcomes",
        json={
            "case_id": CASE,
            "mode": "delegated",
            "outcome": "approve",
            "decided_by": ANALYST_SUB,
            "reason": "Band 2, inside delegated authority.",
        },
    )
    assert closed.status_code == 200, closed.text

    response = _post_pack(decision_client, act_as, release_snapshot(CASE))

    assert response.status_code == 409
    assert response.json()["type"].endswith("/case_decided")


# ── analyst: ask for the latest pack through POST /packs ─────────────────


def test_post_packs_as_an_analyst_returns_the_latest_pack(
    decision_client: TestClient,
    act_as: ActAs,
    build_pack: Callable[..., dict[str, Any]],
) -> None:
    built = build_pack(CASE)
    act_as(Role.ANALYST, ANALYST_SUB)

    response = decision_client.post("/packs", json={"case_id": CASE})

    assert response.status_code == 200
    assert response.json() == built


def test_post_packs_as_an_analyst_before_any_pack_is_409(
    decision_client: TestClient, act_as: ActAs
) -> None:
    act_as(Role.ANALYST, ANALYST_SUB)
    response = decision_client.post("/packs", json={"case_id": CASE})
    assert response.status_code == 409
    assert response.json()["type"].endswith("/pack_not_built")


def test_post_packs_as_an_analyst_never_takes_a_snapshot(
    decision_client: TestClient,
    act_as: ActAs,
    release_snapshot: Callable[..., dict[str, Any]],
) -> None:
    act_as(Role.ANALYST, ANALYST_SUB)
    response = decision_client.post(
        "/packs", json={"case_id": CASE, "snapshot": release_snapshot(CASE)}
    )
    assert response.status_code == 422


# ── reads ────────────────────────────────────────────────────────────────


def test_latest_pack_a_committee_member_cannot_see_an_unreleased_pack(
    decision_client: TestClient,
    act_as: ActAs,
    build_pack: Callable[..., dict[str, Any]],
    release_pack: Callable[[str], dict[str, Any]],
) -> None:
    built = build_pack(CASE)

    act_as(Role.COMMITTEE, "u-amendes")
    assert decision_client.get(f"/cases/{CASE}/packs/latest").status_code == 404
    assert decision_client.get(f"/packs/{built['pack_id']}").status_code == 404
    assert decision_client.get(f"/cases/{CASE}/packs").json()["items"] == []

    act_as(Role.ANALYST, ANALYST_SUB)
    assert decision_client.get(f"/cases/{CASE}/packs/latest").status_code == 200

    release_pack(built["pack_id"])
    act_as(Role.COMMITTEE, "u-amendes")
    seen = decision_client.get(f"/cases/{CASE}/packs/latest")
    assert seen.status_code == 200
    assert seen.json()["status"] == "released"
    assert seen.json()["pack_id"] == built["pack_id"]


def test_latest_pack_for_a_case_without_one_is_404(
    decision_client: TestClient, act_as: ActAs
) -> None:
    act_as(Role.ANALYST, ANALYST_SUB)
    assert decision_client.get("/cases/FCRM-2026-9999/packs/latest").status_code == 404


def test_case_packs_lists_every_version_newest_first(
    decision_client: TestClient,
    act_as: ActAs,
    build_pack: Callable[..., dict[str, Any]],
    release_snapshot: Callable[..., dict[str, Any]],
) -> None:
    v1 = build_pack(CASE)
    act_as(Role.SERVICE, SERVICE_SUB)
    decision_client.post(
        f"/packs/{v1['pack_id']}/supersede", json={"reason": "unlocked for a change"}
    )
    v2 = _post_pack(
        decision_client, act_as, release_snapshot(CASE, business_summary="v2 text")
    ).json()

    act_as(Role.ANALYST, ANALYST_SUB)
    listing = decision_client.get(f"/cases/{CASE}/packs")

    assert listing.status_code == 200
    items = listing.json()["items"]
    assert [item["version"] for item in items] == [2, 1]
    assert [item["status"] for item in items] == ["built", "superseded"]
    assert items[0]["pack_id"] == v2["pack_id"]
    assert items[0]["counts"]["composite"] == 10.4
    assert items[0]["counts"]["areas"] == 3


# ── release ──────────────────────────────────────────────────────────────


def test_release_opens_the_pack_to_the_three_committee_members(
    decision_client: TestClient,
    act_as: ActAs,
    build_pack: Callable[..., dict[str, Any]],
    decision_store: InMemoryDecisionStore,
    committee_member_ids: dict[str, str],
) -> None:
    built = build_pack(CASE)
    act_as(Role.ANALYST, ANALYST_SUB)

    response = decision_client.post(f"/packs/{built['pack_id']}/release")

    assert response.status_code == 200, response.text
    released = response.json()
    assert released["status"] == "released"
    assert released["released_at"] is not None
    assert released["content_hash"] == built["content_hash"]  # nothing changed
    announced = decision_store.events[-1]
    assert announced.type == "pack.released"
    assert announced.payload["released_to"] == sorted(committee_member_ids.values())
    assert announced.payload["release_checks_passed"] is True


@pytest.mark.parametrize("failing", CHECKS)
def test_release_is_409_when_any_one_check_fails_and_names_it(
    decision_client: TestClient,
    act_as: ActAs,
    build_pack: Callable[..., dict[str, Any]],
    decision_store: InMemoryDecisionStore,
    failing: str,
) -> None:
    built = build_pack(CASE, checks={failing: False})
    act_as(Role.ANALYST, ANALYST_SUB)

    response = decision_client.post(f"/packs/{built['pack_id']}/release")

    assert response.status_code == 409
    assert response.json()["type"].endswith("/release_checks_failed")
    assert failing in response.json()["detail"]
    assert decision_store.events == []  # nothing announced
    act_as(Role.ANALYST, ANALYST_SUB)
    unchanged = decision_client.get(f"/packs/{built['pack_id']}").json()
    assert unchanged["status"] == "built"
    assert unchanged["released_at"] is None


def test_release_names_every_failing_check(
    decision_client: TestClient,
    act_as: ActAs,
    build_pack: Callable[..., dict[str, Any]],
) -> None:
    built = build_pack(
        CASE, checks={"locked": False, "boundary_and_band_acknowledged": False}
    )
    act_as(Role.ANALYST, ANALYST_SUB)
    detail = decision_client.post(f"/packs/{built['pack_id']}/release").json()["detail"]
    assert "locked" in detail
    assert "boundary_and_band_acknowledged" in detail
    assert "all_items_reviewed" not in detail


def test_release_twice_is_a_no_op_that_announces_once(
    decision_client: TestClient,
    act_as: ActAs,
    build_pack: Callable[..., dict[str, Any]],
    release_pack: Callable[[str], dict[str, Any]],
    decision_store: InMemoryDecisionStore,
) -> None:
    built = build_pack(CASE)
    first = release_pack(built["pack_id"])
    second = release_pack(built["pack_id"])

    assert second["released_at"] == first["released_at"]
    released_events = [e for e in decision_store.events if e.type == "pack.released"]
    assert len(released_events) == 1


def test_release_of_a_superseded_pack_is_409(
    decision_client: TestClient,
    act_as: ActAs,
    build_pack: Callable[..., dict[str, Any]],
) -> None:
    built = build_pack(CASE)
    act_as(Role.SERVICE, SERVICE_SUB)
    decision_client.post(
        f"/packs/{built['pack_id']}/supersede", json={"reason": "unlocked for a change"}
    )
    act_as(Role.ANALYST, ANALYST_SUB)
    response = decision_client.post(f"/packs/{built['pack_id']}/release")
    assert response.status_code == 409
    assert response.json()["type"].endswith("/pack_superseded")


def test_release_of_an_unknown_pack_is_404(
    decision_client: TestClient, act_as: ActAs
) -> None:
    act_as(Role.ANALYST, ANALYST_SUB)
    assert decision_client.post("/packs/pack_nope/release").status_code == 404


def test_release_needs_a_three_member_committee(
    decision_client: TestClient,
    act_as: ActAs,
    build_pack: Callable[..., dict[str, Any]],
) -> None:
    from fcrm_contracts.api.identity import RosterUser
    from fcrm_decision.directory import StaticCommitteeDirectory

    built = build_pack(CASE)
    app: Any = decision_client.app
    app.state.directory = StaticCommitteeDirectory(
        [RosterUser(id="u-solo", name="Solo Member", roles=[Role.COMMITTEE])]
    )
    act_as(Role.ANALYST, ANALYST_SUB)

    response = decision_client.post(f"/packs/{built['pack_id']}/release")

    assert response.status_code == 409
    assert response.json()["type"].endswith("/committee_incomplete")
