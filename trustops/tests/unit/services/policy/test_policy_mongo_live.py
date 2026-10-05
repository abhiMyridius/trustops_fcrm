"""S3's Mongo-backed keyword collections and the full lifespan against a
real MongoDB 8 replica set (mirrors ``fcrm_scoring``'s own
``test_mongo_live.py``).

Skipped unless ``FCRM_TEST_MONGO_URI`` points at a replica set::

    export FCRM_TEST_MONGO_URI='mongodb://127.0.0.1:27117/?replicaSet=rs0'
    uv run --project src/backend --package fcrm-policy-service pytest \\
        tests/unit/services/policy/test_mongo_live.py
"""

from __future__ import annotations

import os
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path

import pymongo
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fcrm_common.errors import install_error_handlers
from fcrm_common.health import ReadinessProbe, build_health_router
from fcrm_policy.lifespan import lifespan
from fcrm_policy.routes import router
from fcrm_policy.store import POLICY_CLAUSES, PRECEDENT_CASES
from fcrm_seed.loader import generated_clauses
from pymongo import AsyncMongoClient
from pymongo.errors import DuplicateKeyError

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


def test_the_full_lifespan_seeds_mongo_and_the_vector_index(
    app: FastAPI, live_client: LiveClient
) -> None:
    """Start the app for real (runs the lifespan): Mongo gets the seeded
    clauses/precedents/controls and the memory-backed vector index gets
    the whole 197-clause synthetic corpus (ADR-29), including the four
    verbatim demo clauses -- if either failed, ``/readyz`` would still be
    ``ok`` (S3 registers no self-test check, unlike S2), so this asserts
    through an actual search instead.
    """
    with live_client(app) as client:
        ready = client.get("/readyz")
        assert ready.status_code == 200
        assert ready.json()["status"] == "ok"

        response = client.post(
            "/search",
            json={
                "index": POLICY_CLAUSES,
                "query": "screening of payout agents and corridors",
                "top_k": 30,
            },
        )
        assert response.status_code == 200
        ids = [hit["id"] for hit in response.json()["hits"]]
        corpus_ids = {clause["clause_id"] for clause in generated_clauses() or []}
        assert len(corpus_ids) == 197
        # A full page proves far more than the four demo clauses are indexed;
        # every hit must be a real corpus clause, never an invented id.
        assert len(ids) == 30
        assert set(ids) <= corpus_ids
        assert {"FFIEC-TPPP-04", "FCRM42-3.1", "TP71-2.3", "OFAC-1.2"} <= corpus_ids


def test_restarting_the_app_does_not_duplicate_the_mongo_seed(
    app: FastAPI, live_client: LiveClient
) -> None:
    """Two lifespans in a row (simulating a container restart) must not
    insert the seed documents twice -- ``load_if_empty``'s whole point.
    """
    with TestClient(app):
        pass
    with live_client(app) as client:
        response = client.post(
            "/search",
            json={"index": PRECEDENT_CASES, "query": "payout partner", "top_k": 5},
        )
        assert response.status_code == 200
        assert len(response.json()["hits"]) > 0


# ── reference data: jurisdictions, field sets, control inventories ──────────


def _reference_counts() -> dict[str, int]:
    client: pymongo.MongoClient = pymongo.MongoClient(URI)
    try:
        db = client["fcrm_policy"]
        return {
            name: db[name].count_documents({})
            for name in (
                "jurisdiction_list_versions",
                "field_set_versions",
                "control_inventory_versions",
            )
        }
    finally:
        client.close()


def test_the_lifespan_seeds_and_serves_the_reference_data(
    app: FastAPI, live_client: LiveClient
) -> None:
    with live_client(app) as client:
        assert client.get("/jurisdictions").json()["version"] == "v2026-06"
        versions = client.get("/jurisdictions/versions").json()
        assert [(v["version"], v["active"]) for v in versions["items"]] == [
            ("v2026-06", True),
            ("v2026-10-fixture", False),
        ]
        inventory = client.get("/controls").json()
        assert inventory["version"] == "v9"
        assert len(inventory["controls"]) == 24
        assert client.get("/controls/versions").json()["items"] == [
            {"version": "v9", "active": True, "control_count": 24}
        ]
        assert client.get("/field-sets/v6").json()["max_required"] == 18
    assert _reference_counts() == {
        "jurisdiction_list_versions": 2,
        "field_set_versions": 1,
        "control_inventory_versions": 1,
    }


def test_the_mongo_store_answers_exactly_what_the_in_memory_store_does(
    app: FastAPI, live_client: LiveClient, reference_client: TestClient
) -> None:
    queries: list[tuple[str, dict[str, str]]] = [
        ("/jurisdictions", {}),
        ("/jurisdictions", {"version": "fixture"}),
        ("/jurisdictions", {"version": "v2026-10-fixture"}),
        ("/jurisdictions/versions", {}),
        ("/controls", {}),
        ("/controls", {"domain": "channel"}),
        ("/controls", {"version": "9", "domain": "geography"}),
        ("/controls/versions", {}),
        ("/field-sets/v6", {}),
        (
            "/field-sets/latest/resolve",
            {"change_types": "vendor_onboarding,new_geography"},
        ),
        ("/field-sets/6/resolve", {}),
    ]
    with live_client(app) as live:
        for path, params in queries:
            served = live.get(path, params=params)
            expected = reference_client.get(path, params=params)
            assert served.status_code == expected.status_code == 200, path
            assert served.json() == expected.json(), (path, params)


def test_unknown_reference_versions_are_404s_from_mongo_too(
    app: FastAPI, live_client: LiveClient
) -> None:
    with live_client(app) as client:
        assert (
            client.get("/jurisdictions", params={"version": "v1999"}).status_code == 404
        )
        assert client.get("/controls", params={"version": "v10"}).status_code == 404
        assert client.get("/field-sets/v7").status_code == 404


def test_the_reference_indexes_keep_versions_unique_and_one_of_them_active(
    app: FastAPI, live_client: LiveClient
) -> None:
    with live_client(app):
        pass
    client: pymongo.MongoClient = pymongo.MongoClient(URI)
    try:
        db = client["fcrm_policy"]
        for name in (
            "jurisdiction_list_versions",
            "field_set_versions",
            "control_inventory_versions",
        ):
            info = db[name].index_information()
            assert info["version_unique"]["unique"] is True
            assert info["single_active"]["unique"] is True
            assert info["single_active"]["partialFilterExpression"] == {"active": True}
        # A second version document with the same label, or a second active one,
        # is refused by the database itself.
        with pytest.raises(DuplicateKeyError):
            db["field_set_versions"].insert_one(
                {"_id": "v6-copy", "version": "v6", "active": False}
            )
        with pytest.raises(DuplicateKeyError):
            db["control_inventory_versions"].insert_one(
                {"_id": "v10", "version": "v10", "active": True, "controls": []}
            )
        db["control_inventory_versions"].insert_one(
            {"_id": "v10", "version": "v10", "active": False, "controls": []}
        )
    finally:
        client.close()


def test_a_volume_that_predates_one_collection_gets_just_that_one(
    app: FastAPI, live_client: LiveClient
) -> None:
    """A restart with ``SEED_ON_EMPTY`` loads each reference collection only
    if it is empty: dropping one and restarting restores it and leaves the
    other two untouched."""
    with live_client(app):
        pass
    client: pymongo.MongoClient = pymongo.MongoClient(URI)
    try:
        client["fcrm_policy"].drop_collection("control_inventory_versions")
    finally:
        client.close()
    with live_client(app) as running:
        assert len(running.get("/controls").json()["controls"]) == 24
    assert _reference_counts() == {
        "jurisdiction_list_versions": 2,
        "field_set_versions": 1,
        "control_inventory_versions": 1,
    }
