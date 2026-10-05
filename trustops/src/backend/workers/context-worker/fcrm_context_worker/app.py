"""The H4 consumer: :data:`~fcrm_context_worker.topology.CONTEXT_RUN_QUEUE`
consumed through :class:`~fcrm_context_worker.consumer.ContextConsumer`.

:func:`build_unit` returns H4 as a :class:`~fcrm_common.worker.WorkerUnit` for
the composite ``fcrm_harness`` process, which owns the broker, the Mongo client
and the health app. :func:`build_app` wraps the same unit in its own broker
and ``/healthz``/``/readyz`` for ``python -m fcrm_context_worker``.

The embedding provider is a seam, not a branch: ``EMBEDDING_PROVIDER=dummy``
(the default; deterministic, offline) or ``bedrock`` (the model and its
dimension from ``ai/models/tiers.yaml``) go through the same engine client, the
same builder and the same fixture, chosen once in
:func:`~fcrm_context_worker.config.context_config_from_env`.
"""

from __future__ import annotations

import logging
from typing import Optional

from evoq_context.core.config import ContextConfig
from evoq_context.llm.client import LLMClient
from faststream.asgi import AsgiFastStream
from faststream.rabbit import RabbitBroker
from fcrm_common.broker import FastStreamPublisher, wire_consumer
from fcrm_common.events import Consumer as EventConsumer
from fcrm_common.events import Publisher
from fcrm_common.health import (
    ReadinessCheck,
    ReadinessProbe,
    amqp_check,
    build_health_asgi_app,
    mongo_check,
)
from fcrm_common.http import ServiceClient
from fcrm_common.logging import configure_logging
from fcrm_common.mongo import Document, close_clients, get_client
from fcrm_common.objectstore import ObjectStore
from fcrm_common.settings import required_value
from fcrm_common.worker import WorkerUnit
from pymongo import AsyncMongoClient

from fcrm_context_worker.builder import IndexBuilder, StoreFactory
from fcrm_context_worker.config import context_config_from_env
from fcrm_context_worker.consumer import ContextConsumer
from fcrm_context_worker.settings import ContextSettings
from fcrm_context_worker.sources import POLICY_PEER, PolicyRecordSource
from fcrm_context_worker.topology import (
    CONTEXT_JOBS_EXCHANGE,
    ROUTING_KEY_INDEX_BUILD,
)

logger = logging.getLogger(__name__)

CONSUMED_MESSAGES_COLLECTION = "consumed_messages"


def build_broker(rabbitmq_url: str) -> RabbitBroker:
    """An unwired ``RabbitBroker``; call :func:`wire_subscriber` next."""
    return RabbitBroker(rabbitmq_url)


def wire_subscriber(
    broker: RabbitBroker, consumer: EventConsumer, *, prefetch: Optional[int] = None
) -> None:
    """Register the one manually-acked subscriber on the context queue,
    delegating every delivery to ``consumer.process`` (dedupe, the job,
    retry/DLQ; see ``fcrm_common.events``). ``prefetch`` is how many builds run
    at once.
    """
    wire_consumer(
        broker,
        consumer,
        exchange=CONTEXT_JOBS_EXCHANGE,
        routing_keys=[ROUTING_KEY_INDEX_BUILD],
        prefetch=prefetch,
    )


def _vectors_check(store: ObjectStore, bucket: str) -> ReadinessCheck:
    """The vectors bucket answers a ``HEAD``: non-critical, because the unit
    still consumes and a store outage retries the job rather than losing it.
    """

    async def ping() -> None:
        await store.ping(bucket)

    return ReadinessCheck("vectors_bucket", ping, critical=False)


def _policy_client(settings: ContextSettings) -> ServiceClient:
    """The service-account client for S3 (2 s connect, 10 s read, two retries
    for a GET).
    """
    secret = settings.service_token_secret
    return ServiceClient(
        required_value(
            settings.policy_service_url, "POLICY_SERVICE_URL", settings.app_name
        ),
        dependency=POLICY_PEER,
        caller=settings.app_name,
        token_secret=secret.get_secret_value() if secret is not None else None,
    )


def build_unit(
    settings: ContextSettings,
    *,
    mongo: AsyncMongoClient[Document],
    publisher: Publisher,
    object_store: Optional[ObjectStore] = None,
    policy_client: Optional[ServiceClient] = None,
    config: Optional[ContextConfig] = None,
    llm: Optional[LLMClient] = None,
    make_store: Optional[StoreFactory] = None,
) -> WorkerUnit:
    """H4 as a unit a host can wire onto its own broker.

    Args:
        settings: H4's settings (already ``load()``-ed).
        mongo: The host's Mongo client; never closed here.
        publisher: The host's publisher, used for ``index.built`` /
            ``index.failed`` and for the retry and dead-letter copies.
        object_store: Writes the vector fixture; built from ``settings`` (the
            real S3 or MinIO client) when omitted.
        policy_client: The service-account client for S3; built from
            ``settings`` when omitted (and closed at shutdown either way).
        config: The engine config; read from the environment when omitted
            (``VECTOR_BACKEND``, ``EMBEDDING_PROVIDER``, ...).
        llm: The embedding client; built from ``config`` when omitted.
        make_store: A new vector store per build; the configured backend when
            omitted.

    Returns:
        The unit: the ``context-worker.run`` subscriber, the consumed-message
        index at startup, a shutdown hook closing the S3 client and a
        non-critical ``vectors_bucket`` readiness check.

    Raises:
        fcrm_common.settings.MissingSettingsError: the database name or S3's URL
            is unset.
        fcrm_context_worker.config.VectorBackendError: ``VECTOR_BACKEND`` names
            a backend H4 does not support.
        fcrm_context_worker.config.EmbeddingConfigError: the embedding model
            cannot be determined.
    """
    db_name = required_value(settings.mongodb_db, "MONGODB_DB", settings.app_name)
    engine_config = config if config is not None else context_config_from_env()
    embedder = llm if llm is not None else LLMClient.from_config(engine_config)
    store = (
        object_store
        if object_store is not None
        else ObjectStore.from_settings(settings)
    )
    policy = policy_client if policy_client is not None else _policy_client(settings)
    consumer = ContextConsumer(
        mongo[db_name][CONSUMED_MESSAGES_COLLECTION],
        publisher,
        builder=IndexBuilder(
            PolicyRecordSource(policy), embedder, engine_config, make_store
        ),
        store=store,
        bucket=settings.s3_bucket_vectors,
        timeout_s=settings.context_build_timeout_seconds,
    )

    async def _ensure_indexes() -> None:
        await consumer.ensure_indexes()

    async def _close_policy_client() -> None:
        await policy.aclose()

    return WorkerUnit(
        "H4",
        wire=lambda broker: wire_subscriber(
            broker, consumer, prefetch=settings.context_prefetch
        ),
        after_startup=(_ensure_indexes,),
        on_shutdown=(_close_policy_client,),
        checks=(_vectors_check(store, settings.s3_bucket_vectors),),
    )


def build_app(settings: Optional[ContextSettings] = None) -> AsgiFastStream:
    """The standalone H4 process (``python -m fcrm_context_worker``): its own
    broker, the unit, and a health ASGI app, with real dependencies.
    """
    configured = settings or ContextSettings.load()
    configure_logging(configured.app_name, configured.log_level, env=configured.env)

    mongodb_uri = required_value(
        configured.mongodb_uri, "MONGODB_URI", configured.app_name
    )
    rabbitmq_url = required_value(
        configured.rabbitmq_url, "RABBITMQ_URL", configured.app_name
    )
    mongo_client = get_client(mongodb_uri)
    broker = build_broker(rabbitmq_url)
    unit = build_unit(
        configured, mongo=mongo_client, publisher=FastStreamPublisher(broker)
    )
    unit.wire(broker)

    probe = ReadinessProbe(
        [mongo_check(mongo_client), amqp_check(rabbitmq_url), *unit.checks]
    )
    health_app = build_health_asgi_app(probe)
    return AsgiFastStream(
        broker,
        asgi_routes=[("/healthz", health_app), ("/readyz", health_app)],
        after_startup=list(unit.after_startup),
        on_shutdown=[*unit.on_shutdown, close_clients],
    )
