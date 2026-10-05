"""The composite ``harness`` process (ADR-26): the FastStream consumers.

``python -m fcrm_harness`` runs H1 caseflow, H2 agent-worker, H3 ingestion and
H4 context as :class:`~fcrm_common.worker.WorkerUnit` instances on one broker. Each unit
stays a self-contained package with its own ``build_unit``; this package
owns the broker, the Mongo client, the publisher and the health endpoint.
"""

__version__ = "0.1.0"
