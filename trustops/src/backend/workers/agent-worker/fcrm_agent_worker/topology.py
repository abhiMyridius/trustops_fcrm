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

The passive queue and exchange helpers and the exchange type map are
shared (:mod:`fcrm_common.broker`).
"""

from __future__ import annotations

from typing import Final

from fcrm_common.broker import EXCHANGE_TYPES, passive_exchange, passive_queue

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

__all__ = [
    "AGENT_JOBS_EXCHANGE",
    "AGENT_WORKER_RUN_QUEUE",
    "EXCHANGE_TYPES",
    "ROUTING_KEY_AGENT_DONE",
    "ROUTING_KEY_AGENT_FAILED",
    "ROUTING_KEY_AGENT_RUN",
    "passive_exchange",
    "passive_queue",
]
