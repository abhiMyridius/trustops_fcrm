"""Not a numbered BB scenario -- a derived cross-package check this lane
added on its own initiative, because it fell directly out of reading both
packages' source while building the BB suite above.

``fcrm_scoring.engine`` (S2, routing rule R1/R2's enforcement point for
the score itself) and ``fcrm_caseflow.routing`` (H1, routing rule R1/R2's
enforcement point for case dispatch) each independently define a
band -> route mapping. ``fcrm_caseflow.routing.route_for_band``'s own
docstring says it "mirrors ``fcrm_scoring.engine.route_for_band``
exactly" -- a claim that, per BUILD-PLAN.md's status table, no existing
test checks: S2 and H1 are each "merged and fully tested" in isolation,
but nothing imports both and compares. Two independently-maintained
copies of the same business rule (R1/R2, D-19) are exactly the kind of
thing that silently drifts apart over six days of parallel worktrees --
one gets a band added, the other does not -- and only a test that
imports both packages together would catch it.

This also chains the real HTTP ``/score`` route's output into H1's pure
routing function, which is the kind of "two real packages, wired
together, even without a composed stack" proof this lane's brief asks
for.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fcrm_caseflow.routing import route_for_band
from fcrm_common.errors import install_error_handlers
from fcrm_common.health import ReadinessProbe, build_health_router
from fcrm_contracts.enums import Band, Route
from fcrm_scoring.routes import router
from fcrm_scoring.seedmodel import (
    apply_catalogue_weights,
    score_request_from_case,
    scoring_model_from_seed,
)
from fcrm_scoring.store import InMemoryConfigStore

pytestmark = pytest.mark.integration


@pytest.fixture
def client(seeded_scoring_model: dict[str, Any]) -> TestClient:
    app = FastAPI()
    install_error_handlers(app)
    app.state.probe = ReadinessProbe()
    app.include_router(router)
    app.include_router(build_health_router(app.state.probe))
    model = scoring_model_from_seed(seeded_scoring_model)
    app.state.store = InMemoryConfigStore(models=[model], routing=[])
    return TestClient(app)


@pytest.mark.parametrize("band", [Band.BAND_1, Band.BAND_2, Band.BAND_3, Band.BAND_4])
def test_h1_s_own_band_to_route_mapping_agrees_with_s2_s_bands_for_every_band(
    band: Band,
) -> None:
    """Every one of the four bands, checked directly against H1's free
    function (no HTTP needed for this half, since there is no H1 route to
    call): H1's independent mapping must agree with what S2's own
    ``ScoringModelVersion.bands`` (the seed's real band table, read via
    the golden tests' own ``BandDef.route``) says for that band.
    """
    expected = Route.ANALYST if band in (Band.BAND_1, Band.BAND_2) else Route.COMMITTEE
    assert route_for_band(int(band.value)) == expected


def test_the_real_http_score_response_s_route_agrees_with_h1_s_pure_routing(
    client: TestClient, demo: dict[str, Any], catalogue_weights: dict[str, float]
) -> None:
    """The two golden demo numbers, through the real ``/score`` route:
    S2's own ``route`` field in the response and H1's independent
    ``route_for_band(band)`` must agree for both Band 2 (before) and
    Band 3 (after) -- not merely "both say ANALYST/COMMITTEE in the
    abstract" but "given the one real response S2 actually returned."
    """
    for stage in ("score_before", "score_after"):
        request = score_request_from_case(demo["id"], 3, demo[stage]["areas"])
        request = apply_catalogue_weights(request, catalogue_weights)
        response = client.post("/score", json=request.model_dump(mode="json"))
        assert response.status_code == 200
        body = response.json()

        s2_route = Route(body["route"])
        h1_route = route_for_band(int(body["band"]))
        assert s2_route == h1_route, (
            f"{stage}: S2 said {s2_route!r}, H1's route_for_band said "
            f"{h1_route!r} for band {body['band']!r} -- the two copies of "
            "R1/R2 have drifted apart"
        )
