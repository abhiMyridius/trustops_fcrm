"""The exact RabbitMQ topology Caseflow binds to, read off
``ops/rabbitmq/definitions.json`` by hand (not generated: that file is
ops-owned; this module is the one place its names are repeated for H1,
the same convention :mod:`fcrm_agent_worker.topology` establishes for H2).

Design A §4.1's topology table gives Caseflow three consumers ("case
events, ``agent.done``/``agent.failed``, ``ingest.done``/``index.built``").
The real ``definitions.json`` binds three queues to Caseflow, but not those
three: ``ingest.done``/``index.built`` arrive on the ``#`` binding of
``caseflow.events``, and the third queue is ``caseflow.steps`` (ADR-28):

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
- ``caseflow.steps`` <- exchange ``case.events`` (topic), routing key
  ``harness.step`` — H1's own HTTP-side work (:mod:`fcrm_caseflow.steps`),
  published by H1's outbox relay and consumed by H1. Because ``caseflow
  .events`` is bound with ``#``, every ``harness.step`` is delivered to both
  queues; the events consumer ignores it.

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

The passive queue and exchange helpers are shared
(:mod:`fcrm_common.broker`): they set ``declare=False`` because ops owns
the topology and Caseflow only connects to it (``definitions.json``'s own
``_comment``).
"""

from __future__ import annotations

from typing import Final

from fcrm_common.broker import EXCHANGE_TYPES, passive_exchange, passive_queue

#: ``case.events`` fan-in: every case-lifecycle/gate/pack/vote/committee/
#: outcome/condition/index event, bound with routing key ``#``.
CASEFLOW_EVENTS_QUEUE: Final = "caseflow.events"

#: ``agent.done``/``agent.failed`` terminal job results.
CASEFLOW_AGENT_RESULTS_QUEUE: Final = "caseflow.agent-results"

#: ``harness.step`` (ADR-28): H1's own HTTP-side work, enqueued through the
#: outbox in the transaction of the transition that needs it and run outside
#: any transaction. Bound to ``case.events`` with routing key
#: ``harness.step``; the ``#`` binding on :data:`CASEFLOW_EVENTS_QUEUE`
#: delivers a copy there too, which
#: :class:`~fcrm_caseflow.consumers.CaseEventsConsumer` ignores.
CASEFLOW_STEPS_QUEUE: Final = "caseflow.steps"

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

#: The ``harness.step`` event type, which is also its routing key on
#: ``case.events`` (an outbox row's routing key defaults to its type).
HARNESS_STEP_EVENT: Final = "harness.step"
ROUTING_KEY_HARNESS_STEP: Final = HARNESS_STEP_EVENT

__all__ = [
    "AGENT_JOBS_EXCHANGE",
    "CASEFLOW_AGENT_RESULTS_QUEUE",
    "CASEFLOW_EVENTS_QUEUE",
    "CASEFLOW_STEPS_QUEUE",
    "CASE_EVENTS_EXCHANGE",
    "EXCHANGE_TYPES",
    "HARNESS_STEP_EVENT",
    "ROUTING_KEY_AGENT_DONE",
    "ROUTING_KEY_AGENT_FAILED",
    "ROUTING_KEY_AGENT_RUN",
    "ROUTING_KEY_CASE_EVENTS",
    "ROUTING_KEY_HARNESS_STEP",
    "passive_exchange",
    "passive_queue",
]
