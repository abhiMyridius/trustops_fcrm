"""fcrm_agent_worker (H2): the real agent-worker.

Consumes ``agent.run`` jobs from RabbitMQ (``ops/rabbitmq/definitions.json``
owns the exchange/queue topology; this package connects to it passively,
never declares it), resolves the job's agent and builds its context pack
with :mod:`fcrm_agents.context_pack`, drives the job through the same guard
chain :func:`fcrm_agents.runtime.run_agent_offline` proves offline but with
a real Bedrock model call (:mod:`fcrm_agent_worker.live_runtime`), writes an
audit record through S4's HTTP API, and publishes ``agent.done`` /
``agent.retry`` / ``agent.failed`` back onto the bus.

- :mod:`fcrm_agent_worker.settings`: environment-driven configuration.
- :mod:`fcrm_agent_worker.topology`: the exact exchange/queue/routing-key
  names this worker binds to (ops-owned; declared passively).
- :mod:`fcrm_agent_worker.publisher`: :class:`fcrm_common.events.Publisher`
  over a FastStream ``RabbitBroker``.
- :mod:`fcrm_agent_worker.audit_client`: the S4 ``POST /records`` client.
- :mod:`fcrm_agent_worker.live_runtime`: one job, one attempt, through
  guards 1-4 with a live model call.
- :mod:`fcrm_agent_worker.handler`: the :class:`fcrm_common.events.Consumer`
  subclass that ties the above together.
- :mod:`fcrm_agent_worker.app`: the FastStream app factory and health wiring.
"""

from __future__ import annotations

__version__ = "0.1.0"
