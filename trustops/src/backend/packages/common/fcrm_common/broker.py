"""RabbitMQ plumbing shared by every FastStream consumer process.

Ops owns the topology (``ops/rabbitmq/definitions.json``): every exchange,
queue and binding is declared at broker boot, and per that file's own
``_comment`` "clients must declare these queues passively: ops owns the
topology". Everything here therefore connects with ``declare=False``.

* :func:`passive_queue` / :func:`passive_exchange` name an existing queue or
  exchange without declaring it;
* :class:`FastStreamPublisher` is the :class:`fcrm_common.events.Publisher`
  over one ``RabbitBroker``: it maps :func:`fcrm_common.events.message_headers`
  onto AMQP properties and publishes with confirms, and is the only place a
  worker talks to the broker for publishing (``fcrm_common.events`` stays
  broker-agnostic);
* :func:`wire_consumer` registers the manually-acked subscribers that hand
  every delivery to ``Consumer.process`` (dedupe, retry, DLQ).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any, Final, Optional

from faststream import AckPolicy
from faststream.rabbit import (
    Channel,
    ExchangeType,
    RabbitBroker,
    RabbitExchange,
    RabbitMessage,
    RabbitQueue,
)

from fcrm_common.events import Consumer

logger = logging.getLogger(__name__)

#: Every exchange's type, exactly as ``ops/rabbitmq/definitions.json`` declares it.
EXCHANGE_TYPES: Final[dict[str, ExchangeType]] = {
    "case.events": ExchangeType.TOPIC,
    "agent.jobs": ExchangeType.DIRECT,
    "ingestion.jobs": ExchangeType.DIRECT,
    "context.jobs": ExchangeType.DIRECT,
    "retry": ExchangeType.DIRECT,
    "dlx": ExchangeType.DIRECT,
}

#: Headers :func:`fcrm_common.events.message_headers` sets that become AMQP
#: message properties rather than staying in the headers table.
_PROPERTY_HEADERS: Final[dict[str, str]] = {
    "message_id": "message_id",
    "correlation_id": "correlation_id",
    "content_type": "content_type",
    "type": "message_type",  # aio_pika's message "type" property
}


def passive_queue(name: str, *, routing_key: str = "") -> RabbitQueue:
    """A durable quorum queue this process only connects to (never declares).

    The queue's real arguments (DLX routing, delivery limit) are ops's and
    are not repeated here, since ``declare=False`` never sends a declare
    frame. ``routing_key`` is likewise never sent as a bind: it only tells
    FastStream which key the queue answers to, for its AsyncAPI metadata and
    for ``TestRabbitBroker``'s in-memory matching; the broker already bound
    it, by ops.
    """
    return RabbitQueue(name=name, durable=True, declare=False, routing_key=routing_key)


def passive_exchange(name: str) -> RabbitExchange:
    """The named exchange, connected to passively (ops already declared it)."""
    return RabbitExchange(
        name=name,
        type=EXCHANGE_TYPES.get(name, ExchangeType.DIRECT),
        durable=True,
        declare=False,
    )


class FastStreamPublisher:
    """``fcrm_common.events.Publisher`` over one FastStream ``RabbitBroker``.

    ``message_id``, ``correlation_id``, ``content_type`` and ``type`` headers
    become the AMQP properties of the same purpose, ``delivery_mode`` is 2
    (``persist=True``) and every other header stays a header. Every exchange
    is connected to passively.
    """

    def __init__(self, broker: RabbitBroker) -> None:
        """Publish through ``broker`` (connected by the host's lifespan)."""
        self._broker = broker
        self._exchanges: dict[str, RabbitExchange] = {}

    def _exchange(self, name: str) -> RabbitExchange:
        """The cached, passively-connected exchange object for ``name``."""
        exchange = self._exchanges.get(name)
        if exchange is None:
            exchange = passive_exchange(name)
            self._exchanges[name] = exchange
        return exchange

    async def publish(
        self, exchange: str, routing_key: str, body: bytes, headers: dict[str, Any]
    ) -> None:
        """Publish ``body`` with ``headers`` lifted onto AMQP properties;
        returns once RabbitMQ confirmed it (publisher confirms are on by
        default for a ``RabbitBroker``).
        """
        remaining = dict(headers)
        properties = {
            kwarg: remaining.pop(header, None)
            for header, kwarg in _PROPERTY_HEADERS.items()
        }
        await self._broker.publish(
            body,
            exchange=self._exchange(exchange),
            routing_key=routing_key,
            persist=True,
            headers=remaining,
            **properties,
        )
        logger.debug(
            "published to broker",
            extra={
                "amqp": {
                    "exchange": exchange,
                    "routing_key": routing_key,
                    "message_id": properties.get("message_id"),
                }
            },
        )


def wire_consumer(
    broker: RabbitBroker,
    consumer: Consumer,
    *,
    exchange: str,
    routing_keys: Sequence[str],
    prefetch: Optional[int] = None,
) -> None:
    """Register one manually-acked subscriber per routing key on
    ``consumer.queue``, delegating every delivery to ``consumer.process``.

    ``ack_policy=AckPolicy.MANUAL`` disables FastStream's own ack/nack so
    ``consumer.process`` is the only thing that ever acks or nacks a
    delivery. ``prefetch`` sets the channel's QoS: with aio-pika each
    delivery's callback runs as its own task, so it is also the number of
    deliveries this consumer works on concurrently.

    Args:
        broker: An unstarted broker; subscribers must be added before start.
        consumer: The consumer whose queue to bind and whose ``process`` to call.
        exchange: Exchange the queue is bound to (passive).
        routing_keys: The keys the queue answers to, one subscriber each.
        prefetch: Channel prefetch count; ``None`` leaves the broker default.
    """
    channel = Channel(prefetch_count=prefetch) if prefetch is not None else None
    for routing_key in routing_keys:
        broker.subscriber(
            passive_queue(consumer.queue, routing_key=routing_key),
            passive_exchange(exchange),
            ack_policy=AckPolicy.MANUAL,
            channel=channel,
            decoder=_undecoded_body,
        )(_handler_for(consumer))


async def wire_consumer_running(
    broker: RabbitBroker,
    consumer: Consumer,
    *,
    exchange: str,
    routing_keys: Sequence[str],
    prefetch: Optional[int] = None,
) -> list[Any]:
    """:func:`wire_consumer` for a broker that is **already started**: a service
    inside the ``api`` process learns its broker only after the host has
    connected it, and FastStream starts a subscriber added after that only when
    told to.

    Returns:
        The subscribers added, already running; the caller stops each one
        (``await subscriber.stop()``) before the host stops the broker.
    """
    known = {id(sub) for sub in broker.subscribers}
    wire_consumer(
        broker,
        consumer,
        exchange=exchange,
        routing_keys=routing_keys,
        prefetch=prefetch,
    )
    added = [sub for sub in broker.subscribers if id(sub) not in known]
    for subscriber in added:
        await subscriber.start()
    return added


async def _undecoded_body(msg: RabbitMessage) -> bytes:
    """A FastStream decoder that leaves the body as the bytes it arrived as.

    ``Consumer.process`` parses the envelope itself so that a body that does not
    parse is a dead letter. FastStream's own decoder runs first, and for a
    message whose ``content_type`` is ``application/json`` (every message this
    system publishes) it raises on a body that is not JSON, *before* the handler
    and with manual acks: the message was then neither acked nor nacked and sat
    unacknowledged on the channel, holding one of the consumer's prefetch slots
    until the process restarted (measured on a broker: two such messages stalled
    a consumer with a prefetch of two; the poison drill D5b covers it).
    """
    return msg.body


def delivery_headers(msg: RabbitMessage) -> dict[str, Any]:
    """``msg``'s headers plus its AMQP properties, under the names
    :func:`fcrm_common.events.message_headers` gives them.

    ``Consumer.process`` republishes a failed message to the retry queue or the
    dead-letter queue from ``(body, headers)`` alone, and
    :class:`FastStreamPublisher` lifts ``message_id``, ``correlation_id``,
    ``content_type`` and ``type`` out of the headers it is given. With only
    ``msg.headers`` every retry hop dropped them, so a message that parked in a
    DLQ had lost the id it was published with (and its type and the run it
    belonged to) and an operator could not find it by that id. The properties
    are read from the raw delivery, not the parsed message, because FastStream
    makes up a message id and a correlation id when the sender set none.
    """
    headers = dict(msg.headers or {})
    raw = msg.raw_message
    for header, attribute in (
        ("message_id", "message_id"),
        ("correlation_id", "correlation_id"),
        ("content_type", "content_type"),
        ("type", "type"),
    ):
        value = getattr(raw, attribute, None)
        if value is not None:
            headers.setdefault(header, value)
    return headers


def _handler_for(consumer: Consumer) -> Any:
    """The subscriber callback that hands a delivery to ``consumer.process``."""

    async def _consume(msg: RabbitMessage) -> None:
        await consumer.process(
            msg.body,
            delivery_headers(msg),
            ack=msg.ack,
            nack=lambda requeue: msg.nack(requeue=requeue),
        )

    return _consume
