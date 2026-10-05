"""The :class:`fcrm_common.events.Publisher` adapter: maps
:func:`fcrm_common.events.message_headers` onto AMQP exactly as
``fcrm_common.events`` (module docstring) specifies: ``message_id``,
``type`` and ``correlation_id`` headers become the AMQP properties of the
same name, ``content_type`` is ``application/json``, ``delivery_mode`` 2
(FastStream's ``persist=True``); every other key stays a header.

This is the only place :mod:`fcrm_agent_worker` talks to the broker for
publishing; :mod:`fcrm_common.events` (``publish``, ``deliver_with_retry``,
``Consumer``) stays broker-agnostic and calls back through this adapter's
:meth:`FastStreamPublisher.publish`.
"""

from __future__ import annotations

import logging
from typing import Any

from faststream.rabbit import RabbitBroker, RabbitExchange

from fcrm_agent_worker.topology import passive_exchange

logger = logging.getLogger(__name__)

#: Headers :func:`fcrm_common.events.message_headers` sets that become AMQP
#: message properties rather than staying in the headers table.
_PROPERTY_HEADERS: dict[str, str] = {
    "message_id": "message_id",
    "correlation_id": "correlation_id",
    "content_type": "content_type",
    "type": "message_type",  # aio_pika's message "type" property
}


class FastStreamPublisher:
    """``fcrm_common.events.Publisher`` over one FastStream ``RabbitBroker``.

    Every exchange is connected to passively (:func:`passive_exchange`):
    ops owns the topology (``ops/rabbitmq/definitions.json``), this worker
    never declares it.
    """

    def __init__(self, broker: RabbitBroker) -> None:
        """Publish through ``broker`` (already connected by the app lifespan)."""
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
        """Publish ``body`` with ``headers`` lifted onto AMQP properties
        (module docstring); returns once RabbitMQ confirmed it (FastStream's
        publisher confirms are on by default for a ``RabbitBroker``).
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
