"""Two real services' full lifespans -- S2 scoring and X1 identity --
run against the *same* live MongoDB 8 replica set in one test process,
each in its own database.

Every existing live-Mongo test (the established scoring one, and this
lane's new identity one) proves exactly one service's lifespan against a
database nobody else touches concurrently. Nothing proves two of this
system's 23 planned containers can share one Mongo instance without
interfering -- index names colliding, one service's seed-on-empty load
racing or blocking another's, a dropped database in one service's
fixture teardown reaching into the other's data. This is the smallest
possible version of that proof that does not need docker-compose: both
real apps, both real lifespans, one real Mongo, in-process.

Two real things were found writing this file, both documented in the
final report rather than silently worked around:

1. A genuine test-harness hazard, not a production bug: ``fastapi.
   testclient.TestClient`` runs each app's ASGI lifespan in its *own*
   background thread with its *own* asyncio event loop. ``fcrm_common.
   mongo.get_client`` caches one ``AsyncMongoClient`` per *URI string*,
   process-wide (``_REGISTRY``) -- correct and necessary in production,
   where one process is one service with one event loop, but if two
   ``TestClient``-driven apps in one pytest process both connect to the
   literal same URI, the second one reuses the first's cached client from
   a *different* event loop and pymongo raises ``RuntimeError: Cannot use
   AsyncMongoClient in different event loop``. Fixed here, not in
   ``fcrm_common`` (the caching is correct for the one-process-one-service
   world this system actually runs in): each service below connects with
   its own ``appName`` query parameter, a distinct string from
   ``_REGISTRY``'s point of view, so each gets its own client, while both
   still point at the one real ``mongod``.
2. A real bug in *this test file's own first draft*, not in production
   code: two pytest fixtures that each called ``monkeypatch.setenv`` for
   ``MONGODB_DB`` both ran to completion before the test body's first
   ``TestClient(...).__enter__()`` (which is what actually triggers
   ``Settings.load()`` reading the environment) -- so by the time either
   app's lifespan read the environment, the *second* fixture's
   ``MONGODB_DB`` value had silently clobbered the first's, and scoring's
   seed data landed in identity's database. Fixed by setting each
   service's environment immediately before entering *that* service's
   ``TestClient``, never both up front.

    export FCRM_TEST_MONGO_URI='mongodb://127.0.0.1:27217/?replicaSet=rs0'
    uv run --project src --all-packages pytest \\
        tests/integration/live_mongo -m integration
"""

from __future__ import annotations

import os
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

import bcrypt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fcrm_common.errors import install_error_handlers
from fcrm_common.health import ReadinessProbe, build_health_router
from fcrm_contracts.enums import Band, Route
from fcrm_identity.lifespan import lifespan as identity_lifespan
from fcrm_identity.routes import router as identity_router
from fcrm_scoring.lifespan import lifespan as scoring_lifespan
from fcrm_scoring.routes import router as scoring_router
from fcrm_scoring.seedmodel import apply_catalogue_weights, score_request_from_case
from pymongo import AsyncMongoClient

URI = os.environ.get("FCRM_TEST_MONGO_URI", "")
# Distinct appName per service, so fcrm_common.mongo's process-wide,
# URI-keyed client cache gives each service its own AsyncMongoClient (see
# point 1 in the module docstring) -- same physical mongod, two cache
# keys.
SCORING_URI = f"{URI}&appName=fcrm-scoring-shared-test" if URI else ""
IDENTITY_URI = f"{URI}&appName=fcrm-identity-shared-test" if URI else ""

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not URI, reason="FCRM_TEST_MONGO_URI not set"),
]

DEMO_PASSWORD = "shared-mongo-demo-password"
SCORING_DB = "fcrm_scoring_shared_test"
IDENTITY_DB = "fcrm_identity_shared_test"


@pytest.fixture
async def clean_dbs() -> None:
    client: AsyncMongoClient = AsyncMongoClient(URI)
    for db_name in (SCORING_DB, IDENTITY_DB):
        await client.drop_database(db_name)
    yield
    for db_name in (SCORING_DB, IDENTITY_DB):
        await client.drop_database(db_name)
    await client.close()


def _build_scoring_app() -> FastAPI:
    """Just the ASGI wiring -- registering ``scoring_lifespan`` does not
    run it; nothing here reads the environment yet.
    """
    app = FastAPI(lifespan=scoring_lifespan)
    install_error_handlers(app)
    app.state.probe = ReadinessProbe()
    app.include_router(scoring_router)
    app.include_router(build_health_router(app.state.probe))
    return app


def _build_identity_app() -> FastAPI:
    app = FastAPI(lifespan=identity_lifespan)
    install_error_handlers(app)
    app.state.probe = ReadinessProbe()
    app.include_router(identity_router)
    app.include_router(build_health_router(app.state.probe))
    return app


def _set_scoring_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MONGODB_URI", SCORING_URI)
    monkeypatch.setenv("MONGODB_DB", SCORING_DB)
    monkeypatch.setenv("SERVICE_TOKEN_SECRET", "x" * 32)
    monkeypatch.setenv("OPA_URL", "http://localhost:8181")
    monkeypatch.setenv("IDENTITY_VALIDATE_URL", "http://localhost:8000/core/validate")
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("SEED_ON_EMPTY", "true")


def _set_identity_env(monkeypatch: pytest.MonkeyPatch, key_path: str) -> None:
    password_hash = bcrypt.hashpw(
        DEMO_PASSWORD.encode("utf-8"), bcrypt.gensalt()
    ).decode("utf-8")
    monkeypatch.setenv("MONGODB_URI", IDENTITY_URI)
    monkeypatch.setenv("MONGODB_DB", IDENTITY_DB)
    monkeypatch.setenv("SERVICE_TOKEN_SECRET", "y" * 32)
    monkeypatch.setenv("OPA_URL", "http://localhost:8181")
    monkeypatch.setenv("IDENTITY_VALIDATE_URL", "http://localhost:8000/core/validate")
    monkeypatch.setenv("JWT_KEY_FILE", key_path)
    monkeypatch.setenv("DEMO_USERS_PASSWORD_HASH", password_hash)
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("SEED_ON_EMPTY", "true")


@pytest.fixture
def identity_key_path(tmp_path) -> str:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    key_path = tmp_path / "jwt.pem"
    key_path.write_bytes(pem)
    return str(key_path)


def test_both_services_start_up_healthy_against_the_same_mongo_instance(
    clean_dbs,
    identity_key_path: str,
    monkeypatch: pytest.MonkeyPatch,
    live_client: Callable[..., AbstractContextManager[TestClient]],
) -> None:
    """Nesting both ``TestClient`` context managers runs both lifespans
    concurrently against one Mongo instance -- if seeding one service's
    collections ever raced or interfered with the other's (shared lock,
    colliding index name, wrong database picked up), one of these two
    ``/readyz`` calls would fail or hang. Each service's environment is
    set immediately before its own ``TestClient`` is entered (see the
    module docstring's point 2) so neither app ever reads the other's
    configuration.
    """
    _set_scoring_env(monkeypatch)
    scoring_app = _build_scoring_app()
    with live_client(scoring_app) as scoring_client:
        scoring_ready = scoring_client.get("/readyz")
        assert scoring_ready.status_code == 200
        assert scoring_ready.json()["checks"]["recompute"]["ok"] is True

        _set_identity_env(monkeypatch, identity_key_path)
        identity_app = _build_identity_app()
        with TestClient(identity_app) as identity_client:
            identity_ready = identity_client.get("/readyz")
            assert identity_ready.status_code == 200


async def test_a_real_score_and_a_real_login_both_work_with_both_services_sharing_mongo(
    clean_dbs,
    identity_key_path: str,
    monkeypatch: pytest.MonkeyPatch,
    demo: dict[str, Any],
    catalogue_weights: dict[str, float],
    live_client: Callable[..., AbstractContextManager[TestClient]],
) -> None:
    """The decisive check, not just "both start": drive one real golden
    score through S2 and one real login through X1 in the same test,
    both against the one shared live Mongo instance, and confirm neither
    service's data leaked into or disturbed the other's collections.
    The score is requested as H1 would: a real HS256 service token
    verified by the ``TokenVerifier`` S2's own lifespan installed.
    """
    _set_scoring_env(monkeypatch)
    scoring_app = _build_scoring_app()
    with live_client(scoring_app) as scoring_client:
        request = score_request_from_case(demo["id"], 3, demo["score_before"]["areas"])
        request = apply_catalogue_weights(request, catalogue_weights)
        score_response = scoring_client.post(
            "/score", json=request.model_dump(mode="json")
        )
        assert score_response.status_code == 200
        body = score_response.json()
        assert body["composite"] == pytest.approx(9.8)
        assert body["band"] == Band.BAND_2.value
        assert body["route"] == Route.ANALYST.value

        _set_identity_env(monkeypatch, identity_key_path)
        identity_app = _build_identity_app()
        with TestClient(identity_app) as identity_client:
            login_response = identity_client.post(
                "/login", json={"email": "u-riyer", "password": DEMO_PASSWORD}
            )
            assert login_response.status_code == 200

    # Cross-contamination check, after both lifespans have shut down:
    # identity's database must not have picked up anything from scoring's
    # collections, and vice versa.
    client: AsyncMongoClient = AsyncMongoClient(URI)
    try:
        scoring_cols = await client[SCORING_DB].list_collection_names()
        identity_cols = await client[IDENTITY_DB].list_collection_names()
        assert "users" not in scoring_cols
        assert "scoring_model_versions" not in identity_cols
    finally:
        await client.close()
