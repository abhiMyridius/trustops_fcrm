"""BB-13, "boundary/band change acknowledged" -- the slice that exists today
(design doc numbering: "Release without acknowledgement -> 409 listing
``boundary``, ``band_change``; after acknowledge -> 200"; routing rule R3).

The acknowledgement *action* (an analyst acknowledging a boundary flag or a
band change) has no route anywhere: S1 has no gates/acknowledge route and
H1 has no HTTP at all. What does exist, and is driven here through the real
S5 app (``fcrm_decision.main.create_app``, in-memory store, HTTP only), is
the gate on the other side of it: S5 will not release a pack to the
committee unless the frozen snapshot says
``checks.boundary_and_band_acknowledged`` is true.

Honest differences from the table, asserted as they actually are:

- Without the acknowledgement S5 answers **200** with a pack whose
  ``released_at`` is null and whose ``checks.boundary_and_band_acknowledged``
  is false -- not 409 -- and the refusal shows up one step later, when a
  vote is cast on it (409 ``rule_violation``). The missing item is named by
  that one check flag; the two reasons ``boundary`` and ``band_change`` are
  not listed separately because S5 carries them as a single check.
- "After acknowledge -> 200" is the next pack version (content with the flag
  set is different content, so it is v2 and supersedes the unreleased v1).

The full scenario (409 listing both reasons, and an acknowledge call that
flips it) stays skipped in ``test_bb_not_yet_testable.py``.

Known gap, deliberately not asserted because it would fail today: S5 reads
each check with ``bool(...)``, so a malformed but truthy value (the string
``"false"``, say) counts as acknowledged and releases the pack, although the
route docstring promises malformed values fail closed. Only missing, null,
zero and false are exercised below.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration

ACK = "boundary_and_band_acknowledged"
OTHER_CHECKS = ("numbers_locked", "conditions_drafted", "all_review_items_decided")

MEMBER = "u-lchen"


def test_a_pack_without_boundary_acknowledgement_is_unreleased_and_takes_no_votes(
    decision_client: TestClient,
    build_pack: Callable[..., dict[str, Any]],
    cast_vote: Callable[..., Any],
    demo: dict[str, Any],
) -> None:
    pack = build_pack(demo["id"], checks={ACK: False})

    assert pack["released_at"] is None
    assert pack["checks"][ACK] is False
    # It is exactly the acknowledgement that is missing; nothing else is.
    assert all(pack["checks"][name] is True for name in OTHER_CHECKS)

    refused = cast_vote(pack["pack_id"], MEMBER, "approve")
    assert refused.status_code == 409
    assert refused.json()["type"].endswith("/rule_violation")
    assert (
        decision_client.get(f"/packs/{pack['pack_id']}/votes").json()["votes_cast"] == 0
    )
    assert decision_client.get(f"/packs/{pack['pack_id']}/outcome").status_code == 409


@pytest.mark.parametrize(
    "snapshot_checks",
    [
        pytest.param({ACK: None}, id="flag-null"),
        pytest.param({ACK: 0}, id="flag-zero"),
        pytest.param("missing-key", id="flag-absent"),
        pytest.param("no-checks-block", id="no-checks-block"),
    ],
)
def test_a_missing_or_empty_acknowledgement_fails_closed(
    decision_client: TestClient,
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

    response = decision_client.post(
        "/packs", json={"case_id": "FCRM-2026-0151", "snapshot": snapshot}
    )
    assert response.status_code == 200
    pack = response.json()
    assert pack["released_at"] is None
    assert pack["checks"][ACK] is False


@pytest.mark.parametrize("failing", [ACK, *OTHER_CHECKS])
def test_each_of_the_four_release_checks_alone_blocks_the_release(
    build_pack: Callable[..., dict[str, Any]], failing: str
) -> None:
    pack = build_pack("FCRM-2026-0151", checks={failing: False})
    assert pack["released_at"] is None
    assert pack["checks"][failing] is False


def test_acknowledging_releases_the_next_version_and_only_that_one_takes_votes(
    decision_client: TestClient,
    build_pack: Callable[..., dict[str, Any]],
    cast_vote: Callable[..., Any],
    demo: dict[str, Any],
) -> None:
    unacknowledged = build_pack(demo["id"], checks={ACK: False})
    acknowledged = build_pack(demo["id"])  # same case, flag now true

    assert acknowledged["version"] == 2
    assert acknowledged["pack_id"] != unacknowledged["pack_id"]
    assert acknowledged["released_at"] is not None
    assert acknowledged["checks"][ACK] is True

    superseded = decision_client.get(f"/packs/{unacknowledged['pack_id']}").json()
    assert superseded["superseded_by"] == acknowledged["pack_id"]
    assert superseded["released_at"] is None

    assert cast_vote(unacknowledged["pack_id"], MEMBER, "approve").status_code == 409
    assert cast_vote(acknowledged["pack_id"], MEMBER, "approve").status_code == 200
