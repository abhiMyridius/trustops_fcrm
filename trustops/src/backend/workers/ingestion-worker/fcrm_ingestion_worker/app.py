"""The H3 consumer: :data:`~fcrm_ingestion_worker.topology.INGESTION_RUN_QUEUE`
consumed through :class:`~fcrm_ingestion_worker.consumer.IngestionConsumer`.

:func:`build_unit` returns H3 as a :class:`~fcrm_common.worker.WorkerUnit` for
the composite ``fcrm_harness`` process, which owns the broker, the Mongo client
and the health app. :func:`build_app` wraps the same unit in its own broker
and ``/healthz``/``/readyz`` for ``python -m fcrm_ingestion_worker``.
"""

from __future__ import annotations

import logging
from typing import Optional

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
from fcrm_common.logging import configure_logging
from fcrm_common.mongo import Document, close_clients, get_client
from fcrm_common.objectstore import ObjectStore
from fcrm_common.settings import MissingSettingsError, required_value
from fcrm_common.worker import WorkerUnit
from pymongo import AsyncMongoClient

from fcrm_ingestion_worker.consumer import IngestionConsumer
from fcrm_ingestion_worker.guard1 import load_terms
from fcrm_ingestion_worker.settings import IngestionSettings
from fcrm_ingestion_worker.topology import (
    INGESTION_JOBS_EXCHANGE,
    ROUTING_KEY_INGEST_RUN,
)

logger = logging.getLogger(__name__)

CONSUMED_MESSAGES_COLLECTION = "consumed_messages"


def build_broker(rabbitmq_url: str) -> RabbitBroker:
    """An unwired ``RabbitBroker``; call :func:`wire_subscriber` next."""
    return RabbitBroker(rabbitmq_url)


def wire_subscriber(
    broker: RabbitBroker, consumer: EventConsumer, *, prefetch: Optional[int] = None
) -> None:
    """Register the one manually-acked subscriber on the ingestion queue,
    delegating every delivery to ``consumer.process`` (dedupe, the job,
    retry/DLQ; see ``fcrm_common.events``). ``prefetch`` is how many documents
    are extracted at once.
    """
    wire_consumer(
        broker,
        consumer,
        exchange=INGESTION_JOBS_EXCHANGE,
        routing_keys=[ROUTING_KEY_INGEST_RUN],
        prefetch=prefetch,
    )


def _allowlist(settings: IngestionSettings) -> tuple[str, ...]:
    """The extra Guard 1 keep-terms from ``GUARD1_ALLOWLIST_FILE``, if set.

    Raises:
        fcrm_common.settings.MissingSettingsError: the file cannot be read.
    """
    if not settings.guard1_allowlist_file:
        return ()
    try:
        return load_terms(settings.guard1_allowlist_file)
    except OSError as exc:
        raise MissingSettingsError(
            settings.app_name,
            [],
            [
                "GUARD1_ALLOWLIST_FILE: cannot read "
                f"{settings.guard1_allowlist_file!r} ({exc.strerror})"
            ],
        ) from exc


def _uploads_check(store: ObjectStore, bucket: str) -> ReadinessCheck:
    """The uploads bucket answers a ``HEAD``: non-critical, because the unit
    still consumes and a store outage retries the job rather than losing it.
    """

    async def ping() -> None:
        await store.ping(bucket)

    return ReadinessCheck("uploads_bucket", ping, critical=False)


def build_unit(
    settings: IngestionSettings,
    *,
    mongo: AsyncMongoClient[Document],
    publisher: Publisher,
    object_store: Optional[ObjectStore] = None,
) -> WorkerUnit:
    """H3 as a unit a host can wire onto its own broker.

    Args:
        settings: H3's settings (already ``load()``-ed).
        mongo: The host's Mongo client; never closed here.
        publisher: The host's publisher, used for ``ingest.done`` /
            ``ingest.failed`` and for the retry and dead-letter copies.
        object_store: Reads uploads and writes canonical Markdown; built from
            ``settings`` (the real S3 or MinIO client) when omitted.

    Returns:
        The unit: the ``ingestion-worker.run`` subscriber, the consumed-message
        index at startup and a non-critical ``uploads_bucket`` readiness check.

    Raises:
        fcrm_common.settings.MissingSettingsError: the database name is unset
            or ``GUARD1_ALLOWLIST_FILE`` cannot be read.
    """
    db_name = required_value(settings.mongodb_db, "MONGODB_DB", settings.app_name)
    store = (
        object_store
        if object_store is not None
        else ObjectStore.from_settings(settings)
    )
    consumer = IngestionConsumer(
        mongo[db_name][CONSUMED_MESSAGES_COLLECTION],
        publisher,
        store=store,
        timeout_s=settings.uploads_per_file_timeout_seconds,
        pdf_engine=settings.pdf_engine,
        pdf_min_chars_per_page=settings.pdf_min_chars_per_page,
        extra_keep_terms=_allowlist(settings),
    )

    async def _ensure_indexes() -> None:
        await consumer.ensure_indexes()

    return WorkerUnit(
        "H3",
        wire=lambda broker: wire_subscriber(
            broker, consumer, prefetch=settings.prefetch
        ),
        after_startup=(_ensure_indexes,),
        checks=(_uploads_check(store, settings.s3_bucket_uploads),),
    )


def build_app(settings: Optional[IngestionSettings] = None) -> AsgiFastStream:
    """The standalone H3 process (``python -m fcrm_ingestion_worker``): its own
    broker, the unit, and a health ASGI app, with real dependencies.
    """
    configured = settings or IngestionSettings.load()
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
