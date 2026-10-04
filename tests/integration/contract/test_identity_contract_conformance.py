"""Contract-conformance for X1 (``fcrm_identity``): its real HTTP
responses, parsed a second time through ``fcrm_contracts.api.identity``,
must match byte-for-byte.

This intentionally does not import ``tests/unit/services/identity/
identity_test_support.py``: that module is a bare (package-less) helper
pytest only puts on ``sys.path`` while collecting its own directory, and
``make test-int`` (``pytest -m integration tests/integration``) never
collects ``tests/unit`` in the same process, so importing it from here
would be fragile (it would only work by accident, when both directories
happen to be collected together). The small amount of app-wiring code
below is a deliberate, minimal, self-contained duplicate of that helper's
shape -- not a second copy of its *test* assertions, which stay in
``tests/unit`` untouched.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import bcrypt
import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fcrm_common.auth import JwksCache, TokenVerifier, install_auth
from fcrm_common.authz import OPAClient, install_authz
from fcrm_common.errors import install_error_handlers
from fcrm_common.health import ReadinessProbe, build_health_router
from fcrm_contracts.api.identity import LoginResponse, MeResponse, ValidateResponse
from fcrm_contracts.enums import Role
from fcrm_identity.jwks import build_jwks
from fcrm_identity.jwtkeys import SigningKey
from fcrm_identity.models import UserRecord
from fcrm_identity.routes import router
from fcrm_identity.settings import IdentitySettings
from fcrm_identity.store import InMemoryIdentityStore
from fcrm_identity.tokens import TokenIssuer
from pydantic import ValidationError

pytestmark = pytest.mark.integration

ISSUER = "fcrm-identity"
AUDIENCE = "fcrm"
PASSWORD = "contract-test-password"


def _build_app() -> FastAPI:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    signing_key = SigningKey(private_key)
    password_hash = bcrypt.hashpw(PASSWORD.encode("utf-8"), bcrypt.gensalt()).decode(
        "utf-8"
    )
    user = UserRecord(
        id="u-riyer",
        name="Rahul Iyer",
        roles=[Role.ANALYST],
        password_hash=password_hash,
        enabled=True,
        created_at=datetime.now(UTC),
    )

    jwks_body = build_jwks(signing_key)

    def respond_jwks(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=jwks_body)

    def respond_opa(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": {"allow": True}})

    jwks_client = httpx.AsyncClient(transport=httpx.MockTransport(respond_jwks))
    token_verifier = TokenVerifier(
        jwks=JwksCache(
            "http://identity.invalid/.well-known/jwks.json", client=jwks_client
        ),
        issuer=ISSUER,
        audience=AUDIENCE,
    )
    opa_client = OPAClient(
        "http://opa.invalid",
        client=httpx.AsyncClient(transport=httpx.MockTransport(respond_opa)),
    )

    app = FastAPI()
    install_error_handlers(app)
    app.state.probe = ReadinessProbe()
    app.include_router(router)
    app.include_router(build_health_router(app.state.probe))
    app.state.store = InMemoryIdentityStore([user])
    app.state.signing_key = signing_key
    app.state.issuer = TokenIssuer(
        signing_key,
        issuer=ISSUER,
        audience=AUDIENCE,
        access_ttl_s=900,
        refresh_ttl_s=28800,
    )
    app.state.settings = IdentitySettings(refresh_cookie_path="/")
    install_auth(app.state, token_verifier)
    install_authz(app.state, opa_client)
    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_build_app())


def test_login_response_round_trips_through_the_contracts_model(
    client: TestClient,
) -> None:
    """``LoginResponse`` has exactly two fields (``access_token``,
    ``expires_in``) -- no refresh token in the body (rule: the refresh
    token is the ``httpOnly`` cookie only). A second, independent parse
    of the raw wire JSON through the contract model is the proof that
    stays true at the wire level, not just in the route's own type hints.
    """
    response = client.post("/login", json={"email": "u-riyer", "password": PASSWORD})
    assert response.status_code == 200
    raw_json = json.loads(response.text)

    reparsed = LoginResponse.model_validate(raw_json)
    assert reparsed.model_dump(mode="json") == raw_json

    with pytest.raises(ValidationError):
        LoginResponse.model_validate({**raw_json, "refresh_token": "should-not-exist"})


def test_me_response_round_trips_and_carries_only_the_declared_role_shape(
    client: TestClient,
) -> None:
    login = client.post("/login", json={"email": "u-riyer", "password": PASSWORD})
    token = login.json()["access_token"]

    response = client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    raw_json = json.loads(response.text)

    reparsed = MeResponse.model_validate(raw_json)
    assert reparsed.roles == [Role.ANALYST]
    assert reparsed.model_dump(mode="json") == raw_json


def test_core_validate_response_round_trips_through_validate_response(
    client: TestClient,
) -> None:
    login = client.post("/login", json={"email": "u-riyer", "password": PASSWORD})
    token = login.json()["access_token"]

    response = client.post(
        "/core/validate", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    raw_json = json.loads(response.text)

    reparsed = ValidateResponse.model_validate(raw_json)
    assert reparsed.valid is True
    assert reparsed.sub == "u-riyer"
    assert reparsed.model_dump(mode="json") == raw_json


def test_jwks_document_is_a_well_formed_rfc7517_set(client: TestClient) -> None:
    """Not an ``fcrm_contracts`` model (JWKS is a public, external
    standard shape, not a TrustOps-owned contract) -- checked here anyway
    because every other service's ``TokenVerifier`` depends on this exact
    shape being stable, and nothing else in this suite exercises
    ``/.well-known/jwks.json`` end to end.
    """
    response = client.get("/.well-known/jwks.json")
    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    assert "keys" in body
    assert len(body["keys"]) == 1
    key = body["keys"][0]
    assert key["kty"] == "RSA"
    assert key["alg"] == "RS256"
    assert key["use"] == "sig"
    assert "kid" in key
    assert "n" in key
    assert "e" in key
