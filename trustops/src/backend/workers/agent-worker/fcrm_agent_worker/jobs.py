"""H2's job ledger: who is running which ``agent.run`` job, and which are done
(ADR-79, mirroring H1's ADR-28 step leases).

The model call runs **outside** any Mongo transaction: a transaction lives at
most 60 s by default, a strong-tier job can take minutes, and a transaction
that aborts mid-call re-runs the whole job (and pays Bedrock again). Instead
each job is *claimed* in the ``agent_jobs`` collection before it runs, under a
lease long enough for the job's worst case, and *marked done* in the same short
transaction that writes its terminal event to H2's outbox
(:meth:`fcrm_agent_worker.handler.AgentJobConsumer._finish`).

A document per job::

    {_id: job_id, case_id, run_id, agent, status: "running" | "done",
     holder, claim, started_at, lease_until, finished_at, outcome, event_id}

* ``claim`` takes the job when it has no document yet, or its document is
  ``running`` with an expired lease. A live ``running`` job is reported as
  :attr:`ClaimOutcome.RUNNING` (the handler raises :class:`JobInFlightError`,
  so the delivery takes the broker's retry and DLQ path) and a ``done`` job as
  :attr:`ClaimOutcome.DONE` (the delivery is acknowledged with nothing sent:
  its terminal event is already in the outbox).
* Like :meth:`fcrm_common.mongo.MongoLease.acquire`, the take is one atomic
  upsert whose pipeline compares against the *server's* clock (``$$NOW``), so
  replicas with skewed clocks cannot both believe they hold a job.
* Each successful claim writes a fresh ``claim`` token, kept in this process
  only. :meth:`AgentJobStore.renew`, :meth:`AgentJobStore.mark_done` and
  :meth:`AgentJobStore.release` match on it, so a worker whose lease expired
  and was taken over can neither extend nor finish a job someone else now
  holds. A second delivery of a job this same process is still running is
  ``RUNNING``, never a second claim (``holder`` names the process for anyone
  reading the collection; it is not what decides ownership).
* ``finished_at`` carries a 7-day TTL, the same window as the consumer's
  dedupe markers, after which a redelivered job would run again.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from enum import StrEnum
from typing import Any, Final, Optional

from fcrm_common.ids import new_id
from fcrm_common.mongo import Document
from fcrm_contracts.enums import Tier
from fcrm_guardrails.budget import TIER_BUDGETS
from pymongo import ASCENDING, IndexModel, ReturnDocument
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.errors import DuplicateKeyError

logger = logging.getLogger(__name__)

#: The job ledger's collection (H2's database).
AGENT_JOBS_COLLECTION: Final = "agent_jobs"
#: H2's own outbox: ``agent.done`` / ``agent.failed`` rows its relay publishes.
AGENT_OUTBOX_COLLECTION: Final = "agent_outbox"
#: The collection H2's relay lease lives in (shared with H1's, distinct name).
LEASES_COLLECTION: Final = "leases"
#: H2's relay leadership lease. H1's is ``outbox-relay:caseflow``; the two run
#: side by side in the harness process.
OUTBOX_RELAY_LEASE_NAME: Final = "outbox-relay:agent-worker"

STATUS_RUNNING: Final = "running"
STATUS_DONE: Final = "done"

#: How long a finished job's document is kept (the dedupe window).
FINISHED_TTL: Final = timedelta(days=7)
#: Slack on top of the job's worst case before its lease may be taken over.
LEASE_SLACK_S: Final = 60


class ClaimOutcome(StrEnum):
    """What :meth:`AgentJobStore.claim` found."""

    CLAIMED = "claimed"
    """This call took the job: run it."""
    RUNNING = "running"
    """Another claim holds a live lease: do not run it now."""
    DONE = "done"
    """The job finished; its terminal event is already in the outbox."""


class JobInFlightError(RuntimeError):
    """A delivery arrived for a job another claim is still running.

    Raised from the handler so the base consumer republishes the message to
    the retry exchange (and, after its attempts, the DLQ) instead of running
    the model a second time.
    """

    def __init__(self, job_id: str) -> None:
        super().__init__(f"agent job {job_id} is already running")
        self.job_id = job_id


def lease_seconds(tier: Tier, parts: int = 1) -> int:
    """The lease a job on ``tier`` split into ``parts`` needs: every attempt of
    every part, plus one more part's worth for the reduce call, at the tier's
    wall-clock bound, plus :data:`LEASE_SLACK_S`.

    ``wall_s × max_attempts × (parts + 1) + 60`` (plan A7): 780 s for a
    one-part strong job, 1,860 s at four parts.
    """
    budget = TIER_BUDGETS[tier]
    return budget.wall_s * budget.max_attempts * (max(parts, 1) + 1) + LEASE_SLACK_S


def _literal(value: Any) -> dict[str, Any]:
    """``value`` as an aggregation literal (a string starting with ``$`` would
    otherwise be read as a field path)."""
    return {"$literal": value}


class AgentJobStore:
    """Claims, renews and finishes H2 jobs in the ``agent_jobs`` collection.

    Args:
        collection: The ``agent_jobs`` collection, in the same database as
            H2's outbox so :meth:`mark_done` and the outbox row commit together.
        holder: This process's identity (``new_id("h2")``), recorded on the
            jobs it claims.
    """

    def __init__(self, collection: AsyncCollection[Document], holder: str) -> None:
        self._collection = collection
        self.holder = holder
        self._claims: dict[str, str] = {}

    @property
    def collection(self) -> AsyncCollection[Document]:
        """The ``agent_jobs`` collection."""
        return self._collection

    async def ensure_indexes(self) -> list[str]:
        """The ``(status, lease_until)`` scan index and the 7-day TTL on
        ``finished_at`` (a running job has no ``finished_at`` and never
        expires).
        """
        return await self._collection.create_indexes(
            [
                IndexModel(
                    [("status", ASCENDING), ("lease_until", ASCENDING)],
                    name="status_lease_until",
                ),
                IndexModel(
                    [("finished_at", ASCENDING)],
                    expireAfterSeconds=int(FINISHED_TTL.total_seconds()),
                    name="finished_at_ttl",
                ),
            ]
        )

    async def claim(
        self,
        job_id: str,
        ttl_s: float,
        *,
        case_id: str = "",
        run_id: str = "",
        agent: str = "",
    ) -> ClaimOutcome:
        """Take ``job_id`` for ``ttl_s`` seconds if nobody holds it.

        One atomic upsert keyed on ``_id`` alone (``$expr`` is not allowed in
        an upsert's query, see :meth:`fcrm_common.mongo.MongoLease.acquire`):
        the pipeline takes the job when it is not ``done`` and its lease has
        expired or never existed (a missing ``lease_until`` compares below any
        date), and otherwise writes the current values back unchanged.

        Args:
            job_id: The job (``JobPayload.job_id``).
            ttl_s: Lease lifetime (:func:`lease_seconds`).
            case_id: Recorded on the document, for anyone reading it.
            run_id: Recorded on the document.
            agent: Recorded on the document.

        Returns:
            :attr:`ClaimOutcome.CLAIMED` when this call took the job,
            :attr:`ClaimOutcome.DONE` when it has finished, otherwise
            :attr:`ClaimOutcome.RUNNING`.
        """
        token = new_id("claim")
        take = {
            "$and": [
                {"$ne": ["$status", STATUS_DONE]},
                {"$lt": ["$lease_until", "$$NOW"]},
            ]
        }

        def pick(new: Any, field: str) -> dict[str, Any]:
            return {"$cond": [take, new, f"${field}"]}

        try:
            doc = await self._collection.find_one_and_update(
                {"_id": job_id},
                [
                    {
                        "$set": {
                            "status": pick(_literal(STATUS_RUNNING), "status"),
                            "holder": pick(_literal(self.holder), "holder"),
                            "claim": pick(_literal(token), "claim"),
                            "started_at": pick("$$NOW", "started_at"),
                            "lease_until": pick(
                                {"$add": ["$$NOW", int(ttl_s * 1000)]}, "lease_until"
                            ),
                            "case_id": pick(_literal(case_id), "case_id"),
                            "run_id": pick(_literal(run_id), "run_id"),
                            "agent": pick(_literal(agent), "agent"),
                        }
                    }
                ],
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError:
            # Two first-ever upserts raced; the other one created the job.
            return ClaimOutcome.RUNNING
        if doc is None:
            return ClaimOutcome.RUNNING
        if doc.get("status") == STATUS_DONE:
            return ClaimOutcome.DONE
        if doc.get("claim") == token:
            self._claims[job_id] = token
            return ClaimOutcome.CLAIMED
        return ClaimOutcome.RUNNING

    def _mine(self, job_id: str) -> Optional[dict[str, Any]]:
        """The filter matching ``job_id`` while this process's claim still
        holds it, or ``None`` when this process holds no claim on it."""
        token = self._claims.get(job_id)
        if token is None:
            return None
        return {"_id": job_id, "claim": token, "status": STATUS_RUNNING}

    async def renew(self, job_id: str, ttl_s: float) -> bool:
        """Extend this process's lease on ``job_id`` to ``ttl_s`` from now.

        Returns:
            Whether the lease was still ours (``False``: it expired and
            another worker took the job, or this process never claimed it).
        """
        query = self._mine(job_id)
        if query is None:
            return False
        result = await self._collection.update_one(
            query,
            [{"$set": {"lease_until": {"$add": ["$$NOW", int(ttl_s * 1000)]}}}],
        )
        return result.modified_count == 1

    async def mark_done(
        self,
        job_id: str,
        *,
        outcome: str,
        event_id: str,
        session: AsyncClientSession,
    ) -> bool:
        """Mark ``job_id`` done inside ``session``'s transaction.

        Called from the transaction that also enqueues the terminal event, so
        the two commit together. Safe to run again when the transaction is
        retried: a rolled-back first try leaves the claim in place, and a
        second call after a commit finds the job ``done`` and changes nothing.

        Args:
            job_id: The job.
            outcome: The job's metrics outcome (``pass``, ``blocked``...).
            event_id: The terminal event's ``event_id``.
            session: The caller's session, inside its transaction.

        Returns:
            Whether this call finished the job (``False``: another worker took
            it over, or it is already done; enqueue nothing).
        """
        query = self._mine(job_id)
        if query is None:
            return False
        result = await self._collection.update_one(
            query,
            [
                {
                    "$set": {
                        "status": _literal(STATUS_DONE),
                        "finished_at": "$$NOW",
                        "outcome": _literal(outcome),
                        "event_id": _literal(event_id),
                    }
                }
            ],
            session=session,
        )
        return result.modified_count == 1

    async def release(self, job_id: str) -> None:
        """Give up a claim this process cannot finish (the job raised before
        its terminal event committed), so the broker's retry can claim it at
        once instead of waiting out the lease. A job already ``done`` is left
        alone.
        """
        query = self._mine(job_id)
        self.forget(job_id)
        if query is None:
            return
        await self._collection.delete_one(query)

    def forget(self, job_id: str) -> None:
        """Drop this process's claim token for ``job_id`` (the job is over)."""
        self._claims.pop(job_id, None)
