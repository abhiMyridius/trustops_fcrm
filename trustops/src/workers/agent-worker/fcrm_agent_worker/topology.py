"""The exact RabbitMQ topology this worker binds to, read off
``ops/rabbitmq/definitions.json`` by hand (not generated: that file is
ops-owned and this module is the one place its names are repeated).

Ops declares every exchange, queue and binding at boot
(``rabbitmq.conf`` imports ``definitions.json``); per that file's own
``_comment``, "clients must declare these queues passively (or with
exactly these arguments): ops owns the topology." Every
:class:`~faststream.rabbit.schemas.queue.RabbitQueue` and
:class:`~faststream.rabbit.schemas.exchange.RabbitExchange` this module
builds therefore sets ``declare=False``: FastStream connects to the named
object instead of trying to create it.

Exchanges (``definitions.json: exchanges``): ``agent.jobs`` and
``retry``/``dlx`` are ``direct``; ``case.events`` is the only ``topic``
exchange this worker ever publishes to (it does not, today, but
:mod:`fcrm_agent_worker.publisher` is generic over any catalogued event
type, so the type map is complete).
"""

from __future__ import annotations

from typing import Final

from faststream.rabbit import ExchangeType, RabbitExchange, RabbitQueue

#: ``agent.run`` lands here (binding: exchange ``agent.jobs``, routing key
#: ``agent.run`` -> queue ``agent-worker.run``).
AGENT_WORKER_RUN_QUEUE: Final = "agent-worker.run"

#: The exchange H1 publishes ``agent.run`` to and H2 publishes
#: ``agent.done``/``agent.failed`` to (``_JOB_EXCHANGES`` in
#: ``fcrm_common.events`` agrees: every ``agent.*`` type routes here).
AGENT_JOBS_EXCHANGE: Final = "agent.jobs"

ROUTING_KEY_AGENT_RUN: Final = "agent.run"
ROUTING_KEY_AGENT_DONE: Final = "agent.done"
ROUTING_KEY_AGENT_FAILED: Final = "agent.failed"

#: Every exchange's type, exactly as ``ops/rabbitmq/definitions.json``
#: declares it. ``fcrm_common.events.RETRY_EXCHANGE`` /
#: ``DLX_EXCHANGE`` name the same two "direct" exchanges this worker's
#: retry/DLQ republishing targets.
EXCHANGE_TYPES: Final[dict[str, ExchangeType]] = {
    "case.events": ExchangeType.TOPIC,
    "agent.jobs": ExchangeType.DIRECT,
    "ingestion.jobs": ExchangeType.DIRECT,
    "context.jobs": ExchangeType.DIRECT,
    "retry": ExchangeType.DIRECT,
    "dlx": ExchangeType.DIRECT,
}


def passive_queue(name: str, *, routing_key: str = "") -> RabbitQueue:
    """A durable quorum queue this worker only connects to (never declares).

    Matches ``definitions.json``'s ``x-queue-type: quorum`` queues; the
    actual arguments (DLX routing, delivery limit) are ops's, not repeated
    here since ``declare=False`` never sends a declare frame. ``routing_key``
    is likewise never sent as a bind (``declare=False``); it only tells
    FastStream which key this queue answers to, for its AsyncAPI metadata
    and for ``TestRabbitBroker``'s in-memory publish/subscribe matching --
    the real broker already bound it, by ops.
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
