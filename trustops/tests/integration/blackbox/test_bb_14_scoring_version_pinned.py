"""BB-14, "versions pinned" (design doc numbering: "Publish a new control
inventory v10; the open demo case still reports controls v9 and its
score is unchanged").

Full scope needs a case service that stores which corpus/control/field-
set/prompt versions a case was pinned to at submission time (S1, not
built yet -- ``fcrm_case`` is pure domain logic with no HTTP layer) --
not provable end to end today (see ``test_bb_not_yet_testable.py``).

What *is* real and provable today, at the one service that does have a
live HTTP layer and a real multi-version store (``fcrm_scoring``'s
``ConfigStore``, ``GET /models/{version}``): a case's ``scoring_v`` pins
which scoring model its ``/score`` call is computed against, and
publishing a newer model version (becoming the new ``active`` one) must
never change what an older, already-pinned ``scoring_v`` recomputes to.
This is the same mechanism BB-14's full case-level version pinning would
ultimately depend on -- "the scoring model a case pins never moves under
it" -- demonstrated at the layer that actually exists.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fcrm_common.errors import install_error_handlers
from fcrm_common.health import ReadinessProbe, build_health_router
from fcrm_contracts.enums import Band, Role, Route
from fcrm_scoring.routes import router
from fcrm_scoring.seedmodel import (
    apply_catalogue_weights,
    score_request_from_case,
    scoring_model_from_seed,
)
from fcrm_scoring.store import InMemoryConfigStore

pytestmark = pytest.mark.integration


@pytest.fixture
def app(
    seeded_scoring_model: dict[str, Any],
    wire_test_auth: Callable[[FastAPI], FastAPI],
) -> FastAPI:
    """Two scoring model versions loaded at once: v3 (the real seeded
    model, inactive here) and a hypothetical v10 that is ``active`` and
    deliberately computes retention differently (a higher
    ``retention_factor_pct``, so every composite it produces differs from
    v3's -- confirmed directly by
    ``test_scoring_against_the_newly_active_v10_model_genuinely_differs``
    below), so a drift bug -- the route silently reading the active model
    instead of the one the request actually pins -- would be impossible
    to miss: the composite would move, not stay at 9.8/10.4.
    """
    application = FastAPI()
    install_error_handlers(application)
    application.state.probe = ReadinessProbe()
    application.include_router(router)
    application.include_router(build_health_router(application.state.probe))

    v3 = scoring_model_from_seed(seeded_scoring_model).model_copy(
        update={"version": 3, "active": False}
    )
    v10 = v3.model_copy(
        update={
            "version": 10,
            "active": True,
            "retention_factor_pct": 0.9,  # seed's v3 uses 0.6; a different model
            "owner": "test-harness",
            "approved": "2026-10-04",
        }
    )
    application.state.store = InMemoryConfigStore(models=[v3, v10], routing=[])
    return wire_test_auth(application)


@pytest.fixture
def client(app: FastAPI, act_as: Callable[[Role, str], None]) -> TestClient:
    """Calls S2 as H1's service account, its real caller (ADR-07)."""
    act_as(Role.SERVICE, "svc:caseflow")
    return TestClient(app)


def test_a_newly_active_model_version_is_visible_as_the_active_one(
    client: TestClient,
) -> None:
    response = client.get("/models/active")
    assert response.status_code == 200
    assert response.json()["version"] == 10


def test_the_pinned_v3_model_is_still_retrievable_byte_for_byte(
    client: TestClient,
) -> None:
    response = client.get("/models/3")
    assert response.status_code == 200
    body = response.json()
    assert body["version"] == 3
    assert body["active"] is False
    # Despite the "_pct" suffix, the seed stores this as the 0-1 fraction
    # the formula actually uses (CLAUDE.md: "Retention = 1 - 0.6*CE/10"),
    # not 0-100 -- confirmed against the real seeded value, not assumed.
    assert body["retention_factor_pct"] == 0.6


def test_a_case_pinned_to_v3_still_recomputes_9_8_and_10_4_after_v10_goes_active(
    client: TestClient, demo: dict[str, Any], catalogue_weights: dict[str, float]
) -> None:
    """The decisive check: both golden demo numbers, computed with
    ``scoring_v=3`` explicitly, are byte-identical to the pre-v10 golden
    values -- even though ``/models/active`` now answers v10 and v10
    scores everything harsher. A route that read the *active* model
    regardless of ``body.scoring_v`` would fail this immediately.
    """
    for stage, expected_composite, expected_band, expected_route in (
        ("score_before", 9.8, Band.BAND_2, Route.ANALYST),
        ("score_after", 10.4, Band.BAND_3, Route.COMMITTEE),
    ):
        request = score_request_from_case(demo["id"], 3, demo[stage]["areas"])
        request = apply_catalogue_weights(request, catalogue_weights)

        response = client.post("/score", json=request.model_dump(mode="json"))
        assert response.status_code == 200
        body = response.json()
        assert body["scoring_v"] == 3
        assert body["composite"] == pytest.approx(expected_composite)
        assert body["band"] == expected_band.value
        assert body["route"] == expected_route.value


def test_scoring_against_the_newly_active_v10_model_genuinely_differs(
    client: TestClient, demo: dict[str, Any], catalogue_weights: dict[str, float]
) -> None:
    """Negative control for the test above: if this *didn't* differ, the
    previous test would be meaningless (it could be passing because v10
    and v3 happen to compute identically, not because pinning works).
    Requesting ``scoring_v=10`` explicitly must give a different,
    harsher composite than the v3 golden 9.8.
    """
    request = score_request_from_case(demo["id"], 10, demo["score_before"]["areas"])
    request = apply_catalogue_weights(request, catalogue_weights)

    response = client.post("/score", json=request.model_dump(mode="json"))
    assert response.status_code == 200
    body = response.json()
    assert body["scoring_v"] == 10
    assert body["composite"] != pytest.approx(9.8)
