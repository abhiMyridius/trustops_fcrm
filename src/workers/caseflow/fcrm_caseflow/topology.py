"""The exact RabbitMQ topology Caseflow binds to, read off
``ops/rabbitmq/definitions.json`` by hand (not generated: that file is
ops-owned; this module is the one place its names are repeated for H1,
the same convention :mod:`fcrm_agent_worker.topology` establishes for H2).

Design A §4.1's topology table gives Caseflow three consumers ("case
events, ``agent.done``/``agent.failed``, ``ingest.done``/``index.built``"),
but the *real*, already-deployed ``definitions.json`` only binds two queues
to anything Caseflow owns today:

- ``caseflow.events`` <- exchange ``case.events`` (topic), routing key
  ``#`` — every case-lifecycle, gate, pack, vote, committee, outcome,
  condition and index event ever published, not a curated subset. Most of
  what arrives here is not actionable by the state machine (a lot of it is
  Caseflow's *own* prior output echoing back, since publishing a
  case-owned type always lands on this same topic exchange); unmapped
  types are acknowledged as a no-op, not an error.
- ``caseflow.agent-results`` <- exchange ``agent.jobs`` (direct), routing
  keys ``agent.done`` and ``agent.failed`` — the terminal event for every
  dispatched job (:mod:`fcrm_agent_worker.handler` is the producer).

There is no ``ingestion-worker``/``context-worker`` binding back to a
Caseflow-owned queue in ``definitions.json`` today — only two work queues
exist for them (``ingestion-worker.run``, ``context-worker.run``, both
consuming, neither publishing a result anywhere yet: H3/H4's own
``pyproject.toml``/module docs say their ``ingest.done``/``index.built``
publish side is not wired yet). *If* a later lane publishes those onto
``case.events`` (the design-A-documented plan, and the only topic exchange
``case.events`` routing keys ``ingest.done``/``ingest.failed``/``index.*``
suggest), ``caseflow.events``'s ``#`` binding already catches it with no
topology change on this side — but that is a forward-looking note, not a
verified fact about code that exists today.

Every :class:`~faststream.rabbit.schemas.queue.RabbitQueue` and
:class:`~faststream.rabbit.schemas.exchange.RabbitExchange` this module
builds sets ``declare=False``: ops owns the topology, Caseflow only
connects to it (``definitions.json``'s own ``_comment``).
"""

from __future__ import annotations

from typing import Final

from faststream.rabbit import ExchangeType, RabbitExchange, RabbitQueue

#: ``case.events`` fan-in: every case-lifecycle/gate/pack/vote/committee/
#: outcome/condition/index event, bound with routing key ``#``.
CASEFLOW_EVENTS_QUEUE: Final = "caseflow.events"

#: ``agent.done``/``agent.failed`` terminal job results.
CASEFLOW_AGENT_RESULTS_QUEUE: Final = "caseflow.agent-results"

#: The topic exchange every case-owned event (and Caseflow's own output)
#: flows through.
CASE_EVENTS_EXCHANGE: Final = "case.events"

#: The direct exchange ``agent.run``/``agent.done``/``agent.failed`` all
#: route through (``_JOB_EXCHANGES`` in ``fcrm_common.events`` agrees).
AGENT_JOBS_EXCHANGE: Final = "agent.jobs"

ROUTING_KEY_CASE_EVENTS: Final = "#"
ROUTING_KEY_AGENT_DONE: Final = "agent.done"
ROUTING_KEY_AGENT_FAILED: Final = "agent.failed"
ROUTING_KEY_AGENT_RUN: Final = "agent.run"

#: Every exchange's type, exactly as ``ops/rabbitmq/definitions.json``
#: declares it (mirrors ``fcrm_agent_worker.topology.EXCHANGE_TYPES``).
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

    ``routing_key`` is never sent as a bind (``declare=False``); it only
    tells FastStream which key this queue answers to, for its AsyncAPI
    metadata and for ``TestRabbitBroker``'s in-memory publish/subscribe
    matching — the real broker already bound it, by ops.
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
