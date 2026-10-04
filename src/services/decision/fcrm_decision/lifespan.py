"""Startup and shutdown for S5: connect Mongo, ensure indexes, wire the
store and the readiness probe.

Unlike S2, there is nothing in the P7 seed for S5 to recompute at startup
-- packs and votes are created live, per case, never shipped as a
pre-baked catalogue -- so this lifespan is just connect-and-index.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fcrm_common.health import ReadinessProbe, mongo_check
from fcrm_common.logging import configure_logging
from fcrm_common.mongo import mongo_client
from fcrm_common.tracing import init_otel

from fcrm_decision.settings import DecisionSettings
from fcrm_decision.store import MongoDecisionStore, ensure_indexes

logger = logging.getLogger(__name__)

__version__ = "0.1.0"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Connect Mongo, ensure every unique index S5's invariants depend on,
    then wire the store and the Mongo readiness check onto ``app.state``
    for the routes to use.
    """
    settings = DecisionSettings.load()
    configure_logging(
        settings.app_name, settings.log_level, version=__version__, env=settings.env
    )
    init_otel(settings.app_name, __version__, environment=settings.env, app=app)
    logger.info("starting", extra={"env": settings.env})

    assert settings.mongodb_uri is not None  # fail_fast already enforced this
    async with mongo_client(settings.mongodb_uri) as client:
        db = client[settings.mongodb_db or "fcrm_decision"]
        await ensure_indexes(db)

        app.state.store = MongoDecisionStore(db)

        # main.py built ReadinessProbe() before this lifespan ran, so the
        # health router already holds a reference to it; add checks to
        # that same instance rather than replacing it.
        probe: ReadinessProbe = app.state.probe
        probe.add(mongo_check(client))

        yield
