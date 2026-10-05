"""Startup and shutdown for S5: connect Mongo, ensure indexes, seed the decisions
already on record, build the committee directory, start the outbox relay, wire
the store and the readiness probe.

With ``SEED_ON_EMPTY`` and an empty store, S5 loads the history the 80 seed cases
imply (:mod:`fcrm_decision.seedmodel`): an outcome for each of the 65 closed cases,
and a decided pack with three votes for each of those that went to the committee,
plus a released pack open for voting for each committee-stage case. One bulk
insert per collection, after the unique indexes exist. The demo case's pack and
votes are created live.

**The relay.** What S5 writes to its ``outbox`` (``vote.cast``, ``vote.tallied``,
``pack.released``, ``outcome.set``...) leaves for the bus through an
:class:`~fcrm_common.outbox.OutboxRelay` that runs as a background task for the
life of the service, under the lease ``outbox-relay:decision`` so only one
process publishes (ADR-28). It needs the host's publisher
(:attr:`fcrm_common.service.ServiceDeps.publisher`): the composite ``api``
process always has one (it refuses to start without ``RABBITMQ_URL``). With none
(a standalone ``uvicorn fcrm_decision.main:app``) S5 runs without publishing and
says so in the log; its rows wait in the outbox for a host that has a broker.
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

from fcrm_decision.directory import (
    CommitteeDirectory,
    HttpCommitteeDirectory,
    UnconfiguredCommitteeDirectory,
)
from fcrm_decision.seedmodel import SeedReference, build_seed_rows
from fcrm_decision.settings import DecisionSettings
from fcrm_decision.store_mongo import MongoDecisionStore, ensure_indexes

logger = logging.getLogger(__name__)

__version__ = "0.1.0"

OUTBOX_RELAY_LEASE_NAME = "outbox-relay:decision"


def _build_directory(
    settings: DecisionSettings,
) -> tuple[CommitteeDirectory, HttpCommitteeDirectory | None]:
    """The committee directory over X1, and the HTTP one to close at shutdown
    (``None`` when nothing says where X1 is: the directory then refuses on use).
    """
    url = settings.identity_directory_url
    if url is None:
        logger.warning(
            "no identity URL configured; the committee roster is unavailable",
            extra={"dependency": "identity"},
        )
        return UnconfiguredCommitteeDirectory(), None
    secret = settings.service_token_secret
    client = ServiceClient(
        url,
        dependency="identity",
        caller=settings.app_name,
        token_secret=secret.get_secret_value() if secret is not None else None,
    )
    http = HttpCommitteeDirectory(client)
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
            "decision service has no publisher; its outbox is not relayed",
            extra={"outbox": outbox.collection.full_name},
        )
        return
    lease = MongoLease(
        leases, OUTBOX_RELAY_LEASE_NAME, deps.lease_holder or new_id("relay")
    )
    relay = OutboxRelay(outbox.collection, lease, deps.publisher)
    runner = RelayRunner(relay, name="decision")
    runner.start()
    stack.push_async_callback(runner.stop)
    probe.add(ReadinessCheck("outbox", runner.check))


@asynccontextmanager
async def register(
    app: FastAPI, settings: DecisionSettings, deps: ServiceDeps
) -> AsyncIterator[None]:
    """Wire S5 onto ``app`` from injected dependencies: ensure every index S5's
    invariants depend on, seed the decisions on record when the store is empty,
    then set auth/authz, the store and the committee directory on ``app.state``,
    start the outbox relay and add the readiness checks.

    The host owns logging, tracing and the Mongo, verifier, OPA and broker
    clients (``fcrm_common.service``); this never opens or closes them.
    """
    # Bearer verification (fcrm_common.auth.current_user) and the OPA route
    # policy (fcrm_common.authz.opa_dependency, router-level in routes.py).
    # Without these every route answers 503, never 200: fail closed. S5 is
    # where this matters most: a vote's member is the verified principal.
    install_auth(app.state, deps.token_verifier)
    install_authz(app.state, deps.opa)

    db = deps.mongo[settings.mongodb_db or "fcrm_decision"]
    outbox = Outbox(db["outbox"])
    await ensure_indexes(db, outbox)

    store = MongoDecisionStore(db, outbox)
    if settings.seed_on_empty:
        reader = SeedReader(settings.seed_path)
        committee = [m["id"] for m in reader.section("users")["committee"]]
        rows = build_seed_rows(
            reader.cases(), committee, SeedReference.from_seed(reader.raw)
        )
        await store.seed_if_empty(rows.packs, rows.votes, rows.outcomes)
    directory, closable = _build_directory(settings)
    app.state.store = store
    app.state.directory = directory

    # main.py built ReadinessProbe() before this ran, so the health router
    # already holds a reference to it; add checks to that same instance
    # rather than replacing it.
    probe: ReadinessProbe = app.state.probe
    probe.add(mongo_check(deps.mongo))

    async with AsyncExitStack() as stack:
        if closable is not None:
            stack.push_async_callback(closable.aclose)
        start_relay(stack, probe, outbox, db["leases"], deps)
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
