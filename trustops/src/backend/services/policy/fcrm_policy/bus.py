"""S3's two connections to the bus, both optional and both started by the
lifespan under an ``AsyncExitStack`` so they stop in the right order.

* **The outbox relay.** Everything S3 announces (``index.build``,
  ``jurisdiction.list_published``) is written to its ``outbox`` in the same
  transaction as the change and leaves for RabbitMQ through an
  :class:`~fcrm_common.outbox.OutboxRelay` under the lease
  ``outbox-relay:policy``, so only one process publishes (ADR-28). It needs the
  host's publisher; without one (a standalone ``uvicorn fcrm_policy.main:app``)
  S3 runs without publishing, says so in the log, and its rows wait in the
  outbox for a host that has a broker.
* **The ``index.*`` consumer.** The ``api`` process hands S3 its started broker
  (:attr:`fcrm_common.service.ServiceDeps.broker`); S3 adds the subscriber for
  ``policy-service.events`` to it so it hears when H4 has built an index.
"""

from __future__ import annotations

import logging
from contextlib import AsyncExitStack
from typing import Optional

from evoq_context.core.config import ContextConfig
from fcrm_common.broker import wire_consumer_running
from fcrm_common.health import ReadinessCheck, ReadinessProbe
from fcrm_common.ids import new_id
from fcrm_common.mongo import Document, MongoLease
from fcrm_common.objectstore import ObjectStore
from fcrm_common.outbox import Outbox, OutboxRelay, RelayRunner
from fcrm_common.service import ServiceDeps
from pymongo.asynchronous.database import AsyncDatabase

from fcrm_policy.events import (
    POLICY_EVENTS_EXCHANGE,
    POLICY_EVENTS_ROUTING_KEY,
    PolicyEventsConsumer,
    VectorFixtureReloader,
)
from fcrm_policy.indexing import IndexStatusStore
from fcrm_policy.search.vector import VectorClauseSource
from fcrm_policy.settings import PolicySettings

logger = logging.getLogger(__name__)

OUTBOX_RELAY_LEASE_NAME = "outbox-relay:policy"
CONSUMED_MESSAGES_COLLECTION = "consumed_messages"


def start_relay(
    stack: AsyncExitStack,
    probe: ReadinessProbe,
    outbox: Outbox,
    db: AsyncDatabase[Document],
    deps: ServiceDeps,
) -> None:
    """Start the outbox relay under ``stack`` (stopped on exit) and register
    its readiness check, or log that there is no publisher.
    """
    if deps.publisher is None:
        logger.warning(
            "policy service has no publisher; its outbox is not relayed",
            extra={"outbox": outbox.collection.full_name},
        )
        return
    lease = MongoLease(
        db["leases"], OUTBOX_RELAY_LEASE_NAME, deps.lease_holder or new_id("relay")
    )
    relay = OutboxRelay(outbox.collection, lease, deps.publisher)
    runner = RelayRunner(relay, name="policy")
    runner.start()
    stack.push_async_callback(runner.stop)
    probe.add(ReadinessCheck("outbox", runner.check))


async def start_events_consumer(
    stack: AsyncExitStack,
    *,
    db: AsyncDatabase[Document],
    deps: ServiceDeps,
    settings: PolicySettings,
    status: IndexStatusStore,
    source: VectorClauseSource,
    config: ContextConfig,
    object_store: Optional[ObjectStore] = None,
) -> Optional[PolicyEventsConsumer]:
    """Add S3's ``index.*`` subscriber to the host's started broker; the
    subscribers stop when ``stack`` unwinds.

    Returns:
        The consumer, or ``None`` when the host gave S3 no broker or no
        publisher (a standalone S3 neither relays nor consumes).
    """
    broker = deps.broker
    if broker is None or deps.publisher is None:
        logger.warning("policy service has no broker; it will not hear index.built")
        return None
    reloader = VectorFixtureReloader(
        store=object_store or ObjectStore.from_settings(settings),
        bucket=settings.s3_bucket_vectors,
        source=source,
        config=config,
    )
    consumer = PolicyEventsConsumer(
        db[CONSUMED_MESSAGES_COLLECTION],
        deps.publisher,
        status=status,
        reloader=reloader,
    )
    await consumer.ensure_indexes()
    await status.ensure_indexes()
    await reloader.reload_latest(status)
    subscribers = await wire_consumer_running(
        broker,
        consumer,
        exchange=POLICY_EVENTS_EXCHANGE,
        routing_keys=[POLICY_EVENTS_ROUTING_KEY],
    )
    for subscriber in subscribers:
        stack.push_async_callback(subscriber.stop)
    return consumer
