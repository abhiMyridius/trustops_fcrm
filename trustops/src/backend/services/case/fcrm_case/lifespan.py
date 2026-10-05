"""Startup and shutdown: Mongo, the case/outbox indexes, auth and the
readiness probe.

Unlike S2 there is no golden-number self-test to run (S1 has no scoring
model to reproduce); the one startup check worth making is that the
seed-backed field set and pinned-versions providers
(``fcrm_case.fieldset_provider``) parse cleanly, so a corrupted seed file
fails the container at boot rather than on a caller's first ``POST
/cases``.
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
from fcrm_common.outbox import Outbox
from fcrm_common.service import ServiceDeps, standalone_deps
from fcrm_common.tracing import init_otel

from fcrm_case.activity_log import MongoActivityLog
from fcrm_case.fieldset_provider import active_field_set, active_pinned_versions
from fcrm_case.settings import CaseSettings
from fcrm_case.store import MongoCaseStore, build_cases_repository, ensure_case_indexes
from fcrm_case.views_store import MongoViewStore

logger = logging.getLogger(__name__)

__version__ = "0.1.0"


@asynccontextmanager
async def register(
    app: FastAPI, settings: CaseSettings, deps: ServiceDeps
) -> AsyncIterator[None]:
    """Wire S1 onto ``app`` from injected dependencies: parse the seed-backed
    providers, ensure the case and outbox indexes, then set the store,
    auth/authz and the readiness check on ``app.state``.

    The host owns logging, tracing and the Mongo, verifier and OPA
    clients (``fcrm_common.service``); this never opens or closes them.
    """
    # Fail fast on a corrupted seed file rather than on the first request.
    active_field_set()
    active_pinned_versions()

    install_auth(app.state, deps.token_verifier)
    install_authz(app.state, deps.opa)

    db = deps.mongo[settings.mongodb_db or "fcrm_case"]
    cases_repo = build_cases_repository(db)
    counters = db["case_counters"]
    outbox = Outbox(db["outbox"])
    activity = MongoActivityLog(deps.mongo, db["cases"], db["activity"])
    views = MongoViewStore(deps.mongo, db, outbox)
    await ensure_case_indexes(cases_repo)
    await outbox.ensure_indexes()
    await views.ensure_indexes()
    await activity.ensure_indexes()

    app.state.store = MongoCaseStore(deps.mongo, cases_repo, counters, outbox, activity)
    app.state.views = views
    app.state.settings = settings

    probe: ReadinessProbe = app.state.probe
    probe.add(mongo_check(deps.mongo))

    yield


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Standalone entry (``uvicorn fcrm_case.main:app``): load settings,
    configure logging and tracing, build the dependencies, then register.
    """
    settings = CaseSettings.load()
    configure_logging(
        settings.app_name, settings.log_level, version=__version__, env=settings.env
    )
    init_otel(settings.app_name, __version__, environment=settings.env, app=app)
    logger.info("starting", extra={"env": settings.env})
    async with standalone_deps(settings) as deps, register(app, settings, deps):
        yield
