"""Startup and shutdown for S5: connect Mongo, ensure indexes, seed the decisions
already on record, wire the store and the readiness probe.

With ``SEED_ON_EMPTY`` and an empty store, S5 loads the history the 80 seed cases
imply (:mod:`fcrm_decision.seedmodel`): an outcome for each of the 65 closed cases,
and a decided pack with three votes for each of those that went to the committee,
plus a released pack open for voting for each committee-stage case. One bulk
insert per collection, after the unique indexes exist. The demo case's pack and
votes are created live.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fcrm_common.auth import install_auth
from fcrm_common.authz import install_authz
from fcrm_common.health import ReadinessProbe, mongo_check
from fcrm_common.logging import configure_logging
from fcrm_common.seedload import SeedReader
from fcrm_common.service import ServiceDeps, standalone_deps
from fcrm_common.tracing import init_otel

from fcrm_decision.seedmodel import build_seed_rows
from fcrm_decision.settings import DecisionSettings
from fcrm_decision.store import MongoDecisionStore, ensure_indexes

logger = logging.getLogger(__name__)

__version__ = "0.1.0"


@asynccontextmanager
async def register(
    app: FastAPI, settings: DecisionSettings, deps: ServiceDeps
) -> AsyncIterator[None]:
    """Wire S5 onto ``app`` from injected dependencies: ensure every unique
    index S5's invariants depend on, seed the decisions on record when the store
    is empty, then set auth/authz, the store and the Mongo readiness check on
    ``app.state``.

    The host owns logging, tracing and the Mongo, verifier and OPA
    clients (``fcrm_common.service``); this never opens or closes them.
    """
    # Bearer verification (fcrm_common.auth.current_user) and the OPA route
    # policy (fcrm_common.authz.opa_dependency, router-level in routes.py).
    # Without these every route answers 503, never 200: fail closed. S5 is
    # where this matters most: a vote's member_id is bound to the verified
    # principal in routes.py, which needs a real principal to bind to.
    install_auth(app.state, deps.token_verifier)
    install_authz(app.state, deps.opa)

    db = deps.mongo[settings.mongodb_db or "fcrm_decision"]
    await ensure_indexes(db)

    store = MongoDecisionStore(db)
    if settings.seed_on_empty:
        reader = SeedReader(settings.seed_path)
        committee = [m["id"] for m in reader.section("users")["committee"]]
        rows = build_seed_rows(reader.cases(), committee)
        await store.seed_if_empty(rows.packs, rows.votes, rows.outcomes)
    app.state.store = store

    # main.py built ReadinessProbe() before this ran, so the health router
    # already holds a reference to it; add checks to that same instance
    # rather than replacing it.
    probe: ReadinessProbe = app.state.probe
    probe.add(mongo_check(deps.mongo))

    yield


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Standalone entry (``uvicorn fcrm_decision.main:app``): load settings,
    configure logging and tracing, build the dependencies, then register.
    """
    settings = DecisionSettings.load()
    configure_logging(
        settings.app_name, settings.log_level, version=__version__, env=settings.env
    )
    init_otel(settings.app_name, __version__, environment=settings.env, app=app)
    logger.info("starting", extra={"env": settings.env})
    async with standalone_deps(settings) as deps, register(app, settings, deps):
        yield
