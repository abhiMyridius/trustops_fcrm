"""S2's Mongo-backed store and the full lifespan against a real MongoDB 8
replica set.

Skipped unless ``FCRM_TEST_MONGO_URI`` points at a replica set, exactly
like ``fcrm_common``'s own live tests::

    export FCRM_TEST_MONGO_URI='mongodb://127.0.0.1:27117/?replicaSet=rs0'
    uv run --project src --package fcrm-scoring-service pytest \\
        tests/unit/services/scoring/test_mongo_live.py
"""

from __future__ import annotations

import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fcrm_common.errors import install_error_handlers
from fcrm_common.health import ReadinessProbe, build_health_router
from fcrm_contracts.enums import Band
from fcrm_scoring.lifespan import lifespan
from fcrm_scoring.routes import router
from pymongo import AsyncMongoClient

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
    app: FastAPI,
) -> None:
    """Start the app for real (runs the lifespan): Mongo gets the seeded
    v3 model and v4 routing rules, and the startup self-test recomputes
    all 80 seed cases against them -- if that disagreed, the app would
    never finish starting up (``TestClient`` would raise).
    """
    with TestClient(app) as client:
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


def test_restarting_the_app_does_not_duplicate_the_seed(app: FastAPI) -> None:
    """Two lifespans in a row (simulating a container restart) must not
    insert the model twice -- load_if_empty's whole point.
    """
    with TestClient(app):
        pass
    with TestClient(app) as client:
        response = client.get("/models/active")
        assert response.status_code == 200
        assert response.json()["version"] == 3


def test_post_score_through_the_real_mongo_backed_store(app: FastAPI) -> None:
    with TestClient(app) as client:
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
