"""The exact RabbitMQ topology H4 binds to, read off
``ops/rabbitmq/definitions.json`` by hand (not generated: that file is
ops-owned and this module is the one place its names are repeated for H4, the
convention :mod:`fcrm_ingestion_worker.topology` and
:mod:`fcrm_caseflow.topology` set).

``index.build`` arrives on the direct exchange ``context.jobs``, bound to the
queue ``context-worker.run`` (ops declares its retry and dead-letter queues).
``index.built`` and ``index.failed`` leave on the topic exchange ``case.events``,
where S3's ``index.*`` binding on ``policy-service.events`` (and S1's
``index.built`` binding on ``case-service.events``) receive them. Nothing is
declared here: per that file's own ``_comment``, "clients must declare these
queues passively: ops owns the topology".
"""

from __future__ import annotations

from typing import Final

from fcrm_common.broker import EXCHANGE_TYPES, passive_exchange, passive_queue

#: ``index.build`` lands here (exchange ``context.jobs``, key ``index.build``).
CONTEXT_RUN_QUEUE: Final = "context-worker.run"

#: The direct exchange S3 publishes ``index.build`` to (``_JOB_EXCHANGES`` in
#: ``fcrm_common.events`` agrees).
CONTEXT_JOBS_EXCHANGE: Final = "context.jobs"

#: The topic exchange H4's results are published to.
CASE_EVENTS_EXCHANGE: Final = "case.events"

ROUTING_KEY_INDEX_BUILD: Final = "index.build"
ROUTING_KEY_INDEX_BUILT: Final = "index.built"
ROUTING_KEY_INDEX_FAILED: Final = "index.failed"

__all__ = [
    "CASE_EVENTS_EXCHANGE",
    "CONTEXT_JOBS_EXCHANGE",
    "CONTEXT_RUN_QUEUE",
    "EXCHANGE_TYPES",
    "ROUTING_KEY_INDEX_BUILD",
    "ROUTING_KEY_INDEX_BUILT",
    "ROUTING_KEY_INDEX_FAILED",
    "passive_exchange",
    "passive_queue",
]
