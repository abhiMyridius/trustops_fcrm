"""HTTP-layer tests for S2, with no Mongo dependency: the route layer
takes whatever implements ``ConfigStore``, so these wire in
``InMemoryConfigStore`` seeded from the real seed data and exercise the
app exactly as a caller would over HTTP.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fcrm_common.errors import install_error_handlers
from fcrm_common.health import ReadinessProbe, build_health_router
from fcrm_contracts.enums import Band, Route
from fcrm_scoring.routes import router
from fcrm_scoring.seedmodel import scoring_model_from_seed
from fcrm_scoring.store import InMemoryConfigStore
from fcrm_seed.loader import demo_case, scoring_model


@pytest.fixture
def app() -> FastAPI:
    application = FastAPI()
    install_error_handlers(application)
    application.state.probe = ReadinessProbe()
    application.include_router(router)
    application.include_router(build_health_router(application.state.probe))

    model = scoring_model_from_seed(scoring_model())
    application.state.store = InMemoryConfigStore(models=[model], routing=[])
    return application


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def _demo_request(stage: str) -> dict:
    demo = demo_case()
    areas = demo[stage]["areas"]
    weights_by_control = {
        c["id"]: c["weight_norm"]
        for area in areas.values()
        for c in area["controls"]
        if not c.get("planned")
    }
    return {
        "case_id": demo["id"],
        "scoring_v": 3,
        "domains": [
            {
                "domain": domain,
                "likelihood": area["L"],
                "impact": area["I"],
                "controls": [
                    {
                        "control_id": c["id"],
                        "domain": domain,
                        "rating": c["rating"],
                        "catalogue_weight": weights_by_control.get(c["id"], 0.2),
                    }
                    for c in area["controls"]
                    if not c.get("planned")
                ],
            }
            for domain, area in areas.items()
        ],
    }


def test_post_score_reproduces_the_demo_before_override(client: TestClient) -> None:
    response = client.post("/score", json=_demo_request("score_before"))
    assert response.status_code == 200
    body = response.json()
    assert body["composite"] == pytest.approx(9.8)
    assert body["band"] == Band.BAND_2.value
    assert body["route"] == Route.ANALYST.value
    assert body["boundary_flag"] is True


def test_post_score_reproduces_the_demo_after_override(client: TestClient) -> None:
    response = client.post("/score", json=_demo_request("score_after"))
    assert response.status_code == 200
    body = response.json()
    assert body["composite"] == pytest.approx(10.4)
    assert body["band"] == Band.BAND_3.value
    assert body["route"] == Route.COMMITTEE.value


def test_post_score_rejects_a_malformed_body_as_422_problem(client: TestClient) -> None:
    response = client.post("/score", json={"case_id": "X"})
    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["type"].endswith("/validation_failed")
    assert body["errors"]


def test_get_active_model_returns_the_seed_s_v3_model(client: TestClient) -> None:
    response = client.get("/models/active")
    assert response.status_code == 200
    assert response.json()["version"] == 3


def test_get_unknown_model_version_is_a_404_problem(client: TestClient) -> None:
    response = client.get("/models/999")
    assert response.status_code == 404
    body = response.json()
    assert body["type"].endswith("/not_found")
    # exclude_none=True drops trace_id entirely outside a traced request.
    assert "trace_id" not in body or isinstance(body["trace_id"], str)


def test_healthz_is_always_ok(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}


def test_readyz_is_ok_with_no_checks_registered(client: TestClient) -> None:
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


class TestPostScoreRejectsStructurallyInvalidRequests:
    """Each of these used to be *scored* (empty domains gave 0.0 / Band 1 /
    analyst; a repeated domain or control double-counted). They are 422s
    from the contract now, and the engine never sees them."""

    def test_empty_domains_is_422_not_a_zero_score(self, client: TestClient) -> None:
        response = client.post(
            "/score", json={"case_id": "X", "scoring_v": 3, "domains": []}
        )
        assert response.status_code == 422
        assert response.json()["type"].endswith("/validation_failed")

    def test_duplicate_domain_is_422(self, client: TestClient) -> None:
        body = _demo_request("score_before")
        body["domains"].append(body["domains"][0])
        assert client.post("/score", json=body).status_code == 422

    def test_duplicate_control_id_is_422(self, client: TestClient) -> None:
        body = _demo_request("score_before")
        first = body["domains"][0]["controls"][0]
        body["domains"][0]["controls"].append({**first, "catalogue_weight": 0.05})
        assert client.post("/score", json=body).status_code == 422

    def test_control_in_the_wrong_domain_is_422(self, client: TestClient) -> None:
        body = _demo_request("score_before")
        first_domain = body["domains"][0]["domain"]
        other = next(
            d["domain"] for d in body["domains"] if d["domain"] != first_domain
        )
        body["domains"][0]["controls"][0]["domain"] = other
        assert client.post("/score", json=body).status_code == 422

    @pytest.mark.parametrize("bad", [True, "3"])
    def test_non_integer_likelihood_is_422(
        self, client: TestClient, bad: object
    ) -> None:
        body = _demo_request("score_before")
        body["domains"][0]["likelihood"] = bad
        assert client.post("/score", json=body).status_code == 422
