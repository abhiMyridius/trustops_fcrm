"""Startup and shutdown of the composite process.

A lifespan does not cascade into a ``Mount``ed sub-app (see
``fcrm_common.asgi``), so the sub-apps are built with ``standalone=False``
and this module starts each service itself by entering its
``lifespan.register`` in :data:`fcrm_api.registry.MOUNTS` order. The
process-wide pieces are created once here: logging, tracing, one Mongo
client per credential role, one token verifier, one OPA client and one
RabbitMQ broker with the publisher every service's outbox relay shares
(ADR-28). The broker starts before the services and stops after them, so a
relay can drain through it on shutdown.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from time import monotonic
from typing import Any

from aio_pika.exceptions import AMQPException
from fastapi import FastAPI
from faststream.rabbit import RabbitBroker
from fcrm_common.auth import TokenVerifier
from fcrm_common.authz import OPAClient
from fcrm_common.broker import FastStreamPublisher
from fcrm_common.errors import UpstreamUnavailableError
from fcrm_common.health import ReadinessCheck, ReadinessProbe, amqp_check
from fcrm_common.ids import new_id
from fcrm_common.logging import configure_logging
from fcrm_common.mongo import mongo_client
from fcrm_common.service import ServiceDeps
from fcrm_common.settings import BaseServiceSettings, required_value
from fcrm_common.tracing import init_otel, shutdown_otel

from fcrm_api import __version__
from fcrm_api.registry import MOUNTS, ServiceMount
from fcrm_api.settings import ApiSettings

logger = logging.getLogger(__name__)

#: A mounted service's own checks run concurrently with 2 s each; the
#: aggregate check that wraps them needs a little more than that.
SUBPROBE_TIMEOUT_S = 3.0

#: How long the process waits for RabbitMQ at boot (it may still be starting
#: when compose launches the api), how long one connection attempt may take,
#: and the pause between attempts.
BROKER_START_TIMEOUT_S = 60.0
BROKER_ATTEMPT_TIMEOUT_S = 10.0
BROKER_RETRY_S = 2.0


def build_broker(rabbitmq_url: str) -> RabbitBroker:
    """The process's one (unstarted) ``RabbitBroker``. A seam for tests."""
    return RabbitBroker(rabbitmq_url)


async def start_broker(broker: RabbitBroker) -> None:
    """Connect ``broker``, retrying while RabbitMQ is still coming up.

    Raises:
        UpstreamUnavailableError: RabbitMQ did not accept a connection within
            :data:`BROKER_START_TIMEOUT_S`.
    """
    deadline = monotonic() + BROKER_START_TIMEOUT_S
    while True:
        try:
            async with asyncio.timeout(BROKER_ATTEMPT_TIMEOUT_S):
                await broker.start()
            return
        except (OSError, TimeoutError, AMQPException) as exc:
            if monotonic() + BROKER_RETRY_S >= deadline:
                logger.error(
                    "rabbitmq unreachable at startup",
                    extra={
                        "dependency": "rabbitmq",
                        "error": {"type": type(exc).__name__},
                    },
                )
                raise UpstreamUnavailableError(
                    "RabbitMQ is unreachable.", dependency="rabbitmq"
                ) from exc
            logger.warning(
                "rabbitmq not ready; retrying",
                extra={"dependency": "rabbitmq", "error": {"type": type(exc).__name__}},
            )
            await asyncio.sleep(BROKER_RETRY_S)


def _service_settings(mount: ServiceMount, api: ApiSettings) -> BaseServiceSettings:
    """Load ``mount``'s own settings with its database, and for S4 its
    insert-only Mongo URI, overriding the shared environment.
    """
    overrides: dict[str, Any] = {"mongodb_db": mount.db_name}
    if mount.mongo_role == "audit_writer":
        overrides["mongodb_uri"] = api.mongodb_uri_audit_writer
    return mount.settings_cls.load(**overrides)


def _subprobe_check(component_id: str, probe: ReadinessProbe) -> ReadinessCheck:
    """An aggregate ``/readyz`` check that fails when a mounted service's
    own readiness is ``fail`` (a degraded service stays ready, as it does
    standalone).
    """

    async def run() -> None:
        status, _ = await probe.run()
        if status != 200:
            raise UpstreamUnavailableError(
                f"{component_id} is not ready", dependency=component_id
            )

    return ReadinessCheck(
        component_id, run, critical=True, timeout_s=SUBPROBE_TIMEOUT_S
    )


@asynccontextmanager
async def api_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Start every mounted service with shared clients; stop them in reverse.

    Raises:
        MissingSettingsError: A required environment variable is missing.
        UpstreamUnavailableError: A service's startup self-test failed
            (the process then refuses to start rather than half-serve).
    """
    settings = ApiSettings.load()
    configure_logging(
        settings.app_name, settings.log_level, version=__version__, env=settings.env
    )
    init_otel(settings.app_name, __version__, environment=settings.env, app=app)
    logger.info("starting", extra={"env": settings.env})

    app_uri = required_value(settings.mongodb_uri, "MONGODB_URI", settings.app_name)
    writer_uri = required_value(
        settings.mongodb_uri_audit_writer, "MONGODB_URI_AUDIT_WRITER", settings.app_name
    )
    opa_url = required_value(settings.opa_url, "OPA_URL", settings.app_name)
    rabbitmq_url = required_value(
        settings.rabbitmq_url, "RABBITMQ_URL", settings.app_name
    )

    async with AsyncExitStack() as stack:
        # Pushed first, so it runs last: batched spans and log records are
        # flushed after every client and service has finished logging.
        stack.callback(shutdown_otel)
        app_mongo = await stack.enter_async_context(mongo_client(app_uri))
        audit_mongo = await stack.enter_async_context(mongo_client(writer_uri))
        evals_mongo = (
            await stack.enter_async_context(
                mongo_client(settings.mongodb_uri_evals_reader)
            )
            if settings.mongodb_uri_evals_reader
            else None
        )
        verifier = TokenVerifier.from_settings(settings)
        stack.push_async_callback(verifier.aclose)
        opa = OPAClient(opa_url)
        stack.push_async_callback(opa.aclose)
        broker = build_broker(rabbitmq_url)
        await start_broker(broker)
        stack.push_async_callback(broker.stop)
        publisher = FastStreamPublisher(broker)
        lease_holder = new_id("relay")

        probe: ReadinessProbe = app.state.probe
        probe.add(amqp_check(rabbitmq_url))
        for mount in MOUNTS:
            sub: FastAPI = app.state.mounted[mount.component_id]
            deps = ServiceDeps(
                mongo=audit_mongo if mount.mongo_role == "audit_writer" else app_mongo,
                token_verifier=verifier,
                opa=opa,
                publisher=publisher,
                lease_holder=lease_holder,
                broker=broker,
                evals_mongo=evals_mongo if mount.mongo_role == "audit_writer" else None,
            )
            await stack.enter_async_context(
                mount.register(sub, _service_settings(mount, settings), deps)
            )
            probe.add(_subprobe_check(mount.component_id, sub.state.probe))
            logger.info(
                "service ready",
                extra={"component": mount.component_id, "prefix": mount.prefix},
            )
        yield
