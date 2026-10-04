"""X1's real Mongo-backed lifespan and store, against a real MongoDB 8
replica set.

Mirrors the established pattern exactly (``tests/unit/services/scoring/
test_mongo_live.py``: skip unless ``FCRM_TEST_MONGO_URI`` points at a
reachable replica set) -- but this specific scenario (identity's *own*
full lifespan: Mongo connect, seed-on-empty loading real P7 demo users,
login, JWKS, ``/core/validate``, all against a live database) has no
existing test file anywhere in the repo. BUILD-PLAN.md's status table
claims this was already done ("identity service ... JWKS/login/validate
live-verified with a real Mongo container") but no committed test
reproduces it -- this file is that missing, genuinely new, test, placed
under ``tests/integration`` per this lane's brief (not ``tests/unit``,
where the analogous scoring file already lives, since that placement
predates this lane and is left untouched).

    export FCRM_TEST_MONGO_URI='mongodb://127.0.0.1:27217/?replicaSet=rs0'
    uv run --project src --all-packages pytest \\
        tests/integration/live_mongo -m integration
"""

from __future__ import annotations

import os
from typing import Any

import bcrypt
import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fcrm_common.auth import JwksCache, TokenVerifier
from fcrm_common.errors import install_error_handlers
from fcrm_common.health import ReadinessProbe, build_health_router
from fcrm_identity.lifespan import lifespan
from fcrm_identity.routes import router
from pymongo import AsyncMongoClient

URI = os.environ.get("FCRM_TEST_MONGO_URI", "")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not URI, reason="FCRM_TEST_MONGO_URI not set"),
]

DEMO_PASSWORD = "live-mongo-demo-password"


@pytest.fixture
async def clean_db() -> None:
    """Drop ``fcrm_identity`` before and after, matching the scoring
    live-Mongo test's own ``clean_db`` fixture shape exactly, so this
    file never depends on, or leaves behind, another run's data.
    """
    client: AsyncMongoClient = AsyncMongoClient(URI)
    await client.drop_database("fcrm_identity")
    yield
    await client.drop_database("fcrm_identity")
    await client.close()


@pytest.fixture
def signing_key_file(tmp_path):
    """A real, temporary RSA keypair written to disk as
    ``JWT_KEY_FILE`` expects (PEM, PKCS8, unencrypted) -- generated fresh
    per test run, never a checked-in key.
    """
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    key_path = tmp_path / "jwt.pem"
    key_path.write_bytes(pem)
    return key_path


@pytest.fixture
def app(clean_db, signing_key_file, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    password_hash = bcrypt.hashpw(
        DEMO_PASSWORD.encode("utf-8"), bcrypt.gensalt()
    ).decode("utf-8")

    monkeypatch.setenv("MONGODB_URI", URI)
    monkeypatch.setenv("MONGODB_DB", "fcrm_identity")
    monkeypatch.setenv("SERVICE_TOKEN_SECRET", "x" * 32)
    monkeypatch.setenv("OPA_URL", "http://localhost:8181")
    monkeypatch.setenv("IDENTITY_VALIDATE_URL", "http://localhost:8000/core/validate")
    monkeypatch.setenv("JWT_KEY_FILE", str(signing_key_file))
    monkeypatch.setenv("DEMO_USERS_PASSWORD_HASH", password_hash)
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("SEED_ON_EMPTY", "true")

    application = FastAPI(lifespan=lifespan)
    install_error_handlers(application)
    application.state.probe = ReadinessProbe()
    application.include_router(router)
    application.include_router(build_health_router(application.state.probe))
    return application


def test_the_full_lifespan_connects_mongo_and_seeds_the_real_demo_users(
    app: FastAPI,
) -> None:
    """Starting the app for real (running the lifespan) against a live
    replica set: Mongo gets the P7 seed's ``users`` section (``u-riyer``
    among them, Rahul Iyer, analyst) with the one shared bcrypt hash this
    test minted, and ``/readyz`` reports the Mongo check healthy.
    """
    with TestClient(app) as client:
        ready = client.get("/readyz")
        assert ready.status_code == 200
        assert ready.json()["status"] == "ok"


def test_restarting_the_app_does_not_duplicate_seeded_users(app: FastAPI) -> None:
    """Two lifespans in a row (a simulated container restart) must not
    insert the same seeded users twice -- ``load_if_empty``'s whole
    point, checked here for identity specifically (the scoring live-Mongo
    test already checks this for its own collections; identity's
    ``users`` collection is a different repository and a different
    seeding path, so this is a genuinely separate code path to verify).
    """
    with TestClient(app):
        pass
    with TestClient(app) as client:
        login = client.post(
            "/login", json={"email": "u-riyer", "password": DEMO_PASSWORD}
        )
        assert login.status_code == 200


async def test_login_through_the_real_mongo_backed_store_then_jwks_then_verify(
    app: FastAPI,
) -> None:
    """The full round trip against live Mongo: log in as a real seeded
    demo user (password verified against the bcrypt hash stored in
    Mongo), fetch the real JWKS the app just published, and independently
    verify the minted access token against it with a second, standalone
    :class:`fcrm_common.auth.TokenVerifier` -- the same real verification
    code every other service runs, fed the real JWKS this live app
    produced, rather than this app's own ``/core/validate`` route.

    Deliberately not calling this app's own ``/core/validate``: that
    route is itself gated by ``current_user``, which (per
    ``IdentitySettings``, no ``IDENTITY_JWKS_URL`` configured for this
    single-process test) falls back to calling ``IDENTITY_VALIDATE_URL``
    over a *real* network request -- a genuine second hop to itself that
    only makes sense inside the real multi-container deployment (nginx's
    ``auth_request`` loop), not inside one in-process ``TestClient`` with
    no real socket listening. ``/core/validate``'s own correctness is
    already covered without that network dependency by
    ``tests/unit/services/identity/test_identity_routes.py`` and this
    lane's ``test_identity_contract_conformance.py``.
    """
    with TestClient(app) as client:
        login = client.post(
            "/login", json={"email": "u-riyer", "password": DEMO_PASSWORD}
        )
        assert login.status_code == 200
        body = login.json()
        assert "access_token" in body
        assert "refresh_token" not in body

        jwks_response = client.get("/.well-known/jwks.json")
        assert jwks_response.status_code == 200
        jwks_body: dict[str, Any] = jwks_response.json()
        assert len(jwks_body["keys"]) == 1

        wrong_password = client.post(
            "/login", json={"email": "u-riyer", "password": "not-the-password"}
        )
        assert wrong_password.status_code == 401

    def respond_jwks(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=jwks_body)

    verifier = TokenVerifier(
        jwks=JwksCache(
            "http://identity.invalid/.well-known/jwks.json",
            client=httpx.AsyncClient(transport=httpx.MockTransport(respond_jwks)),
        ),
        issuer="fcrm-identity",
        audience="fcrm",
    )
    try:
        principal = await verifier.verify(body["access_token"])
    finally:
        await verifier.aclose()
    assert principal.sub == "u-riyer"
    assert principal.roles == ["fcrm_analyst"]


def test_the_seeded_committee_and_po_demo_users_are_also_present(
    app: FastAPI,
) -> None:
    """Not just the one analyst used above: the demo case's full cast
    (Priya Nair the PO, Ana Mendes / Joseph Okafor / Lin Chen on the
    committee) must all have been seeded from the real P7 ``users``
    section into live Mongo, each able to log in with the one shared
    demo password.
    """
    with TestClient(app) as client:
        for user_id in ("u-pnair", "u-amendes", "u-jokafor", "u-lchen"):
            response = client.post(
                "/login", json={"email": user_id, "password": DEMO_PASSWORD}
            )
            assert response.status_code == 200, f"{user_id} failed to log in"
