"""BB-11, "auth hygiene" (design doc numbering: "Refresh token as bearer
-> 401; expired access -> 401; role escalation by editing the JWT
payload -> 401").

The first case (refresh token presented as a bearer access token) is
already fully covered, end to end, by ``tests/unit/services/identity/
test_upstream_fixes.py::test_refresh_token_rejected_as_access_token`` (the
exact AEO bug CLAUDE.md's reuse table names) -- not repeated here.

The other two cases this rule names have no existing test anywhere in
the repo (checked: neither ``test_identity_routes.py`` nor
``test_upstream_fixes.py`` constructs an expired token or a
tampered-payload token). This file fills that real gap, against the real
identity app (``fcrm_identity.routes``, real ``TokenIssuer``/
``TokenVerifier`` wiring), not a mock.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import bcrypt
import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fcrm_common.auth import JwksCache, TokenVerifier, install_auth
from fcrm_common.authz import OPAClient, install_authz
from fcrm_common.errors import install_error_handlers
from fcrm_common.health import ReadinessProbe, build_health_router
from fcrm_contracts.enums import Role
from fcrm_identity.jwks import build_jwks
from fcrm_identity.jwtkeys import SigningKey
from fcrm_identity.models import UserRecord
from fcrm_identity.routes import router
from fcrm_identity.settings import IdentitySettings
from fcrm_identity.store import InMemoryIdentityStore
from fcrm_identity.tokens import ALGORITHM, TokenIssuer

pytestmark = pytest.mark.integration

ISSUER = "fcrm-identity"
AUDIENCE = "fcrm"
PASSWORD = "bb11-password"


def _signing_key() -> SigningKey:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return SigningKey(private_key)


def _build_app(signing_key: SigningKey) -> FastAPI:
    user = UserRecord(
        id="u-riyer",
        name="Rahul Iyer",
        roles=[Role.ANALYST],
        password_hash=bcrypt.hashpw(PASSWORD.encode("utf-8"), bcrypt.gensalt()).decode(
            "utf-8"
        ),
        enabled=True,
        created_at=datetime.now(UTC),
    )
    jwks_body = build_jwks(signing_key)

    def respond_jwks(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=jwks_body)

    def respond_opa(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": {"allow": True}})

    token_verifier = TokenVerifier(
        jwks=JwksCache(
            "http://identity.invalid/.well-known/jwks.json",
            client=httpx.AsyncClient(transport=httpx.MockTransport(respond_jwks)),
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
def signing_key() -> SigningKey:
    return _signing_key()


@pytest.fixture
def client(signing_key: SigningKey) -> TestClient:
    return TestClient(_build_app(signing_key))


def test_expired_access_token_is_401(
    client: TestClient, signing_key: SigningKey
) -> None:
    """A token signed by the *real* key the app trusts, with every claim
    correct except an ``exp`` ten minutes in the past -- the signature
    verifies, so this is purely the expiry check, isolated from every
    other way a token could be invalid.
    """
    now = datetime.now(UTC)
    claims = {
        "sub": "u-riyer",
        "roles": [Role.ANALYST.value],
        "name": "Rahul Iyer",
        "typ": "access",
        "iat": now - timedelta(minutes=20),
        "exp": now - timedelta(minutes=10),
        "jti": "jti-expired-1",
        "iss": ISSUER,
        "aud": AUDIENCE,
    }
    expired_token = jwt.encode(claims, signing_key.private_key, algorithm=ALGORITHM)

    response = client.post(
        "/core/validate", headers={"Authorization": f"Bearer {expired_token}"}
    )
    assert response.status_code == 401

    me_response = client.get(
        "/me", headers={"Authorization": f"Bearer {expired_token}"}
    )
    assert me_response.status_code == 401


def test_role_escalation_by_editing_the_jwt_payload_is_401(
    client: TestClient, signing_key: SigningKey
) -> None:
    """The actual attack BB-11 names: take a legitimately-issued token's
    claims, change ``roles`` from analyst to committee, and re-sign --
    but with a key the identity service's JWKS does *not* publish
    (an attacker editing a JWT payload does not also have the server's
    private key). The forged token must fail signature verification,
    never merely "fail the role check" -- a caller should never be able
    to get this far with an escalated role at all.
    """
    now = datetime.now(UTC)
    legitimate_claims = {
        "sub": "u-riyer",
        "roles": [Role.ANALYST.value],
        "name": "Rahul Iyer",
        "typ": "access",
        "iat": now,
        "exp": now + timedelta(minutes=15),
        "jti": "jti-legit-1",
        "iss": ISSUER,
        "aud": AUDIENCE,
    }
    attacker_key = _signing_key()  # the attacker's own keypair, not the server's
    forged_claims = {**legitimate_claims, "roles": [Role.COMMITTEE.value]}
    forged_token = jwt.encode(
        forged_claims, attacker_key.private_key, algorithm=ALGORITHM
    )

    response = client.post(
        "/core/validate", headers={"Authorization": f"Bearer {forged_token}"}
    )
    assert response.status_code == 401

    me_response = client.get("/me", headers={"Authorization": f"Bearer {forged_token}"})
    assert me_response.status_code == 401


def test_a_token_with_alg_none_is_rejected_outright(
    client: TestClient, signing_key: SigningKey
) -> None:
    """The classic ``alg: none`` forgery (an unsigned token an attacker
    can construct without any key at all): ``TokenVerifier.verify``'s own
    algorithm dispatch only recognises ``"HS256"`` (service tokens) and
    ``"RS256"`` (identity tokens) -- anything else, including ``"none"``,
    is rejected outright as "Unsupported token algorithm" before the
    token's claims are ever inspected, let alone a signature checked.
    """
    now = datetime.now(UTC)
    claims = {
        "sub": "u-riyer",
        "roles": [Role.COMMITTEE.value],
        "name": "Rahul Iyer",
        "typ": "access",
        "iat": now,
        "exp": now + timedelta(minutes=15),
        "jti": "jti-none-1",
        "iss": ISSUER,
        "aud": AUDIENCE,
    }
    none_token = jwt.encode(claims, key=None, algorithm="none")

    response = client.post(
        "/core/validate", headers={"Authorization": f"Bearer {none_token}"}
    )
    assert response.status_code == 401
