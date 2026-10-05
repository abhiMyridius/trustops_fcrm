"""Publishing and consuming :class:`fcrm_contracts.events.envelope.Envelope`
messages, broker-agnostically (design A §4).

The broker client is not wired here: producers depend on the small
:class:`Publisher` protocol and consumers receive raw ``(body, headers)``
plus injected ``ack``/``nack`` callables, so this module is fully testable
without RabbitMQ and the FastStream-backed implementation is a thin
adapter the H1 lane provides. The adapter's job is only to map
:func:`message_headers` onto AMQP: ``message_id``, ``type`` and
``correlation_id`` headers become the AMQP properties of the same name
(design A §4.2), ``content_type`` is ``application/json`` and
``delivery_mode`` 2; every other key stays a header.

Delivery rules implemented (design A §4.5): at-least-once; consumers are
idempotent by ``message_id`` (``consumed_messages``, unique index, 7-day
TTL); a handler failure republishes to the ``retry`` exchange with
``x-attempt + 1`` while ``x-attempt < 3`` and to ``dlx`` after that, then
acks the original; an envelope that does not parse (poison) goes straight
to the DLQ; if the retry/DLQ publish itself fails the original is nacked
with requeue so nothing is ever dropped.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, timedelta
from enum import StrEnum
from typing import Any, Optional, Protocol

from fcrm_contracts.events.catalogue import EVENT_TYPES
from fcrm_contracts.events.envelope import Envelope
from opentelemetry import trace
from opentelemetry.trace import SpanKind
from pydantic import ValidationError as PydanticValidationError
from pymongo import ASCENDING, AsyncMongoClient, IndexModel
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.errors import DuplicateKeyError

from fcrm_common._otel import current_trace_ids
from fcrm_common.ids import new_id
from fcrm_common.logging import bound_context
from fcrm_common.mongo import Document, with_transaction
from fcrm_common.time import utcnow
from fcrm_common.tracing import extract_amqp, inject_amqp

logger = logging.getLogger(__name__)
_tracer = trace.get_tracer(__name__)

CONTENT_TYPE = "application/json"
ATTEMPT_HEADER = "x-attempt"
SCHEMA_HEADER = "x-schema"
MAX_ATTEMPTS = 3
RETRY_EXCHANGE = "retry"
DLX_EXCHANGE = "dlx"
CONSUMED_TTL = timedelta(days=7)

# design A §4.1: which exchange each event type is published to.
_JOB_EXCHANGES: dict[str, str] = {
    "agent.run": "agent.jobs",
    "agent.done": "agent.jobs",
    "agent.failed": "agent.jobs",
    "ingest.run": "ingestion.jobs",
    "index.build": "context.jobs",
}
CASE_EVENTS_EXCHANGE = "case.events"


class Publisher(Protocol):
    """What the broker adapter implements (FastStream/RabbitMQ in H1)."""

    async def publish(
        self, exchange: str, routing_key: str, body: bytes, headers: dict[str, Any]
    ) -> None:
        """Publish ``body`` and return once the broker confirmed it."""
        ...


class UnknownEventTypeError(ValueError):
    """The envelope's ``type`` is not in ``fcrm_contracts`` ``EVENT_TYPES``
    (design A §4.4: producers may not publish an uncatalogued type).
    """


def default_exchange(event_type: str) -> str:
    """The exchange an event type is published to (design A §4.1): the
    job types go to their direct exchanges, everything else to the
    ``case.events`` topic exchange.
    """
    return _JOB_EXCHANGES.get(event_type, CASE_EVENTS_EXCHANGE)


def check_event_type(event_type: str) -> None:
    """Raise :class:`UnknownEventTypeError` for an uncatalogued type."""
    if event_type not in EVENT_TYPES:
        raise UnknownEventTypeError(
            f"event type {event_type!r} is not in fcrm_contracts EVENT_TYPES"
        )


def build_envelope(
    event_type: str,
    payload: Mapping[str, Any],
    *,
    case_id: Optional[str] = None,
    run_id: Optional[str] = None,
    correlation_id: Optional[str] = None,
    version: int = 1,
) -> Envelope:
    """A complete envelope: fresh ``evt_`` id, ``occurred_at`` now (UTC),
    trace/span ids from the current span, ``correlation_id`` defaulting to
    the run id (one trace per case run, design A §4.2).
    """
    check_event_type(event_type)
    trace_id, span_id = current_trace_ids()
    return Envelope(
        event_id=new_id("evt"),
        type=event_type,
        version=version,
        case_id=case_id,
        run_id=run_id,
        correlation_id=correlation_id if correlation_id is not None else run_id,
        trace_id=trace_id,
        span_id=span_id,
        occurred_at=utcnow(),
        payload=dict(payload),
    )


def complete_envelope(envelope: Envelope) -> Envelope:
    """Fill what a caller left unset: an empty ``event_id``, a naive
    ``occurred_at`` (taken as UTC), missing trace/span ids, a missing
    ``correlation_id`` (the run id). Returns a copy; never mutates.
    """
    updates: dict[str, Any] = {}
    if not envelope.event_id:
        updates["event_id"] = new_id("evt")
    if envelope.occurred_at.tzinfo is None:
        updates["occurred_at"] = envelope.occurred_at.replace(tzinfo=UTC)
    if envelope.trace_id is None:
        trace_id, span_id = current_trace_ids()
        if trace_id is not None:
            updates["trace_id"] = trace_id
            updates["span_id"] = span_id
    if envelope.correlation_id is None and envelope.run_id is not None:
        updates["correlation_id"] = envelope.run_id
    return envelope.model_copy(update=updates) if updates else envelope


def encode_envelope(envelope: Envelope) -> bytes:
    """The message body: the envelope as UTF-8 JSON."""
    return envelope.model_dump_json().encode("utf-8")


def decode_envelope(body: bytes) -> Envelope:
    """Parse a message body; raises ``pydantic.ValidationError`` on poison."""
    return Envelope.model_validate_json(body)


def message_headers(envelope: Envelope, attempt: int = 0) -> dict[str, Any]:
    """Headers for publishing ``envelope``: W3C ``traceparent``/``tracestate``
    of the current context, ``x-attempt``, ``x-schema`` =
    ``fcrm.events/<type>@<version>``, and ``message_id``/``type``/
    ``correlation_id`` for the adapter to lift into AMQP properties.
    """
    headers: dict[str, Any] = {
        "message_id": envelope.event_id,
        "type": envelope.type,
        "content_type": CONTENT_TYPE,
        ATTEMPT_HEADER: attempt,
        SCHEMA_HEADER: f"fcrm.events/{envelope.type}@{envelope.version}",
    }
    if envelope.correlation_id is not None:
        headers["correlation_id"] = envelope.correlation_id
    return inject_amqp(headers)


async def publish(
    publisher: Publisher,
    exchange: str,
    routing_key: str,
    envelope: Envelope,
    *,
    check_type: bool = True,
) -> Envelope:
    """Complete ``envelope`` (:func:`complete_envelope`) and publish it.

    This publishes *directly*. A service that also changes state must not
    call it inside its request: write the envelope through
    :meth:`fcrm_common.outbox.Outbox.enqueue` in the same transaction and
    let the relay publish (BUILD-PLAN §3.9).

    Returns:
        The envelope exactly as published.
    """
    if check_type:
        check_event_type(envelope.type)
    final = complete_envelope(envelope)
    await publisher.publish(
        exchange, routing_key, encode_envelope(final), message_headers(final)
    )
    logger.debug(
        "published %s",
        final.type,
        extra={
            "amqp": {
                "exchange": exchange,
                "routing_key": routing_key,
                "message_id": final.event_id,
            }
        },
    )
    return final


class Disposition(StrEnum):
    """What happened to one delivery."""

    PROCESSED = "processed"
    DUPLICATE = "duplicate"
    RETRIED = "retried"
    DEAD_LETTERED = "dead_lettered"
    REQUEUED = "requeued"


AckFn = Callable[[], Awaitable[None]]
NackFn = Callable[[bool], Awaitable[None]]  # argument: requeue
HandlerFn = Callable[[Envelope], Awaitable[Disposition]]


def attempt_of(headers: Optional[Mapping[str, Any]]) -> int:
    """The ``x-attempt`` header as an int (0 on first delivery)."""
    raw = (headers or {}).get(ATTEMPT_HEADER, 0)
    if isinstance(raw, bytes):
        raw = raw.decode("ascii", errors="replace")
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        logger.warning("unparseable %s header %r; treating as 0", ATTEMPT_HEADER, raw)
        return 0


async def _republish(
    publisher: Publisher,
    exchange: str,
    queue: str,
    body: bytes,
    headers: Mapping[str, Any],
    attempt: int,
    error: str,
) -> None:
    """Send the original body to the retry or dead-letter exchange."""
    out = {**headers, ATTEMPT_HEADER: attempt, "x-last-error": error}
    await publisher.publish(exchange, queue, body, out)


async def deliver_with_retry(
    handler: HandlerFn,
    body: bytes,
    headers: Optional[Mapping[str, Any]],
    *,
    queue: str,
    publisher: Publisher,
    ack: AckFn,
    nack: NackFn,
    max_attempts: int = MAX_ATTEMPTS,
) -> Disposition:
    """Run ``handler`` on one delivery with the design A §4.5 semantics.

    Parses the envelope (poison -> DLQ), binds ``case_id``/``run_id`` into
    the logging context, awaits ``handler``, then acks (trace continuation
    is the caller's: :meth:`Consumer.process` opens a consumer span on the
    extracted context). On a handler exception the message is
    republished to ``retry``/``<queue>`` with ``x-attempt + 1`` while
    ``x-attempt < max_attempts``, otherwise to ``dlx``/``<queue>``; the
    original is acked once the republish is confirmed. If the republish
    fails the original is nacked with requeue (never dropped).

    The broad ``except Exception`` clauses here and in the relay are
    deliberate: this is the consumer's last line, where *any* handler failure
    must become a retry or a DLQ entry rather than a crashed consumer.
    """
    hdrs = dict(headers or {})
    attempt = attempt_of(hdrs)
    try:
        envelope = decode_envelope(body)
    except PydanticValidationError as exc:
        logger.error(
            "poison message on %s; dead-lettering",
            queue,
            extra={"amqp": {"queue": queue, "attempt": attempt}},
        )
        return await _park(
            publisher,
            DLX_EXCHANGE,
            queue,
            body,
            hdrs,
            attempt,
            f"poison: {exc.error_count()} errors",
            ack,
            nack,
            Disposition.DEAD_LETTERED,
        )
    amqp = {"queue": queue, "message_id": envelope.event_id, "attempt": attempt}
    with bound_context(case_id=envelope.case_id, run_id=envelope.run_id, amqp=amqp):
        try:
            outcome = await handler(envelope)
        except Exception as exc:  # noqa: BLE001 - see docstring
            retry = attempt < max_attempts
            logger.warning(
                "handler failed on %s (%s); %s",
                queue,
                type(exc).__name__,
                "retrying" if retry else "dead-lettering",
                exc_info=exc,
            )
            return await _park(
                publisher,
                RETRY_EXCHANGE if retry else DLX_EXCHANGE,
                queue,
                body,
                hdrs,
                attempt + 1 if retry else attempt,
                type(exc).__name__,
                ack,
                nack,
                Disposition.RETRIED if retry else Disposition.DEAD_LETTERED,
            )
        await ack()
        return outcome


async def _park(
    publisher: Publisher,
    exchange: str,
    queue: str,
    body: bytes,
    headers: Mapping[str, Any],
    attempt: int,
    error: str,
    ack: AckFn,
    nack: NackFn,
    disposition: Disposition,
) -> Disposition:
    """Republish to ``exchange`` then ack, or nack-requeue if that fails."""
    try:
        await _republish(publisher, exchange, queue, body, headers, attempt, error)
    except Exception:  # noqa: BLE001 - broker down: requeue, never drop
        logger.exception("could not publish to %s for %s; requeueing", exchange, queue)
        await nack(True)
        return Disposition.REQUEUED
    await ack()
    return disposition


class Consumer(ABC):
    """Base class for an idempotent queue consumer.

    Subclasses implement :meth:`handle`. :meth:`process` is what the broker
    adapter calls per delivery. Dedupe is by ``(consumer, message_id)`` in
    ``consumed_messages`` (unique index; documents expire after 7 days):

    - with a ``client`` (replica set), the dedupe marker and the handler's
      writes commit in one transaction — a crash between them cannot leave
      the work done but unmarked, or marked but undone;
    - without one, the marker is written after the handler succeeds, so a
      crash in between re-runs the handler: it must be idempotent by its
      own domain keys (design A §4.5 requires that anyway).

    The dedupe key includes the consumer name so two consumers sharing a
    database (each gets its own copy of a topic event) never suppress each
    other's deliveries.

    Args:
        queue: The queue this consumer reads; also its retry/DLQ routing key.
        consumed: The ``consumed_messages`` collection.
        publisher: For retry/DLQ republishing.
        client: Enables the transactional mode above.
        max_attempts: Retries before the DLQ.
    """

    def __init__(
        self,
        queue: str,
        consumed: AsyncCollection[Document],
        publisher: Publisher,
        *,
        client: Optional[AsyncMongoClient[Document]] = None,
        max_attempts: int = MAX_ATTEMPTS,
    ) -> None:
        self.queue = queue
        self._consumed = consumed
        self._publisher = publisher
        self._client = client
        self._max_attempts = max_attempts

    @abstractmethod
    async def handle(
        self, envelope: Envelope, session: Optional[AsyncClientSession]
    ) -> None:
        """Apply one message. Do every Mongo write through ``session`` when it
        is not ``None``. Raise to trigger retry/DLQ.
        """

    async def ensure_indexes(self) -> list[str]:
        """Unique ``(message_id, consumer)`` and the 7-day TTL."""
        return await self._consumed.create_indexes(
            [
                IndexModel(
                    [("message_id", ASCENDING), ("consumer", ASCENDING)],
                    unique=True,
                    name="message_id_consumer_unique",
                ),
                IndexModel(
                    [("consumed_at", ASCENDING)],
                    expireAfterSeconds=int(CONSUMED_TTL.total_seconds()),
                    name="consumed_at_ttl",
                ),
            ]
        )

    def _marker(self, envelope: Envelope) -> Document:
        """The dedupe document for ``envelope``."""
        return {
            "message_id": envelope.event_id,
            "consumer": self.queue,
            "type": envelope.type,
            "case_id": envelope.case_id,
            "consumed_at": utcnow(),
        }

    async def _already_consumed(self, envelope: Envelope) -> bool:
        """Whether this consumer has a marker for ``envelope``."""
        found = await self._consumed.find_one(
            {"message_id": envelope.event_id, "consumer": self.queue},
            projection={"_id": 1},
        )
        return found is not None

    async def _apply(self, envelope: Envelope) -> Disposition:
        """Dedupe then handle, transactionally when a client is configured."""
        if self._client is not None:
            return await self._apply_in_transaction(self._client, envelope)
        if await self._already_consumed(envelope):
            return Disposition.DUPLICATE
        await self.handle(envelope, None)
        try:
            await self._consumed.insert_one(self._marker(envelope))
        except DuplicateKeyError:
            logger.info("concurrent duplicate of %s completed first", envelope.event_id)
        return Disposition.PROCESSED

    async def _apply_in_transaction(
        self, client: AsyncMongoClient[Document], envelope: Envelope
    ) -> Disposition:
        """Marker insert and handler writes in one transaction."""

        async def txn(session: AsyncClientSession) -> Disposition:
            await self._consumed.insert_one(self._marker(envelope), session=session)
            await self.handle(envelope, session)
            return Disposition.PROCESSED

        try:
            return await with_transaction(client, txn)
        except DuplicateKeyError:
            return Disposition.DUPLICATE

    async def process(
        self,
        body: bytes,
        headers: Optional[Mapping[str, Any]],
        ack: AckFn,
        nack: NackFn,
    ) -> Disposition:
        """Handle one delivery end to end: trace continuation, dedupe, the
        handler, ack, and retry/DLQ on failure (:func:`deliver_with_retry`).
        """
        with _tracer.start_as_current_span(
            f"{self.queue} process",
            context=extract_amqp(headers),
            kind=SpanKind.CONSUMER,
            attributes={"messaging.destination.name": self.queue},
        ):
            disposition = await deliver_with_retry(
                self._apply,
                body,
                headers,
                queue=self.queue,
                publisher=self._publisher,
                ack=ack,
                nack=nack,
                max_attempts=self._max_attempts,
            )
        if disposition is Disposition.DUPLICATE:
            logger.info("duplicate delivery acked", extra={"queue": self.queue})
        return disposition
