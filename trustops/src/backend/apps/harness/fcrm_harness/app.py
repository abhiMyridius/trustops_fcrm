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
from itertools import chain
from typing import Optional

from faststream.asgi import AsgiFastStream
from faststream.rabbit import RabbitBroker
from fcrm_agent_worker.app import build_unit as build_agent_worker_unit
from fcrm_agent_worker.live_runtime import ModelFactory, live_model_for_slot
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
    model_factory: ModelFactory = live_model_for_slot,
) -> list[WorkerUnit]:
    """The consumer units this process runs, in start order.

    Args:
        settings: The harness settings the units' own settings derive from.
        mongo: The process's Mongo client.
        publisher: The process's publisher over its one broker.
        registry: Tool registry for H2; ``None`` selects its default.
        model_factory: Model factory for H2 (a fake in tests).
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
    ]


def build_app(
    settings: Optional[HarnessSettings] = None,
    *,
    registry: Optional[ToolRegistry] = None,
    model_factory: ModelFactory = live_model_for_slot,
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
        after_startup=[hook for unit in units for hook in unit.after_startup],
        on_shutdown=[
            *(hook for unit in reversed(units) for hook in unit.on_shutdown),
            close_clients,
            _shutdown_telemetry,
        ],
    )
