"""Startup and shutdown: Mongo, the case/view/outbox indexes, the seed, the
outbox relay, auth and the readiness probe.

Unlike S2 there is no golden-number self-test to run (S1 has no scoring
model to reproduce); the one startup check worth making is that the
seed-backed field set and pinned-versions providers
(``fcrm_case.fieldset_provider``) parse cleanly, so a corrupted seed file
fails the container at boot rather than on a caller's first ``POST
/cases``.

**The relay.** What S1 writes to its ``outbox`` (``case.created``,
``case.submitted``, ``case.document_stored``...) leaves for the bus through an
:class:`~fcrm_common.outbox.OutboxRelay` that runs as a background task for the
life of the service, under the lease ``outbox-relay:case`` so only one process
publishes (ADR-28). It needs the host's publisher
(:attr:`fcrm_common.service.ServiceDeps.publisher`): with none (a standalone
``uvicorn fcrm_case.main:app``) S1 runs without publishing and says so in the
log, and its rows wait in the outbox for a host that has a broker.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI
from fcrm_common.auth import install_auth
from fcrm_common.authz import install_authz
from fcrm_common.health import ReadinessCheck, ReadinessProbe, mongo_check
from fcrm_common.http import ServiceClient
from fcrm_common.ids import new_id
from fcrm_common.logging import configure_logging
from fcrm_common.mongo import Document, MongoLease
from fcrm_common.outbox import Outbox, OutboxRelay, RelayRunner
from fcrm_common.seedload import SeedReader
from fcrm_common.service import ServiceDeps, standalone_deps
from fcrm_common.tracing import init_otel
from pymongo.asynchronous.collection import AsyncCollection

from fcrm_case.activity_log import MongoActivityLog
from fcrm_case.directory import (
    AnalystLoadService,
    HttpIdentityDirectory,
    IdentityDirectory,
    UnconfiguredIdentityDirectory,
)
from fcrm_case.fieldset_provider import active_field_set, active_pinned_versions
from fcrm_case.outcome_store import MongoOutcomeStore
from fcrm_case.seeding import seed_case_service
from fcrm_case.settings import CaseSettings
from fcrm_case.sse import EventHub
from fcrm_case.store import MongoCaseStore, build_cases_repository, ensure_case_indexes
from fcrm_case.views_store import MongoViewStore

logger = logging.getLogger(__name__)

__version__ = "0.1.0"

OUTBOX_RELAY_LEASE_NAME = "outbox-relay:case"


def _build_directory(
    settings: CaseSettings,
) -> tuple[IdentityDirectory, HttpIdentityDirectory | None]:
    """The analyst directory over X1, and the HTTP one to close at shutdown
    (``None`` when nothing says where X1 is: the directory then refuses on use).
    """
    url = settings.identity_directory_url
    if url is None:
        logger.warning(
            "no identity URL configured; the analyst roster is unavailable",
            extra={"dependency": "identity"},
        )
        return UnconfiguredIdentityDirectory(), None
    secret = settings.service_token_secret
    client = ServiceClient(
        url,
        dependency="identity",
        caller=settings.app_name,
        token_secret=secret.get_secret_value() if secret is not None else None,
    )
    http = HttpIdentityDirectory(client)
    return http, http


def start_relay(
    stack: AsyncExitStack,
    probe: ReadinessProbe,
    outbox: Outbox,
    leases: AsyncCollection[Document],
    deps: ServiceDeps,
) -> None:
    """Start the outbox relay under ``stack`` (stopped on exit) and register
    its readiness check, or log that there is no publisher.
    """
    if deps.publisher is None:
        logger.warning(
            "case service has no publisher; its outbox is not relayed",
            extra={"outbox": outbox.collection.full_name},
        )
        return
    lease = MongoLease(
        leases, OUTBOX_RELAY_LEASE_NAME, deps.lease_holder or new_id("relay")
    )
    relay = OutboxRelay(outbox.collection, lease, deps.publisher)
    runner = RelayRunner(relay, name="case")
    runner.start()
    stack.push_async_callback(runner.stop)
    probe.add(ReadinessCheck("outbox", runner.check))


@asynccontextmanager
async def register(
    app: FastAPI, settings: CaseSettings, deps: ServiceDeps
) -> AsyncIterator[None]:
    """Wire S1 onto ``app`` from injected dependencies: parse the seed-backed
    providers, ensure the indexes, seed an empty database (``SEED_ON_EMPTY``),
    set the stores, hub, directory and auth/authz on ``app.state``, start the
    outbox relay and add the readiness checks.

    The host owns logging, tracing and the Mongo, verifier, OPA and broker
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
    outcomes = MongoOutcomeStore(
        deps.mongo, db, cases_repo, views.repos.conditions, outbox, activity
    )
    await ensure_case_indexes(cases_repo)
    await outbox.ensure_indexes()
    await views.ensure_indexes()
    await activity.ensure_indexes()
    await outcomes.ensure_indexes()
    if settings.seed_on_empty:
        await seed_case_service(
            SeedReader(settings.seed_path), cases_repo, views.repos, counters
        )

    store = MongoCaseStore(deps.mongo, cases_repo, counters, outbox, activity)
    directory, closable = _build_directory(settings)
    app.state.store = store
    app.state.views = views
    app.state.outcomes = outcomes
    app.state.hub = EventHub(settings.sse_queue_size)
    app.state.directory = directory
    app.state.loads = AnalystLoadService(store, directory)
    app.state.settings = settings

    probe: ReadinessProbe = app.state.probe
    probe.add(mongo_check(deps.mongo))

    async with AsyncExitStack() as stack:
        if closable is not None:
            stack.push_async_callback(closable.aclose)
        start_relay(stack, probe, outbox, db["leases"], deps)
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
