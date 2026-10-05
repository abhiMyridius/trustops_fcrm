"""Startup and shutdown: load the signing key, connect Mongo, seed the
demo users, wire auth/authz onto ``app.state`` and the readiness probe.

Fix carried from the cloned ``evoqidentityservice`` (CLAUDE.md's
AEO-reuse table, bug 3): :func:`SigningKey.from_file` and
:meth:`IdentitySettings.load` both raise rather than fall back to a
default when a required secret is missing or unusable, and neither
exception is caught here, so a container with no ``JWT_KEY_FILE`` or no
``DEMO_USERS_PASSWORD_HASH`` never finishes starting up — it exits loudly
instead of serving requests with a baked-in key no attacker needs to
guess.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fcrm_common.auth import install_auth
from fcrm_common.authz import install_authz
from fcrm_common.health import ReadinessProbe, mongo_check
from fcrm_common.logging import configure_logging
from fcrm_common.mongo import Repository
from fcrm_common.seedload import SeedReader, load_if_empty
from fcrm_common.service import ServiceDeps, standalone_deps
from fcrm_common.time import utcnow
from fcrm_common.tracing import init_otel
from pymongo import IndexModel
from pymongo.asynchronous.database import AsyncDatabase

from fcrm_identity.jwtkeys import SigningKey
from fcrm_identity.models import RefreshTokenRecord, UserRecord
from fcrm_identity.seeding import seed_user_records
from fcrm_identity.settings import IdentitySettings
from fcrm_identity.store import MongoIdentityStore
from fcrm_identity.tokens import TokenIssuer

logger = logging.getLogger(__name__)

__version__ = "0.1.0"


def _users_repo(db: AsyncDatabase[Any]) -> Repository[UserRecord]:
    """``Repository[UserRecord]`` over ``db.users``, keyed by seed id."""
    return Repository(db["users"], UserRecord, id_of=lambda u: u.id)


def _refresh_repo(db: AsyncDatabase[Any]) -> Repository[RefreshTokenRecord]:
    """``Repository[RefreshTokenRecord]`` over ``db.refresh_tokens``,
    keyed by ``jti``.
    """
    return Repository(db["refresh_tokens"], RefreshTokenRecord, id_of=lambda r: r.jti)


@asynccontextmanager
async def register(
    app: FastAPI, settings: IdentitySettings, deps: ServiceDeps
) -> AsyncIterator[None]:
    """Wire X1 onto ``app`` from injected dependencies: load the signing
    key, seed demo users on an empty collection, and set ``app.state``
    for the routes.

    The host owns logging, tracing and the Mongo, verifier and OPA
    clients (``fcrm_common.service``); this never opens or closes them.
    """
    assert settings.jwt_key_file is not None  # fail_fast already enforced this
    signing_key = SigningKey.from_file(settings.jwt_key_file)
    issuer = TokenIssuer(
        signing_key,
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        access_ttl_s=settings.jwt_access_ttl_s,
        refresh_ttl_s=settings.jwt_refresh_ttl_s,
    )

    db = deps.mongo[settings.mongodb_db or "fcrm_identity"]
    users_repo = _users_repo(db)
    refresh_repo = _refresh_repo(db)
    await refresh_repo.ensure_indexes(
        [IndexModel([("exp", 1)], expireAfterSeconds=0, name="exp_ttl")]
    )

    if settings.seed_on_empty:
        assert settings.demo_users_password_hash is not None
        reader = SeedReader(settings.seed_path)
        records = seed_user_records(
            reader.section("users"),
            settings.demo_users_password_hash.get_secret_value(),
            created_at=utcnow(),
        )
        await load_if_empty(users_repo, [users_repo.to_document(r) for r in records])

    store = MongoIdentityStore(users_repo, refresh_repo)
    app.state.store = store
    app.state.signing_key = signing_key
    app.state.issuer = issuer
    app.state.settings = settings

    install_auth(app.state, deps.token_verifier)
    install_authz(app.state, deps.opa)

    # main.py built ReadinessProbe() before this ran, so the health router
    # already holds a reference to it; add checks to that same instance
    # rather than replacing it (fcrm_common.health's documented usage).
    probe: ReadinessProbe = app.state.probe
    probe.add(mongo_check(deps.mongo))

    yield


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Standalone entry (``uvicorn fcrm_identity.main:app``): load settings,
    configure logging and tracing, build the dependencies, then register.
    """
    settings = IdentitySettings.load()
    configure_logging(
        settings.app_name, settings.log_level, version=__version__, env=settings.env
    )
    init_otel(settings.app_name, __version__, environment=settings.env, app=app)
    logger.info("starting", extra={"env": settings.env})
    async with standalone_deps(settings) as deps, register(app, settings, deps):
        yield
