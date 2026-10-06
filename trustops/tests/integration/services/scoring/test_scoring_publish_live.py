"""S2's routing-rules read and its publish routes against a real MongoDB 8
replica set, through the service's real lifespan.

What only a real Mongo proves: a published model becomes the active one in one
transaction while the seed's v3 stays readable, the startup self-test (which
recomputes the 80 seeded cases) keeps passing after a newer model is active and
across a restart, at most one version is ever active (the partial unique index),
and two concurrent publishes of one version leave a single winner.

Skipped unless ``FCRM_TEST_MONGO_URI`` points at a replica set::

    export FCRM_TEST_MONGO_URI='mongodb://127.0.0.1:28817/?replicaSet=rs0'
    uv run --project src/backend --all-packages pytest \\
        tests/integration/services/scoring -m integration
"""

from __future__ import annotations

import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager
from typing import Any

import pymongo
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fcrm_common.errors import install_error_handlers
from fcrm_common.health import ReadinessProbe, build_health_router
from fcrm_scoring.lifespan import lifespan
from fcrm_scoring.routes import router
from pymongo import AsyncMongoClient
from pymongo.errors import DuplicateKeyError

LiveClient = Callable[..., AbstractContextManager[TestClient]]

URI = os.environ.get("FCRM_TEST_MONGO_URI", "")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not URI, reason="FCRM_TEST_MONGO_URI not set"),
]

WEIGHTS = {"customer": 0.25, "product": 0.2, "geography": 0.24, "channel": 0.31}


@pytest.fixture
async def clean_db() -> None:
    """Drop fcrm_scoring before and after, so a test never depends on another
    run's data.
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


def model_body(client: TestClient, **changes: Any) -> dict[str, Any]:
    """A publish of v4 built on whatever model v3 is, with ``changes``."""
    v3 = client.get("/models/3").json()
    model = {**v3, "version": 4, "active": True, "domain_weights": WEIGHTS, **changes}
    return {"base_version": 3, "published_by": "Rahul Iyer", "model": model}


def routing_body(client: TestClient, **changes: Any) -> dict[str, Any]:
    v4 = client.get("/routing-rules/v4").json()
    routing = {
        "version": 5,
        "rules": [
            {"rule_id": r["id"], "label": r["id"], "description": r["rule"]}
            for r in v4["rules"]
        ],
        "committee_quorum": 5,
        "parameters": {"quorum": "5"},
        "active": True,
        **changes,
    }
    return {"base_version": 4, "published_by": "Rahul Iyer", "routing": routing}


def test_the_seeded_routing_rules_are_read_by_version_and_as_active(
    app: FastAPI, live_client: LiveClient
) -> None:
    with live_client(app) as client:
        for spelling in ("v4", "4", "active"):
            body = client.get(f"/routing-rules/{spelling}").json()
            assert body["version"] == "v4"
            assert [r["id"] for r in body["rules"]] == [
                "R1",
                "R2",
                "R3",
                "R4",
                "R5",
                "R6",
            ]
            assert body["committee_quorum"] == 3
        assert client.get("/routing-rules/v9").status_code == 404


def test_a_published_model_is_active_the_seeded_one_stays_and_the_self_test_holds(
    app: FastAPI, live_client: LiveClient
) -> None:
    """The startup recompute replays the 80 seeded cases against the model they
    were scored with (v3), not whichever is active: with v4 active the service
    stays ready now and starts again after a restart.
    """
    with live_client(app) as client:
        published = client.post("/internal/models", json=model_body(client))
        assert published.status_code == 200, published.text
        assert published.json() == {"version": 4, "created": True}
        assert client.get("/models/active").json()["version"] == 4
        old = client.get("/models/3").json()
        assert old["active"] is False
        assert old["domain_weights"]["channel"] == 0.36
        ready = client.get("/readyz")
        assert ready.status_code == 200
        assert ready.json()["checks"]["recompute"]["ok"] is True
    with live_client(app) as client:  # a restart with v4 active
        assert client.get("/readyz").status_code == 200
        assert client.get("/models/active").json()["version"] == 4


def test_a_published_model_scores_new_cases_and_old_cases_keep_their_version(
    app: FastAPI, live_client: LiveClient
) -> None:
    request = {
        "case_id": "FCRM-2026-0151",
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
            },
            {
                "domain": "customer",
                "likelihood": 4,
                "impact": 4,
                "controls": [
                    {
                        "control_id": "CTL-C01",
                        "domain": "customer",
                        "rating": 9.0,
                        "catalogue_weight": 0.18,
                    }
                ],
            },
        ],
    }
    with live_client(app) as client:
        client.post("/internal/models", json=model_body(client))
        pinned = client.post("/score", json={**request, "scoring_v": 3}).json()
        newer = client.post("/score", json={**request, "scoring_v": 4}).json()
    assert pinned["scoring_v"] == 3
    assert newer["scoring_v"] == 4
    assert pinned["composite"] != newer["composite"]


def test_a_repeat_of_the_same_publish_is_answered_and_writes_nothing(
    app: FastAPI, live_client: LiveClient
) -> None:
    with live_client(app) as client:
        body = model_body(client)
        client.post("/internal/models", json=body)
        again = client.post("/internal/models", json=body)
        assert again.json() == {"version": 4, "created": False}
    mongo = pymongo.MongoClient(URI)
    try:
        assert mongo["fcrm_scoring"]["scoring_model_versions"].count_documents({}) == 2
    finally:
        mongo.close()


def test_a_stale_base_and_an_invalid_model_are_refused_and_change_nothing(
    app: FastAPI, live_client: LiveClient
) -> None:
    with live_client(app) as client:
        stale = client.post(
            "/internal/models", json=model_body(client) | {"base_version": 2}
        )
        assert stale.status_code == 409
        broken = client.post(
            "/internal/models",
            json=model_body(client, domain_weights={**WEIGHTS, "channel": 0.9}),
        )
        assert broken.status_code == 422
        assert broken.json()["errors"][0]["loc"] == "domain_weights"
        assert client.get("/models/active").json()["version"] == 3
        assert client.get("/models/4").status_code == 404


def test_routing_rules_publish_and_the_old_version_stays_readable(
    app: FastAPI, live_client: LiveClient
) -> None:
    with live_client(app) as client:
        response = client.post("/internal/routing-rules", json=routing_body(client))
        assert response.status_code == 200, response.text
        assert client.get("/routing-rules/active").json()["version"] == "v5"
        assert client.get("/routing-rules/v4").json()["committee_quorum"] == 3
        new = client.get("/routing-rules/v5").json()
        assert (new["committee_quorum"], new["parameters"]) == (5, {"quorum": "5"})
        assert client.get("/routing/active").json()["version"] == 5


def test_at_most_one_version_is_active_in_the_database(
    app: FastAPI, live_client: LiveClient
) -> None:
    with live_client(app):
        pass  # the lifespan creates the indexes
    mongo = pymongo.MongoClient(URI)
    try:
        for name in ("scoring_model_versions", "routing_rules_versions"):
            info = mongo["fcrm_scoring"][name].index_information()
            assert info["single_active"]["unique"] is True
            assert info["single_active"]["partialFilterExpression"] == {"active": True}
        with pytest.raises(DuplicateKeyError):
            mongo["fcrm_scoring"]["scoring_model_versions"].insert_one(
                {"_id": 99, "version": 99, "active": True}
            )
    finally:
        mongo.close()


def test_two_concurrent_publishes_of_one_version_leave_a_single_winner(
    app: FastAPI, live_client: LiveClient
) -> None:
    with live_client(app) as client:
        first = model_body(client, boundary_flag_distance=0.6)
        second = model_body(client, boundary_flag_distance=0.7)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(
                    lambda body: client.post("/internal/models", json=body).status_code,
                    [first, second],
                )
            )
        assert sorted(results) == [200, 409]
        winner = client.get("/models/4").json()
        assert winner["boundary_flag_distance"] in (0.6, 0.7)
        assert client.get("/models/active").json()["version"] == 4
    mongo = pymongo.MongoClient(URI)
    try:
        docs = list(
            mongo["fcrm_scoring"]["scoring_model_versions"].find({"version": 4})
        )
        assert len(docs) == 1
        assert (
            mongo["fcrm_scoring"]["scoring_model_versions"].count_documents(
                {"active": True}
            )
            == 1
        )
    finally:
        mongo.close()
