"""Build the harness: one broker, one Mongo client, one health endpoint, and
every consumer unit wired onto them.

Each unit (:class:`~fcrm_common.worker.WorkerUnit`) registers its own
subscribers and startup, shutdown and readiness hooks. The harness owns what
they share: the ``RabbitBroker`` (so every subscriber and the outbox relay
publish through one connection), the process's Mongo client, logging,
tracing and the ``/healthz``/``/readyz`` ASGI app that aggregates every
unit's checks.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from itertools import chain
from typing import Optional

from faststream.asgi import AsgiFastStream
from faststream.rabbit import RabbitBroker
from fcrm_agent_worker.app import build_unit as build_agent_worker_unit
from fcrm_agent_worker.live_runtime import ModelFactory
from fcrm_caseflow.app import build_unit as build_caseflow_unit
from fcrm_common.broker import FastStreamPublisher
from fcrm_common.events import Publisher
from fcrm_common.health import (
    ReadinessProbe,
    amqp_check,
    build_health_asgi_app,
    mongo_check,
)
from fcrm_common.logging import configure_logging
from fcrm_common.mongo import Document, close_clients, get_client
from fcrm_common.settings import required_value
from fcrm_common.tracing import init_otel, shutdown_otel
from fcrm_common.worker import WorkerUnit
from fcrm_context_worker.app import build_unit as build_context_unit
from fcrm_ingestion_worker.app import build_unit as build_ingestion_unit
from fcrm_tools import ToolRegistry
from pymongo import AsyncMongoClient

from fcrm_harness import __version__
from fcrm_harness.settings import HarnessSettings

logger = logging.getLogger(__name__)


def build_units(
    settings: HarnessSettings,
    *,
    mongo: AsyncMongoClient[Document],
    publisher: Publisher,
    registry: Optional[ToolRegistry] = None,
    model_factory: Optional[ModelFactory] = None,
) -> list[WorkerUnit]:
    """The consumer units this process runs, in start order.

    Args:
        settings: The harness settings the units' own settings derive from.
        mongo: The process's Mongo client.
        publisher: The process's publisher over its one broker.
        registry: Tool registry for H2; ``None`` selects its default.
        model_factory: Model factory for H2 (a fake in tests); ``None`` builds the
            live one with the second Bedrock region, if one is set.
    """
    return [
        build_caseflow_unit(
            settings.caseflow_settings(), mongo=mongo, publisher=publisher
        ),
        build_agent_worker_unit(
            settings.agent_worker_settings(),
            mongo=mongo,
            publisher=publisher,
            registry=registry,
            model_factory=model_factory,
        ),
        build_ingestion_unit(
            settings.ingestion_settings(), mongo=mongo, publisher=publisher
        ),
        build_context_unit(
            settings.context_settings(), mongo=mongo, publisher=publisher
        ),
    ]


def _unit_startup(unit: WorkerUnit) -> Callable[[], Awaitable[None]]:
    """``unit``'s startup hooks as one hook that never raises.

    The harness runs every unit's hooks in one list, and the first that raised
    skipped all the rest: on 19 October an index conflict in H2's first hook
    left H1's and H2's outbox relays unstarted, so no event was published and
    every case stopped. Each hook now runs whatever the one before it did; a
    failure is logged, and the unit's readiness check (``/readyz``) reports it.
    """

    async def run() -> None:
        for hook in unit.after_startup:
            try:
                await hook()
            except Exception:  # noqa: BLE001 - logged; readiness reports it
                logger.exception(
                    "startup hook failed; the unit's other hooks still run",
                    extra={"component": unit.component_id},
                )

    return run


def build_app(
    settings: Optional[HarnessSettings] = None,
    *,
    registry: Optional[ToolRegistry] = None,
    model_factory: Optional[ModelFactory] = None,
) -> AsgiFastStream:
    """The full harness process with real dependencies.

    Raises:
        MissingSettingsError: A required environment variable is missing.
        ModelConfigError: ``ai/models/tiers.yaml`` is missing or invalid.
    """
    configured = settings or HarnessSettings.load()
    configure_logging(
        configured.app_name,
        configured.log_level,
        version=__version__,
        env=configured.env,
    )
    init_otel(configured.app_name, __version__, environment=configured.env)

    mongodb_uri = required_value(
        configured.mongodb_uri, "MONGODB_URI", configured.app_name
    )
    rabbitmq_url = required_value(
        configured.rabbitmq_url, "RABBITMQ_URL", configured.app_name
    )
    mongo_client = get_client(mongodb_uri)
    broker = RabbitBroker(rabbitmq_url)
    units = build_units(
        configured,
        mongo=mongo_client,
        publisher=FastStreamPublisher(broker),
        registry=registry,
        model_factory=model_factory,
    )
    for unit in units:
        unit.wire(broker)
        logger.info("unit wired", extra={"component": unit.component_id})

    probe = ReadinessProbe(
        [
            mongo_check(mongo_client),
            amqp_check(rabbitmq_url),
            *chain.from_iterable(unit.checks for unit in units),
        ]
    )
    health_app = build_health_asgi_app(probe)

    async def _shutdown_telemetry() -> None:
        shutdown_otel()

    return AsgiFastStream(
        broker,
        asgi_routes=[("/healthz", health_app), ("/readyz", health_app)],
        after_startup=[_unit_startup(unit) for unit in units],
        on_shutdown=[
            *(hook for unit in reversed(units) for hook in unit.on_shutdown),
            close_clients,
            _shutdown_telemetry,
        ],
    )
