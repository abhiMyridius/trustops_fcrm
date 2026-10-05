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
from fcrm_common.auth import TokenVerifier, install_auth
from fcrm_common.health import ReadinessProbe, mongo_check
from fcrm_common.logging import configure_logging
from fcrm_common.mongo import mongo_client
from fcrm_common.outbox import Outbox
from fcrm_common.tracing import init_otel

from fcrm_case.fieldset_provider import active_field_set, active_pinned_versions
from fcrm_case.settings import CaseSettings
from fcrm_case.store import MongoCaseStore, build_cases_repository, ensure_case_indexes

logger = logging.getLogger(__name__)

__version__ = "0.1.0"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Connect Mongo, wire the store and auth, then wire the readiness
    probe onto ``app.state`` for the routes to use.
    """
    settings = CaseSettings.load()
    configure_logging(
        settings.app_name, settings.log_level, version=__version__, env=settings.env
    )
    init_otel(settings.app_name, __version__, environment=settings.env, app=app)
    logger.info("starting", extra={"env": settings.env})

    # Fail fast on a corrupted seed file rather than on the first request.
    active_field_set()
    active_pinned_versions()

    verifier = TokenVerifier.from_settings(settings)
    install_auth(app.state, verifier)

    assert settings.mongodb_uri is not None  # fail_fast already enforced this
    async with mongo_client(settings.mongodb_uri) as client:
        db = client[settings.mongodb_db or "fcrm_case"]
        cases_repo = build_cases_repository(db)
        counters = db["case_counters"]
        outbox = Outbox(db["outbox"])
        await ensure_case_indexes(cases_repo)
        await outbox.ensure_indexes()

        app.state.store = MongoCaseStore(client, cases_repo, counters, outbox)
        app.state.settings = settings

        probe: ReadinessProbe = app.state.probe
        probe.add(mongo_check(client))

        yield
