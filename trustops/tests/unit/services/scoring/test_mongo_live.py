"""S2's Mongo-backed store and the full lifespan against a real MongoDB 8
replica set.

Skipped unless ``FCRM_TEST_MONGO_URI`` points at a replica set, exactly
like ``fcrm_common``'s own live tests::

    export FCRM_TEST_MONGO_URI='mongodb://127.0.0.1:27117/?replicaSet=rs0'
    uv run --project src/backend --package fcrm-scoring-service pytest \\
        tests/unit/services/scoring/test_mongo_live.py
"""

from __future__ import annotations

import os
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fcrm_common.errors import install_error_handlers
from fcrm_common.health import ReadinessProbe, build_health_router
from fcrm_contracts.enums import Band, Role
from fcrm_scoring.lifespan import lifespan
from fcrm_scoring.routes import router
from fcrm_scoring.simulate import HttpCaseInputs, UnconfiguredCaseInputs
from pymongo import AsyncMongoClient

#: ``tests/conftest.py``'s ``live_client``: real lifespan, real HS256
#: service token, allow-all OPA stub.
LiveClient = Callable[..., AbstractContextManager[TestClient]]

URI = os.environ.get("FCRM_TEST_MONGO_URI", "")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not URI, reason="FCRM_TEST_MONGO_URI not set"),
]


@pytest.fixture
async def clean_db() -> None:
    """Drop fcrm_scoring before and after, so this test never depends on
    (or leaves behind) another run's data.
    """
    client: AsyncMongoClient = AsyncMongoClient(URI)
    await client.drop_database("fcrm_scoring")
    yield
    await client.drop_database("fcrm_scoring")
    await client.close()


@pytest.fixture
def app(clean_db, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setenv("MONGODB_URI", URI)
    monkeypatch.setenv("MONGODB_DB", "fcrm_scoring")
    monkeypatch.setenv("SERVICE_TOKEN_SECRET", "x" * 32)
    monkeypatch.setenv("OPA_URL", "http://localhost:8181")
    monkeypatch.setenv("IDENTITY_VALIDATE_URL", "http://localhost:8000/core/validate")
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("SEED_ON_EMPTY", "true")

    application = FastAPI(lifespan=lifespan)
    install_error_handlers(application)
    application.state.probe = ReadinessProbe()
    application.include_router(router)
    application.include_router(build_health_router(application.state.probe))
    return application


def test_the_full_lifespan_seeds_mongo_and_passes_its_own_recompute_self_test(
    app: FastAPI, live_client: LiveClient
) -> None:
    """Start the app for real (runs the lifespan): Mongo gets the seeded
    v3 model and v4 routing rules, and the startup self-test recomputes
    all 80 seed cases against them -- if that disagreed, the app would
    never finish starting up (``TestClient`` would raise).
    """
    with live_client(app) as client:
        ready = client.get("/readyz")
        assert ready.status_code == 200
        assert ready.json()["status"] == "ok"
        assert ready.json()["checks"]["recompute"]["ok"] is True

        active = client.get("/models/active")
        assert active.status_code == 200
        assert active.json()["version"] == 3

        routing = client.get("/routing/active")
        assert routing.status_code == 200
        assert routing.json()["version"] == 4


def test_restarting_the_app_does_not_duplicate_the_seed(
    app: FastAPI, live_client: LiveClient
) -> None:
    """Two lifespans in a row (simulating a container restart) must not
    insert the model twice -- load_if_empty's whole point.
    """
    with TestClient(app):
        pass
    with live_client(app) as client:
        response = client.get("/models/active")
        assert response.status_code == 200
        assert response.json()["version"] == 3


def test_post_score_through_the_real_mongo_backed_store(
    app: FastAPI, live_client: LiveClient
) -> None:
    with live_client(app) as client:
        request = {
            "case_id": "FCRM-2026-0151",
            "scoring_v": 3,
            "domains": [
                {
                    "domain": "channel",
                    "likelihood": 4,
                    "impact": 5,
                    "controls": [
                        {
                            "control_id": "CTL-T01",
                            "domain": "channel",
                            "rating": 7.6,
                            "catalogue_weight": 0.28,
                        }
                    ],
                }
            ],
        }
        response = client.post("/score", json=request)
        assert response.status_code == 200
        assert response.json()["band"] in {b.value for b in Band}


def test_the_lifespan_leaves_simulate_refusing_until_the_case_service_is_set(
    app: FastAPI, live_client: LiveClient
) -> None:
    with live_client(app):
        assert isinstance(app.state.case_inputs, UnconfiguredCaseInputs)


def test_the_lifespan_wires_the_case_service_from_the_setting_and_closes_it(
    app: FastAPI, live_client: LiveClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CASE_SERVICE_URL", "http://case.invalid/api/fcrm")

    with live_client(app):
        assert isinstance(app.state.case_inputs, HttpCaseInputs)
    # leaving the context ran the shutdown, which closed S2's one client


def test_simulate_scores_over_the_model_mongo_holds(
    app: FastAPI,
    live_client: LiveClient,
    wire_test_auth: Callable[[FastAPI], FastAPI],
    act_as: Callable[[Role, str], None],
    stub_case_inputs: Callable[..., Any],
) -> None:
    """The real lifespan and the Mongo-backed model, with S1 stubbed and the
    caller acting as the analyst: the walkthrough's override preview, 9.8 in
    Band 2 to 10.4 in Band 3.
    """
    with live_client(app) as client:
        wire_test_auth(app)
        act_as(Role.ANALYST, "u-riyer")
        app.state.case_inputs = stub_case_inputs()

        response = client.post(
            "/simulate",
            json={"case_id": "FCRM-2026-0151", "control_id": "CTL-T01", "value": 4.5},
        )

        assert response.status_code == 200, response.text
        assert response.json()["composite"] == [9.8, 10.4]
        assert response.json()["band"] == [2, 3]
