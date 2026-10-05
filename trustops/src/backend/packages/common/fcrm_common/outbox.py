"""Transactional outbox and its relay (BUILD-PLAN §3.9, design A §1.4, §3.5).

The rule (CLAUDE.md): every state change and the event announcing it
commit together. A service never publishes to RabbitMQ inside a request;
it calls :meth:`Outbox.enqueue` with the *same session* as its state
write, inside :func:`fcrm_common.mongo.with_transaction`, so either both
the state and the outbox row exist or neither does. The
:class:`OutboxRelay` then publishes unpublished rows in ``_id`` order and
stamps ``published_at``.

Delivery is at-least-once: if the broker confirms but the ``published_at``
write fails (or leadership changes hands mid-batch), the row is published
again on a later tick, and consumers dedupe by ``message_id``
(:class:`fcrm_common.events.Consumer`). Order is preserved per relay: a
failed publish stops the batch, so a later row never overtakes an earlier
one.

Only one relay replica publishes: leadership is a
:class:`fcrm_common.mongo.MongoLease` (10 s TTL, renewed every 3 s).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from time import monotonic
from typing import Optional, Protocol

from bson import ObjectId
from fcrm_contracts.events.envelope import Envelope
from pymongo import ASCENDING, IndexModel
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.errors import PyMongoError

from fcrm_common.events import (
    Publisher,
    check_event_type,
    complete_envelope,
    default_exchange,
    encode_envelope,
    message_headers,
)
from fcrm_common.metrics import FcrmMetrics, get_metrics
from fcrm_common.mongo import Document
from fcrm_common.time import utcnow

logger = logging.getLogger(__name__)

PUBLISHED_TTL = timedelta(days=7)
DRAIN_TIMEOUT_S = 10.0


class OutboxTransactionError(RuntimeError):
    """``enqueue`` was called without an active transaction, which would
    break the atomicity the outbox exists to provide.
    """


class Lease(Protocol):
    """What the relay needs from a leadership lease
    (:class:`fcrm_common.mongo.MongoLease` implements it).
    """

    @property
    def held(self) -> bool:
        """Whether this process currently holds the lease."""
        ...

    async def acquire(self) -> bool:
        """Acquire or renew; returns whether the lease is held."""
        ...

    async def release(self) -> None:
        """Give the lease up."""
        ...


class Outbox:
    """Writes outbox rows inside the caller's transaction.

    Args:
        collection: The unit's ``outbox`` collection (same database as the
            state it announces, so one transaction covers both).
    """

    def __init__(self, collection: AsyncCollection[Document]) -> None:
        self._collection = collection

    @property
    def collection(self) -> AsyncCollection[Document]:
        """The ``outbox`` collection."""
        return self._collection

    async def ensure_indexes(self) -> list[str]:
        """The relay's scan index, ``event_id`` uniqueness, and a 7-day TTL on
        published rows (unpublished rows have ``published_at: null`` and
        never expire).
        """
        return await self._collection.create_indexes(
            [
                IndexModel(
                    [("published_at", ASCENDING), ("_id", ASCENDING)],
                    name="unpublished_scan",
                ),
                IndexModel(
                    [("event_id", ASCENDING)], unique=True, name="event_id_unique"
                ),
                IndexModel(
                    [("published_at", ASCENDING)],
                    expireAfterSeconds=int(PUBLISHED_TTL.total_seconds()),
                    name="published_ttl",
                ),
            ]
        )

    async def enqueue(
        self,
        session: AsyncClientSession,
        envelope: Envelope,
        *,
        exchange: Optional[str] = None,
        routing_key: Optional[str] = None,
    ) -> Envelope:
        """Write ``envelope`` to the outbox within ``session``'s transaction.

        The envelope is completed (:func:`fcrm_common.events.complete_envelope`)
        and the current trace context is captured into the stored headers
        *now*, so the relay's later publish continues the request's trace.

        Args:
            session: The caller's session, inside an active transaction.
            envelope: The event; ``type`` must be catalogued.
            exchange: Defaults to :func:`fcrm_common.events.default_exchange`.
            routing_key: Defaults to the event type.

        Returns:
            The completed envelope as it will be published.

        Raises:
            OutboxTransactionError: ``session`` has no active transaction.
            fcrm_common.events.UnknownEventTypeError: Uncatalogued type.
        """
        if not session.in_transaction:
            raise OutboxTransactionError(
                "Outbox.enqueue must run inside the caller's transaction"
            )
        check_event_type(envelope.type)
        final = complete_envelope(envelope)
        await self._collection.insert_one(
            {
                "_id": ObjectId(),
                "event_id": final.event_id,
                "type": final.type,
                "case_id": final.case_id,
                "exchange": exchange or default_exchange(final.type),
                "routing_key": routing_key or final.type,
                "body": encode_envelope(final),
                "headers": message_headers(final),
                "created_at": utcnow(),
                "published_at": None,
                "attempts": 0,
                "last_error": None,
            },
            session=session,
        )
        return final


class OutboxRelay:
    """Publishes outbox rows while holding the leadership lease.

    Args:
        collection: The ``outbox`` collection.
        lease: Leadership lease (one publishing replica at a time).
        publisher: Broker adapter.
        interval_s: Poll interval when healthy (design A: 200 ms).
        renew_every_s: Lease renew/acquire cadence (design A: 3 s).
        max_backoff_s: Poll interval cap while the broker is failing (5 s).
        batch_size: Rows per poll.
        metrics: Instrument set (defaults to the process-wide one).
    """

    def __init__(
        self,
        collection: AsyncCollection[Document],
        lease: Lease,
        publisher: Publisher,
        *,
        interval_s: float = 0.2,
        renew_every_s: float = 3.0,
        max_backoff_s: float = 5.0,
        batch_size: int = 100,
        metrics: Optional[FcrmMetrics] = None,
    ) -> None:
        self._collection = collection
        self._lease = lease
        self._publisher = publisher
        self._interval_s = interval_s
        self._renew_every_s = renew_every_s
        self._max_backoff_s = max_backoff_s
        self._batch_size = batch_size
        self._metrics = metrics if metrics is not None else get_metrics()
        self._last_lease_attempt: Optional[float] = None

    async def maintain_lease(self) -> bool:
        """Acquire or renew the lease every ``renew_every_s`` (a standby
        replica retries at the same cadence, so it takes over within one TTL
        plus one renew period of the leader dying); returns whether it is
        held. A Mongo error counts as not renewing (the lease will lapse).
        """
        now = monotonic()
        last = self._last_lease_attempt
        if last is None or now - last >= self._renew_every_s:
            self._last_lease_attempt = now
            try:
                await self._lease.acquire()
            except PyMongoError:
                logger.warning("outbox relay could not renew its lease", exc_info=True)
        return self._lease.held

    async def publish_batch(self) -> tuple[int, bool]:
        """Publish up to ``batch_size`` unpublished rows in ``_id`` order.

        Returns:
            ``(published, failed)``: rows published, and whether a publish
            failed (the batch stops at the first failure to keep order).
        """
        docs = await self._collection.find(
            {"published_at": None}, sort=[("_id", ASCENDING)], limit=self._batch_size
        ).to_list()
        published = 0
        failed = False
        for doc in docs:
            if not self._lease.held:
                logger.info("outbox relay lost its lease mid-batch; stopping")
                break
            if not await self._publish_one(doc):
                failed = True
                break
            published += 1
        await self.record_lag()
        return published, failed

    async def _publish_one(self, doc: Document) -> bool:
        """Publish one row and stamp it; returns ``False`` on broker failure."""
        try:
            await self._publisher.publish(
                doc["exchange"],
                doc["routing_key"],
                bytes(doc["body"]),
                dict(doc["headers"]),
            )
        except Exception as exc:  # noqa: BLE001 - any broker error: keep the row
            logger.warning(
                "outbox publish failed; will retry",
                extra={
                    "amqp": {
                        "exchange": doc["exchange"],
                        "message_id": doc["event_id"],
                    },
                    "error": {"type": type(exc).__name__},
                },
            )
            await self._collection.update_one(
                {"_id": doc["_id"]},
                {"$inc": {"attempts": 1}, "$set": {"last_error": type(exc).__name__}},
            )
            return False
        await self._collection.update_one(
            {"_id": doc["_id"], "published_at": None},
            {"$set": {"published_at": utcnow()}},
        )
        return True

    async def record_lag(self) -> float:
        """Set ``fcrm_outbox_lag_seconds`` to the age of the oldest unpublished
        row (0 when the outbox is empty) and return it.
        """
        oldest = await self._collection.find_one(
            {"published_at": None},
            sort=[("_id", ASCENDING)],
            projection={"created_at": 1},
        )
        lag = 0.0
        if oldest is not None and oldest.get("created_at") is not None:
            lag = max(0.0, (utcnow() - oldest["created_at"]).total_seconds())
        self._metrics.outbox_lag_seconds.set(
            lag, attributes={"outbox": self._collection.full_name}
        )
        return lag

    async def tick(self) -> tuple[int, bool]:
        """One relay iteration: maintain the lease, publish if leader."""
        if not await self.maintain_lease():
            return 0, False
        return await self.publish_batch()

    async def run(self, stop: asyncio.Event) -> None:
        """Loop until ``stop`` is set, then drain (bounded) and release.

        The poll interval doubles up to ``max_backoff_s`` while publishes
        fail or Mongo is unreachable, and resets on the first success.
        """
        delay = self._interval_s
        while not stop.is_set():
            try:
                _, failed = await self.tick()
            except PyMongoError:
                logger.warning("outbox relay tick failed on mongo", exc_info=True)
                failed = True
            delay = min(delay * 2, self._max_backoff_s) if failed else self._interval_s
            await _sleep_unless(stop, delay)
        await self.drain()
        await self._lease.release()

    async def drain(self, timeout_s: float = DRAIN_TIMEOUT_S) -> int:
        """Publish what is pending at shutdown, for at most ``timeout_s``
        (design A §10.4). Returns rows published; leftovers stay for the
        next leader.
        """
        total = 0
        try:
            async with asyncio.timeout(timeout_s):
                while self._lease.held:
                    published, failed = await self.publish_batch()
                    total += published
                    if failed or published == 0:
                        break
        except TimeoutError:
            logger.warning("outbox drain timed out; rows left for the next leader")
        except PyMongoError:
            logger.warning("outbox drain stopped on a mongo error", exc_info=True)
        return total


async def _sleep_unless(stop: asyncio.Event, delay_s: float) -> None:
    """Sleep ``delay_s`` or until ``stop`` is set, whichever is first."""
    try:
        async with asyncio.timeout(delay_s):
            await stop.wait()
    except TimeoutError:
        return
