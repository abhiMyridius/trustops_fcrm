"""Shared, plain-function test helpers for the identity-service suite.

Not a ``conftest.py``: pytest fixtures stay local to each test file, the
pattern already used in this repo (``tests/unit/services/scoring/test_routes.py``'s
``app``/``client`` fixtures, ``tests/unit/packages/common/test_common_settings.py``'s
``clean_env``). This module holds the plain builder functions more than
one of this lane's test files calls, imported as a bare module name
(there is no ``__init__.py`` under ``tests/``, so pytest's default import
mode adds this directory to ``sys.path`` rather than treating it as a
package) -- named distinctively (not ``support`` or ``helpers``) so it
cannot collide with another lane's same-named module under pytest's flat
module cache.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Optional

import bcrypt
import httpx
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
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
from fcrm_identity.tokens import TokenIssuer

ISSUER = "fcrm-identity"
AUDIENCE = "fcrm"
DEMO_PASSWORD = "correct-demo-password"


def make_signing_key() -> SigningKey:
    """A fresh, test-only RSA keypair (never written to disk)."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return SigningKey(private_key)


def make_issuer(signing_key: SigningKey) -> TokenIssuer:
    return TokenIssuer(
        signing_key,
        issuer=ISSUER,
        audience=AUDIENCE,
        access_ttl_s=900,
        refresh_ttl_s=28800,
    )


def make_settings(**overrides: Any) -> IdentitySettings:
    """An :class:`IdentitySettings` built directly (not ``.load()``), so
    no environment variables are needed: every field has a usable
    default except the two secrets, which route tests never read off
    ``IdentitySettings`` itself (the signing key and password hash are
    wired in separately).

    ``refresh_cookie_path`` defaults to ``"/"`` here, overriding
    ``IdentitySettings``'s own production default (``/api/fcrm/auth``,
    design A §8.1 -- correct once D-13's ``mount_at_root_path`` puts the
    real app behind that prefix, which is what the browser's cookie jar
    actually matches against). ``build_app()`` below serves the *bare*,
    unprefixed app ("create_app() keeps returning the bare app ... what
    unit tests exercise directly" per the D-13 convention), so a cookie
    scoped to ``/api/fcrm/auth`` would never be sent back on a test
    request to ``/refresh`` -- this is a test-harness concern, not a
    production one, so it is fixed here rather than by changing the
    shipped default.
    """
    fields: dict[str, Any] = {
        "refresh_cookie_path": "/",
        # Pinned so the shell's own ENV / DEMO_USERS_ENABLED never leaks in:
        # demo login is off unless a test turns it on.
        "env": "local",
        "demo_users_enabled": False,
        **overrides,
    }
    return IdentitySettings(**fields)


def make_opa_client(*, allow: bool = True) -> OPAClient:
    """An :class:`OPAClient` whose transport never leaves the process:
    every decision is ``allow`` (default) or always denied.
    """

    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": {"allow": allow}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    return OPAClient("http://opa.invalid", client=client)


def make_token_verifier(signing_key: SigningKey) -> TokenVerifier:
    """A :class:`TokenVerifier` whose JWKS "fetch" is served in-process
    from ``signing_key`` (``httpx.MockTransport``, the pattern
    ``tests/unit/packages/common/test_common_auth.py`` uses), so
    verifying a token this suite minted never makes a real HTTP call.
    """
    jwks_body = build_jwks(signing_key)

    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=jwks_body)

    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    jwks_cache = JwksCache(
        "http://identity.invalid/.well-known/jwks.json", client=client
    )
    return TokenVerifier(jwks=jwks_cache, issuer=ISSUER, audience=AUDIENCE)


def make_user(
    user_id: str,
    name: str,
    role: Role,
    *,
    password: str = DEMO_PASSWORD,
    title: str = "",
    unit: Optional[str] = None,
    enabled: bool = True,
) -> UserRecord:
    """A seeded-looking user whose password is ``password`` (bcrypt-hashed
    here so tests can log in with the plaintext).
    """
    password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode(
        "utf-8"
    )
    return UserRecord(
        id=user_id,
        name=name,
        roles=[role],
        title=title,
        unit=unit,
        password_hash=password_hash,
        enabled=enabled,
        created_at=datetime.now(UTC),
    )


def build_app(
    users: list[UserRecord],
    *,
    opa_allow: bool = True,
    **settings_overrides: Any,
) -> tuple[FastAPI, SigningKey]:
    """A fully wired identity app over :class:`InMemoryIdentityStore`,
    plus the signing key it was built with (so a test can mint its own
    tokens, e.g. an expired or wrong-``typ`` one, with the same key the
    app's ``TokenVerifier`` trusts). ``settings_overrides`` go to
    :func:`make_settings` (e.g. ``demo_users_enabled=True, env="local"``).
    """
    signing_key = make_signing_key()
    app = FastAPI()
    install_error_handlers(app)
    app.state.probe = ReadinessProbe()
    app.include_router(router)
    app.include_router(build_health_router(app.state.probe))
    app.state.store = InMemoryIdentityStore(users)
    app.state.signing_key = signing_key
    app.state.issuer = make_issuer(signing_key)
    app.state.settings = make_settings(**settings_overrides)
    install_auth(app.state, make_token_verifier(signing_key))
    install_authz(app.state, make_opa_client(allow=opa_allow))
    return app, signing_key
