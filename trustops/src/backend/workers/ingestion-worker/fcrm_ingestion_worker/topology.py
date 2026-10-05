"""The exact RabbitMQ topology H3 binds to, read off
``ops/rabbitmq/definitions.json`` by hand (not generated: that file is
ops-owned and this module is the one place its names are repeated for H3, the
convention :mod:`fcrm_agent_worker.topology` and :mod:`fcrm_caseflow.topology`
set).

``ingest.run`` arrives on the direct exchange ``ingestion.jobs``, bound to the
queue ``ingestion-worker.run`` (ops declares its retry and dead-letter queues).
``ingest.done`` and ``ingest.failed`` leave on the topic exchange
``case.events``, where Caseflow's ``#`` binding on ``caseflow.events`` receives
them. Nothing is declared here: per that file's own ``_comment``, "clients must
declare these queues passively: ops owns the topology".
"""

from __future__ import annotations

from typing import Final

from fcrm_common.broker import EXCHANGE_TYPES, passive_exchange, passive_queue

#: ``ingest.run`` lands here (exchange ``ingestion.jobs``, key ``ingest.run``).
INGESTION_RUN_QUEUE: Final = "ingestion-worker.run"

#: The direct exchange H1 publishes ``ingest.run`` to (``_JOB_EXCHANGES`` in
#: ``fcrm_common.events`` agrees).
INGESTION_JOBS_EXCHANGE: Final = "ingestion.jobs"

#: The topic exchange H3's results are published to.
CASE_EVENTS_EXCHANGE: Final = "case.events"

ROUTING_KEY_INGEST_RUN: Final = "ingest.run"
ROUTING_KEY_INGEST_DONE: Final = "ingest.done"
ROUTING_KEY_INGEST_FAILED: Final = "ingest.failed"

__all__ = [
    "CASE_EVENTS_EXCHANGE",
    "EXCHANGE_TYPES",
    "INGESTION_JOBS_EXCHANGE",
    "INGESTION_RUN_QUEUE",
    "ROUTING_KEY_INGEST_DONE",
    "ROUTING_KEY_INGEST_FAILED",
    "ROUTING_KEY_INGEST_RUN",
    "passive_exchange",
    "passive_queue",
]
