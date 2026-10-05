"""BB-10, "planned control not scored" (design doc numbering;
BUILD-PLAN.md's summary line lists the same rule as its own BB-09): "Add
a planned control; ``assessment.score`` unchanged; pack lists it under
planned."

The real seed data for the demo case already has exactly this shape: in
``demo["score_after"]["areas"]["channel"]``, the control catalogue entry
``CTL-T01`` appears *twice* -- once as the real, scored, overridden
control (rating 4.5) and once more as a second ``CTL-T01`` row with
``"planned": true, "rating": null, "note": "planned control, not
scored"`` (a corridor-specific re-assessment scheduled for after the
demo). This file drives that real planned-control row through the real
HTTP ``/score`` route (not just the engine function) via two
genuinely-provable mechanisms:

1. ``fcrm_scoring.seedmodel.score_request_from_case`` (the real adapter
   S2's own startup self-test and golden tests use to turn seed ``areas``
   into a ``ScoreRequest``) drops the planned row before it ever reaches
   the engine, and the resulting composite through the real route still
   equals the golden 10.4.
2. ``fcrm_contracts.api.scoring.ControlInput.rating`` is a required
   ``float`` -- a planned control's ``rating: null`` cannot even be
   constructed as a scored ``ControlInput`` at all. "Never scored" is not
   merely a convention the adapter happens to follow; the wire contract
   itself has no slot for an unscored row to occupy. This is the part
   that was not provable before an HTTP layer existed for *some*
   service -- S2's is real, so this is exercised as a genuine HTTP round
   trip, not only as engine-level unit tests already do in
   ``test_golden_cases.py``.

The "pack lists it under planned" half needs S5's pack-assembly HTTP
route, which does not exist yet -- not claimed here (see
``test_bb_not_yet_testable.py``).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fcrm_common.errors import install_error_handlers
from fcrm_common.health import ReadinessProbe, build_health_router
from fcrm_contracts.api.scoring import ControlInput
from fcrm_contracts.enums import Band, Domain, Role, Route
from fcrm_scoring.routes import router
from fcrm_scoring.seedmodel import (
    apply_catalogue_weights,
    score_request_from_case,
    scoring_model_from_seed,
)
from fcrm_scoring.store import InMemoryConfigStore
from pydantic import ValidationError

pytestmark = pytest.mark.integration


@pytest.fixture
def app(
    seeded_scoring_model: dict[str, Any],
    wire_test_auth: Callable[[FastAPI], FastAPI],
) -> FastAPI:
    application = FastAPI()
    install_error_handlers(application)
    application.state.probe = ReadinessProbe()
    application.include_router(router)
    application.include_router(build_health_router(application.state.probe))
    model = scoring_model_from_seed(seeded_scoring_model)
    application.state.store = InMemoryConfigStore(models=[model], routing=[])
    return wire_test_auth(application)


@pytest.fixture
def client(app: FastAPI, act_as: Callable[[Role, str], None]) -> TestClient:
    """Calls S2 as H1's service account, its real caller (ADR-07)."""
    act_as(Role.SERVICE, "svc:caseflow")
    return TestClient(app)


def test_the_demo_s_planned_control_row_exists_exactly_as_the_docstring_claims(
    demo: dict[str, Any],
) -> None:
    """Grounding check: the planned row this whole file depends on is
    real seed data, not a fixture invented for this test.
    """
    channel_controls = demo["score_after"]["areas"]["channel"]["controls"]
    planned_rows = [c for c in channel_controls if c.get("planned")]
    assert len(planned_rows) == 1
    planned = planned_rows[0]
    assert planned["id"] == "CTL-T01"
    assert planned["rating"] is None
    assert planned["weight_norm"] == 0.0

    scored_rows = [
        c for c in channel_controls if c["id"] == "CTL-T01" and not c.get("planned")
    ]
    assert len(scored_rows) == 1
    assert scored_rows[0]["rating"] == 4.5


def test_a_planned_control_s_null_rating_cannot_be_expressed_as_a_scored_control_input(
    demo: dict[str, Any],
) -> None:
    """The contract-level proof: not "the adapter happens to filter it
    out" but "the wire shape has no field that could carry an unscored
    control's rating at all" -- constructing ``ControlInput`` directly
    from the planned row's raw data raises, because ``rating`` is a
    required ``float`` and the planned row's rating is ``null``.
    """
    channel_controls = demo["score_after"]["areas"]["channel"]["controls"]
    planned = next(c for c in channel_controls if c.get("planned"))

    with pytest.raises(ValidationError):
        ControlInput(
            control_id=planned["id"],
            domain=Domain.CHANNEL,
            rating=planned["rating"],  # None
            catalogue_weight=planned["weight_norm"] or 0.01,
        )


def test_the_real_adapter_drops_the_planned_row_and_the_route_still_scores_10_4(
    client: TestClient, demo: dict[str, Any], catalogue_weights: dict[str, float]
) -> None:
    """The planned row is left in ``demo["score_after"]["areas"]`` as-is
    (not pre-filtered by this test) and handed to the real adapter, the
    real weight-filling step, and the real HTTP ``/score`` route -- the
    exact path S2's own startup self-test and golden tests exercise. The
    planned control changes nothing about the result.
    """
    request = score_request_from_case(demo["id"], 3, demo["score_after"]["areas"])
    request = apply_catalogue_weights(request, catalogue_weights)

    channel = next(d for d in request.domains if d.domain == Domain.CHANNEL)
    control_ids = [c.control_id for c in channel.controls]
    # Only the scored CTL-T01 made it through -- one entry, not two.
    assert control_ids.count("CTL-T01") == 1

    response = client.post("/score", json=request.model_dump(mode="json"))
    assert response.status_code == 200
    body = response.json()
    assert body["composite"] == pytest.approx(10.4)
    assert body["band"] == Band.BAND_3.value
    assert body["route"] == Route.COMMITTEE.value
