"""BB-13, "boundary/band change acknowledged" -- the slice that exists today
(design doc numbering: "Release without acknowledgement -> 409 listing
``boundary``, ``band_change``; after acknowledge -> 200"; routing rule R3).

The acknowledgement *action* (an analyst acknowledging a boundary flag or a
band change) has no route anywhere yet: S1 has no acknowledge route and H1 has
no HTTP at all. What does exist, and is driven here through the real S5 app
(``fcrm_decision.main.create_app``, in-memory store, HTTP only), is the gate on
the other side of it: S5 will not release a pack to the committee unless the
frozen snapshot says ``checks.boundary_and_band_acknowledged`` is true, and
``POST /packs/{id}/release`` answers **409** naming the check that failed.

Honest difference from the table, asserted as it actually is: the 409 lists the
failing *check* (``boundary_and_band_acknowledged``), not ``boundary`` and
``band_change`` separately: S5 carries them as one check, set by H1 from S1's
acknowledgements. "After acknowledge -> 200" is the next pack version (content
with the flag set is different content, so it is v2, built once the unreleased
v1 is superseded).

The full scenario (the acknowledge call that flips it) stays skipped in
``test_bb_not_yet_testable.py``.

A release check must be a literal JSON ``true``: a missing key, a null, a zero
or the string ``"true"`` is refused at the build (422), never read as an
acknowledgement.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from fcrm_contracts.enums import Role

pytestmark = pytest.mark.integration

ACK = "boundary_and_band_acknowledged"
OTHER_CHECKS = ("locked", "all_items_reviewed", "business_summary_present")

MEMBER = "u-lchen"
ActAs = Callable[[Role, str], None]


def _release(client: TestClient, act_as: ActAs, pack_id: str) -> Any:
    act_as(Role.ANALYST, "u-riyer")
    return client.post(f"/packs/{pack_id}/release")


def test_a_pack_without_boundary_acknowledgement_is_409_on_release_and_takes_no_votes(
    decision_client: TestClient,
    act_as: ActAs,
    build_pack: Callable[..., dict[str, Any]],
    cast_vote: Callable[..., Any],
    demo: dict[str, Any],
) -> None:
    pack = build_pack(demo["id"], checks={ACK: False})
    assert pack["status"] == "built"
    assert pack["released_at"] is None

    refused = _release(decision_client, act_as, pack["pack_id"])

    assert refused.status_code == 409
    assert refused.json()["type"].endswith("/release_checks_failed")
    assert ACK in refused.json()["detail"]
    # It is exactly the acknowledgement that is missing; nothing else is.
    assert not any(name in refused.json()["detail"] for name in OTHER_CHECKS)
    voted = cast_vote(pack["pack_id"], MEMBER, "approve")
    assert voted.status_code == 409
    assert voted.json()["type"].endswith("/pack_not_released")
    act_as(Role.ANALYST, "u-riyer")
    assert (
        decision_client.get(f"/packs/{pack['pack_id']}/votes").json()["cast_count"] == 0
    )
    assert decision_client.get(f"/packs/{pack['pack_id']}/outcome").status_code == 409
    # A committee member cannot even see it.
    act_as(Role.COMMITTEE, MEMBER)
    assert decision_client.get(f"/packs/{pack['pack_id']}").status_code == 404


@pytest.mark.parametrize(
    "snapshot_checks",
    [
        pytest.param({ACK: None}, id="flag-null"),
        pytest.param({ACK: 0}, id="flag-zero"),
        pytest.param({ACK: "true"}, id="flag-truthy-string"),
        pytest.param("missing-key", id="flag-absent"),
        pytest.param("no-checks-block", id="no-checks-block"),
    ],
)
def test_a_missing_or_malformed_acknowledgement_is_refused_at_the_build(
    decision_client: TestClient,
    act_as: ActAs,
    release_snapshot: Callable[..., dict[str, Any]],
    snapshot_checks: Any,
) -> None:
    snapshot = release_snapshot("FCRM-2026-0151")
    if snapshot_checks == "missing-key":
        del snapshot["checks"][ACK]
    elif snapshot_checks == "no-checks-block":
        del snapshot["checks"]
    else:
        snapshot["checks"].update(snapshot_checks)

    act_as(Role.SERVICE, "svc:caseflow")
    response = decision_client.post(
        "/packs", json={"case_id": "FCRM-2026-0151", "snapshot": snapshot}
    )

    assert response.status_code == 422
    assert response.json()["type"].endswith("/validation_failed")


@pytest.mark.parametrize("failing", [ACK, *OTHER_CHECKS])
def test_each_of_the_four_release_checks_alone_blocks_the_release(
    decision_client: TestClient,
    act_as: ActAs,
    build_pack: Callable[..., dict[str, Any]],
    failing: str,
) -> None:
    pack = build_pack("FCRM-2026-0151", checks={failing: False})

    refused = _release(decision_client, act_as, pack["pack_id"])

    assert refused.status_code == 409
    assert failing in refused.json()["detail"]


def test_acknowledging_releases_the_next_version_and_only_that_one_takes_votes(
    decision_client: TestClient,
    act_as: ActAs,
    build_pack: Callable[..., dict[str, Any]],
    release_pack: Callable[[str], dict[str, Any]],
    cast_vote: Callable[..., Any],
    demo: dict[str, Any],
) -> None:
    unacknowledged = build_pack(demo["id"], checks={ACK: False})
    assert (
        _release(decision_client, act_as, unacknowledged["pack_id"]).status_code == 409
    )
    # H1: the analyst unlocks to acknowledge, so the unreleased v1 is superseded.
    act_as(Role.SERVICE, "svc:caseflow")
    assert (
        decision_client.post(
            f"/packs/{unacknowledged['pack_id']}/supersede",
            json={"reason": "unlocked so the boundary flag can be acknowledged"},
        ).status_code
        == 200
    )

    acknowledged = build_pack(demo["id"])  # same case, flag now true
    released = release_pack(acknowledged["pack_id"])

    assert acknowledged["version"] == 2
    assert acknowledged["pack_id"] != unacknowledged["pack_id"]
    assert released["status"] == "released"
    act_as(Role.ANALYST, "u-riyer")
    old = decision_client.get(f"/packs/{unacknowledged['pack_id']}").json()
    assert old["status"] == "superseded"
    assert old["released_at"] is None
    assert cast_vote(unacknowledged["pack_id"], MEMBER, "approve").status_code == 409
    assert cast_vote(acknowledged["pack_id"], MEMBER, "approve").status_code == 201
