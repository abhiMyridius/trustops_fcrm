"""fcrm_common.auth: RS256/JWKS and HS256 service-token verification, the
``/core/validate`` fallback, and the FastAPI dependencies (design A §8.1).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Annotated

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from fcrm_common.auth import (
    JwksCache,
    TokenVerifier,
    current_user,
    install_auth,
    mint_service_token,
    require_roles,
)
from fcrm_common.cache import InMemoryTTLCache
from fcrm_common.errors import (
    UnauthorizedError,
    UpstreamUnavailableError,
    install_error_handlers,
)
from fcrm_contracts.common import Principal
from fcrm_contracts.enums import Role
from jwt.algorithms import RSAAlgorithm

SECRET = "service-secret-" + "x" * 32
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def jwk(key, kid: str) -> dict:
    data = json.loads(RSAAlgorithm.to_jwk(key.public_key()))
    return {**data, "kid": kid, "use": "sig", "alg": "RS256"}


def token(key=KEY, kid="k1", **overrides) -> str:
    now = datetime.now(UTC)
    claims = {
        "sub": "u-riyer",
        "roles": ["fcrm_analyst"],
        "name": "Rahul Iyer",
        "typ": "access",
        "iat": now,
        "exp": now + timedelta(minutes=15),
        "jti": "j1",
        "iss": "fcrm-identity",
        "aud": "fcrm",
    }
    claims.update(overrides)
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid})


class JwksServer:
    def __init__(self, keys: list[dict]) -> None:
        self.keys = keys
        self.calls = 0
        self.down = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.down:
            raise httpx.ConnectError("down", request=request)
        return httpx.Response(200, json={"keys": self.keys})


def verifier_with(server: JwksServer, **jwks_kwargs) -> TokenVerifier:
    client = httpx.AsyncClient(transport=httpx.MockTransport(server))
    jwks = JwksCache(
        "http://identity/.well-known/jwks.json",
        client=client,
        backoff_s=0,
        **jwks_kwargs,
    )
    return TokenVerifier(jwks=jwks, service_secret=SECRET)


async def test_verify_access_token_against_jwks():
    principal = await verifier_with(JwksServer([jwk(KEY, "k1")])).verify(token())
    assert principal == Principal(
        sub="u-riyer", roles=[Role.ANALYST], name="Rahul Iyer", typ="access"
    )


async def test_jwks_is_cached_between_verifications():
    server = JwksServer([jwk(KEY, "k1")])
    verifier = verifier_with(server)
    await verifier.verify(token())
    await verifier.verify(token())
    assert server.calls == 1


async def test_refresh_token_is_rejected_as_access_token():
    verifier = verifier_with(JwksServer([jwk(KEY, "k1")]))
    with pytest.raises(UnauthorizedError):
        await verifier.verify(token(typ="refresh"))


@pytest.mark.parametrize(
    "overrides",
    [
        {"exp": datetime.now(UTC) - timedelta(minutes=5)},
        {"aud": "other"},
        {"iss": "someone-else"},
        {"roles": ["superuser"]},
    ],
)
async def test_invalid_claims_are_unauthorized(overrides):
    verifier = verifier_with(JwksServer([jwk(KEY, "k1")]))
    with pytest.raises(UnauthorizedError):
        await verifier.verify(token(**overrides))


async def test_token_signed_by_unknown_key_is_rejected():
    verifier = verifier_with(JwksServer([jwk(KEY, "k1")]))
    with pytest.raises(UnauthorizedError):
        await verifier.verify(token(key=OTHER_KEY, kid="k1"))


async def test_unknown_kid_triggers_one_refresh_for_rotation():
    server = JwksServer([jwk(KEY, "k1")])
    verifier = verifier_with(server, min_refresh_interval_s=0)
    await verifier.verify(token())
    server.keys = [jwk(KEY, "k1"), jwk(OTHER_KEY, "k2")]
    principal = await verifier.verify(token(key=OTHER_KEY, kid="k2"))
    assert principal.sub == "u-riyer"
    assert server.calls == 2


async def test_jwks_outage_with_no_cached_keys_is_503():
    server = JwksServer([])
    server.down = True
    with pytest.raises(UpstreamUnavailableError):
        await verifier_with(server).verify(token())
    assert server.calls == 3  # one attempt + two retries


async def test_jwks_outage_after_ttl_keeps_stale_keys():
    server = JwksServer([jwk(KEY, "k1")])
    verifier = verifier_with(server, ttl_s=0)
    await verifier.verify(token())
    server.down = True
    assert (await verifier.verify(token())).sub == "u-riyer"


async def test_service_token_round_trip():
    verifier = verifier_with(JwksServer([]))
    principal = await verifier.verify(mint_service_token(SECRET, "caseflow"))
    assert principal.roles == [Role.SERVICE]
    assert principal.typ == "service"
    assert principal.sub == "svc:caseflow"


async def test_service_token_with_wrong_secret_is_rejected():
    verifier = verifier_with(JwksServer([]))
    with pytest.raises(UnauthorizedError):
        await verifier.verify(mint_service_token("y" * 40, "caseflow"))


async def test_hs256_token_claiming_user_roles_is_rejected():
    now = datetime.now(UTC)
    forged = jwt.encode(
        {
            "sub": "u-riyer",
            "roles": ["fcrm_analyst"],
            "typ": "service",
            "iss": "fcrm-service",
            "aud": "fcrm",
            "iat": now,
            "exp": now + timedelta(minutes=1),
        },
        SECRET,
        algorithm="HS256",
    )
    with pytest.raises(UnauthorizedError):
        await verifier_with(JwksServer([])).verify(forged)


@pytest.mark.parametrize(
    "bad", ["not-a-jwt", jwt.encode({"sub": "x"}, None, algorithm="none")]
)
async def test_malformed_or_unsigned_tokens_are_rejected(bad):
    with pytest.raises(UnauthorizedError):
        await verifier_with(JwksServer([jwk(KEY, "k1")])).verify(bad)


async def test_validate_fallback_used_without_jwks_and_cached():
    calls: list[str] = []

    def identity(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers["authorization"])
        return httpx.Response(
            200, json={"valid": True, "sub": "u-amendes", "roles": ["fcrm_committee"]}
        )

    verifier = TokenVerifier(
        validate_url="http://identity/core/validate",
        client=httpx.AsyncClient(transport=httpx.MockTransport(identity)),
        cache=InMemoryTTLCache(),
    )
    tok = token()
    first = await verifier.verify(tok)
    second = await verifier.verify(tok)
    assert first == second
    assert first.roles == [Role.COMMITTEE]
    assert first.name == "u-amendes"
    assert calls == [f"Bearer {tok}"]


async def test_validate_fallback_rejection_is_401():
    verifier = TokenVerifier(
        validate_url="http://identity/core/validate",
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(401))
        ),
    )
    with pytest.raises(UnauthorizedError):
        await verifier.verify(token())


def make_app(verifier: TokenVerifier) -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)
    install_auth(app.state, verifier)

    @app.get("/me")
    async def me(principal: Annotated[Principal, Depends(current_user)]) -> dict:
        return {"sub": principal.sub}

    @app.get("/queue")
    async def queue(
        principal: Annotated[
            Principal, Depends(require_roles(Role.ANALYST, Role.SERVICE))
        ],
    ) -> dict:
        return {"sub": principal.sub}

    return app


def test_require_roles_end_to_end():
    client = TestClient(make_app(verifier_with(JwksServer([jwk(KEY, "k1")]))))
    assert client.get("/me").status_code == 401
    assert client.get("/me", headers={"Authorization": "Basic abc"}).status_code == 401
    analyst = {"Authorization": f"Bearer {token()}"}
    assert client.get("/queue", headers=analyst).json() == {"sub": "u-riyer"}
    po = {"Authorization": f"Bearer {token(roles=['fcrm_po'])}"}
    forbidden = client.get("/queue", headers=po)
    assert forbidden.status_code == 403
    assert forbidden.headers["content-type"] == "application/problem+json"
    service = {"Authorization": f"Bearer {mint_service_token(SECRET, 'caseflow')}"}
    assert client.get("/queue", headers=service).status_code == 200


def test_require_roles_needs_a_role():
    with pytest.raises(ValueError, match="at least one role"):
        require_roles()
