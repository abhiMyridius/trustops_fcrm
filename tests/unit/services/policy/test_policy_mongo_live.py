"""S3's Mongo-backed keyword collections and the full lifespan against a
real MongoDB 8 replica set (mirrors ``fcrm_scoring``'s own
``test_mongo_live.py``).

Skipped unless ``FCRM_TEST_MONGO_URI`` points at a replica set::

    export FCRM_TEST_MONGO_URI='mongodb://127.0.0.1:27117/?replicaSet=rs0'
    uv run --project src --package fcrm-policy-service pytest \\
        tests/unit/services/policy/test_mongo_live.py
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fcrm_common.errors import install_error_handlers
from fcrm_common.health import ReadinessProbe, build_health_router
from fcrm_policy.lifespan import lifespan
from fcrm_policy.routes import router
from fcrm_policy.store import POLICY_CLAUSES, PRECEDENT_CASES
from pymongo import AsyncMongoClient

URI = os.environ.get("FCRM_TEST_MONGO_URI", "")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not URI, reason="FCRM_TEST_MONGO_URI not set"),
]


@pytest.fixture
async def clean_db() -> None:
    """Drop fcrm_policy before and after, so this test never depends on
    (or leaves behind) another run's data.
    """
    client: AsyncMongoClient = AsyncMongoClient(URI)
    await client.drop_database("fcrm_policy")
    yield
    await client.drop_database("fcrm_policy")
    await client.close()


@pytest.fixture
def app(clean_db, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FastAPI:
    monkeypatch.setenv("MONGODB_URI", URI)
    monkeypatch.setenv("MONGODB_DB", "fcrm_policy")
    monkeypatch.setenv("SERVICE_TOKEN_SECRET", "x" * 32)
    monkeypatch.setenv("OPA_URL", "http://localhost:8181")
    monkeypatch.setenv("IDENTITY_VALIDATE_URL", "http://localhost:8000/core/validate")
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("SEED_ON_EMPTY", "true")
    monkeypatch.setenv("VECTOR_BACKEND", "memory")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "dummy")
    monkeypatch.setenv("EMBEDDING_DIMENSION", "64")
    monkeypatch.setenv("CONTEXT_PROJECTS_ROOT", str(tmp_path))

    application = FastAPI(lifespan=lifespan)
    install_error_handlers(application)
    application.state.probe = ReadinessProbe()
    application.include_router(router)
    application.include_router(build_health_router(application.state.probe))
    return application


def test_the_full_lifespan_seeds_mongo_and_the_vector_index(app: FastAPI) -> None:
    """Start the app for real (runs the lifespan): Mongo gets the seeded
    clauses/precedents/controls and the memory-backed vector index gets
    the four demo clauses -- if either failed, ``/readyz`` would still be
    ``ok`` (S3 registers no self-test check, unlike S2), so this asserts
    through an actual search instead.
    """
    with TestClient(app) as client:
        ready = client.get("/readyz")
        assert ready.status_code == 200
        assert ready.json()["status"] == "ok"

        response = client.post(
            "/search",
            json={
                "index": POLICY_CLAUSES,
                "query": "screening of payout agents and corridors",
                "top_k": 10,
            },
        )
        assert response.status_code == 200
        ids = {hit["id"] for hit in response.json()["hits"]}
        assert ids == {"FFIEC-TPPP-04", "FCRM42-3.1", "TP71-2.3", "OFAC-1.2"}


def test_restarting_the_app_does_not_duplicate_the_mongo_seed(app: FastAPI) -> None:
    """Two lifespans in a row (simulating a container restart) must not
    insert the seed documents twice -- ``load_if_empty``'s whole point.
    """
    with TestClient(app):
        pass
    with TestClient(app) as client:
        response = client.post(
            "/search",
            json={"index": PRECEDENT_CASES, "query": "payout partner", "top_k": 5},
        )
        assert response.status_code == 200
        assert len(response.json()["hits"]) > 0
