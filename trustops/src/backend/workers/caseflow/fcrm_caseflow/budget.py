"""The per-case token ledger (ADR-14, ADR-27): one budget for every agent job of
a case, kept by H1.

A case may spend ``CASE_TOKEN_CAP`` tokens (250,000, ``ai/models/tiers.yaml``
``per_case_budget_tokens``) across all of its agent jobs. H2's Guard 3 keeps
limiting one job (its attempts, its wall clock, its tier's tokens); this ledger
is the other half: no job is published unless the case can pay for one attempt of
it, and what a job really spent is added when its result arrives.

**Reserve.** Before an ``agent.run`` goes to the outbox, H1 reserves ONE
attempt of the job's tier budget (``TIER_BUDGETS[tier].tokens``) in the same
Mongo transaction. It dispatches only if ``spent + reserved + asked <= cap``.
The retry is not reserved up front: ``tokens x (1 + retries)`` per job would
reserve 96k for a research and assessment pair and block the critic that
follows, though the pair really spends about 30k. A retry only matters when the
result arrives.

**Settle.** ``agent.done`` and ``agent.failed`` both carry the whole job's
usage, refused attempts included. Settling drops the job's reservation and
adds what it used, once per job id. A job may overrun its reservation (its
retry really spent the tokens), so ``spent`` may pass ``cap``; the overrun is
logged and the next reservation is refused.

**One ledger per case**, not per run: a re-run after the clarification round
spends from the same ledger. The document is ``case_budgets/<case_id>``::

    {_id: case_id, case_id, run_id, cap, spent, reserved,
     jobs: {job_id: {agent, reserved, spent, status}}, last_refusal?, updated_at}

Every method takes the caller's transaction ``session`` and never opens one.
Read-modify-write inside that snapshot transaction is atomic: a concurrent
writer of the same document makes the later commit a ``WriteConflict``, which
:func:`fcrm_common.mongo.with_transaction` retries against the new state, so two
dispatches can never both take the last tokens.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final, Optional

from fcrm_common.mongo import Document
from fcrm_common.time import utcnow
from fcrm_contracts.enums import AgentName
from fcrm_guardrails.budget import CASE_TOKEN_CAP, TIER_BUDGETS
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.collection import AsyncCollection

from fcrm_caseflow.dispatch import AGENT_TIERS

logger = logging.getLogger(__name__)

CASE_BUDGETS_COLLECTION: Final = "case_budgets"

#: The oversight agents. They run on demand, over a closed case (the examiner),
#: over the jurisdiction list (the watchlist) or beside the analyst's review (the
#: policy-gap writer), never as a step of the case's own chain, and a closed run
#: has no handoff to refuse into. They are not reserved or refused, but what they
#: spend is settled into the ledger like any job's, so the monitor shows it.
UNMETERED_AGENTS: Final = frozenset(
    {AgentName.EXAMINER, AgentName.WATCHLIST, AgentName.POLICY_GAP}
)

#: ``usage`` keys that count against the cap: cache reads and writes are billed
#: apart and are not part of the 120k (``evals/README.md``).
_CAPPED_USAGE_KEYS: Final = ("input_tokens", "output_tokens")


class JobStatus(StrEnum):
    """Where one job stands in the ledger."""

    RESERVED = "reserved"
    SETTLED = "settled"


@dataclass(frozen=True)
class JobEntry:
    """One job's line in the ledger.

    Attributes:
        agent: The agent's wire name.
        reserved: Tokens held for it now (0 once settled).
        spent: Tokens it has really used, summed over every time it settled.
        status: ``reserved`` until its result arrives, then ``settled``.
    """

    agent: str
    reserved: int
    spent: int
    status: JobStatus


@dataclass(frozen=True)
class CaseLedger:
    """A case's ledger as stored.

    Attributes:
        case_id: The case (the document id).
        run_id: The run of the latest reservation (informational: the ledger
            outlives a run's re-runs).
        cap: Tokens the case may spend.
        spent: Tokens used by settled jobs.
        reserved: Tokens held by jobs still running.
        jobs: Each job's line, by job id.
    """

    case_id: str
    run_id: str
    cap: int
    spent: int
    reserved: int
    jobs: Mapping[str, JobEntry] = field(default_factory=dict)

    @property
    def headroom(self) -> int:
        """Tokens neither spent nor held; negative after an overrun."""
        return self.cap - self.spent - self.reserved


@dataclass(frozen=True)
class JobRequest:
    """One job H1 wants to publish and the tokens to hold for it."""

    job_id: str
    agent: AgentName
    tokens: int


@dataclass(frozen=True)
class ReserveOutcome:
    """The answer to a reservation.

    Attributes:
        granted: Whether every job may be published.
        ledger: The ledger after the call (unchanged when refused).
        asked: The tokens this call tried to add: only jobs not already held.
    """

    granted: bool
    ledger: CaseLedger
    asked: int


@dataclass(frozen=True)
class SettleOutcome:
    """The answer to a settlement.

    Attributes:
        applied: ``False`` when the job had already settled (a redelivery).
        ledger: The ledger after the call.
        overran: Whether the job used more than it had reserved.
    """

    applied: bool
    ledger: CaseLedger
    overran: bool = False


@dataclass(frozen=True)
class BudgetRefusal:
    """A dispatch the cap refused: what the audit record and the handoff say.

    Attributes:
        jobs: The jobs that were not published, first one first.
        asked: The tokens the dispatch needed.
        ledger: The ledger that said no.
    """

    jobs: tuple[JobRequest, ...]
    asked: int
    ledger: CaseLedger

    @property
    def first(self) -> JobRequest:
        """The job the handoff names."""
        return self.jobs[0]


def reservation_tokens(agent: AgentName) -> int:
    """One attempt of ``agent``'s tier budget: what a dispatch holds."""
    return TIER_BUDGETS[AGENT_TIERS[agent]].tokens


def usage_tokens(usage: Mapping[str, Any]) -> int:
    """The tokens of a result's ``usage`` that count against the cap: input plus
    output. A missing key, or a value that is not an integer, counts as zero.
    """
    total = 0
    for key in _CAPPED_USAGE_KEYS:
        value = usage.get(key, 0)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            total += value
    return total


def _entry_to_document(entry: JobEntry) -> dict[str, Any]:
    return {
        "agent": entry.agent,
        "reserved": entry.reserved,
        "spent": entry.spent,
        "status": entry.status.value,
    }


def ledger_from_document(doc: Document) -> CaseLedger:
    """Validate a stored ``case_budgets`` document back into :class:`CaseLedger`."""
    jobs = {
        str(job_id): JobEntry(
            agent=str(raw.get("agent", "")),
            reserved=int(raw.get("reserved", 0)),
            spent=int(raw.get("spent", 0)),
            status=JobStatus(raw.get("status", JobStatus.SETTLED.value)),
        )
        for job_id, raw in (doc.get("jobs") or {}).items()
    }
    return CaseLedger(
        case_id=str(doc["_id"]),
        run_id=str(doc.get("run_id", "")),
        cap=int(doc["cap"]),
        spent=int(doc.get("spent", 0)),
        reserved=int(doc.get("reserved", 0)),
        jobs=jobs,
    )


def _check_job_id(job_id: str) -> str:
    """A job id that is safe inside a ``jobs.<id>`` ``$set`` path.

    Raises:
        ValueError: ``job_id`` is empty, holds ``.`` or starts with ``$``.
    """
    if not job_id or "." in job_id or job_id.startswith("$"):
        raise ValueError(f"unusable job id {job_id!r} for the case ledger")
    return job_id


class CaseBudgetStore:
    """Typed access to ``case_budgets``: reserve, settle, read.

    Args:
        collection: ``case_budgets`` in ``fcrm_harness``.
        cap: The cap a ledger takes when it is first created
            (``CASE_TOKEN_CAP``); an existing ledger keeps its own.
    """

    def __init__(
        self,
        collection: AsyncCollection[Document],
        *,
        cap: int = CASE_TOKEN_CAP,
    ) -> None:
        self._collection = collection
        self._cap = cap

    async def get(
        self, case_id: str, *, session: Optional[AsyncClientSession] = None
    ) -> Optional[CaseLedger]:
        """The case's ledger, or ``None`` when no job was ever dispatched."""
        doc = await self._collection.find_one({"_id": case_id}, session=session)
        return ledger_from_document(doc) if doc is not None else None

    async def reserve(
        self,
        case_id: str,
        run_id: str,
        jobs: Sequence[JobRequest],
        *,
        session: AsyncClientSession,
    ) -> ReserveOutcome:
        """Hold the tokens of every job in ``jobs`` or none of them.

        All or nothing: a research and assessment pair that cannot both be paid
        for is refused as a pair, because one half running alone would leave the
        join waiting for a job that was never published. A job that is already
        held (a re-delivered dispatch) adds nothing. A job id that has settled
        before (a re-dispatch of the same job) is held again and keeps what it
        spent.

        Raises:
            ValueError: a job id cannot be a field name.
        """
        ledger = await self._ensure(case_id, run_id, session)
        fresh = [
            job
            for job in jobs
            if (held := ledger.jobs.get(_check_job_id(job.job_id))) is None
            or held.status is not JobStatus.RESERVED
        ]
        asked = sum(job.tokens for job in fresh)
        if not fresh:
            return ReserveOutcome(True, ledger, 0)
        if ledger.spent + ledger.reserved + asked > ledger.cap:
            return ReserveOutcome(False, ledger, asked)
        entries = {
            job.job_id: JobEntry(
                agent=job.agent.value,
                reserved=job.tokens,
                spent=ledger.jobs[job.job_id].spent if job.job_id in ledger.jobs else 0,
                status=JobStatus.RESERVED,
            )
            for job in fresh
        }
        await self._collection.update_one(
            {"_id": case_id},
            {
                "$inc": {"reserved": asked},
                "$set": {
                    "run_id": run_id,
                    "updated_at": utcnow(),
                    **{f"jobs.{k}": _entry_to_document(v) for k, v in entries.items()},
                },
            },
            session=session,
        )
        after = dataclasses.replace(
            ledger,
            run_id=run_id,
            reserved=ledger.reserved + asked,
            jobs={**ledger.jobs, **entries},
        )
        return ReserveOutcome(True, after, asked)

    async def settle(
        self,
        case_id: str,
        run_id: str,
        job_id: str,
        agent: AgentName,
        tokens: int,
        *,
        session: AsyncClientSession,
    ) -> SettleOutcome:
        """Drop ``job_id``'s reservation and add the ``tokens`` it really used,
        once per job: a job that is already settled changes nothing.

        A job the ledger never saw (dispatched before the ledger existed, or an
        unmetered oversight agent) is recorded with nothing reserved.

        Raises:
            ValueError: ``tokens`` is negative, or ``job_id`` cannot be a field
                name.
        """
        if tokens < 0:
            raise ValueError(f"settled tokens must be >= 0, got {tokens}")
        ledger = await self._ensure(case_id, run_id, session)
        entry = ledger.jobs.get(_check_job_id(job_id))
        if entry is not None and entry.status is JobStatus.SETTLED:
            return SettleOutcome(False, ledger)
        held = entry.reserved if entry is not None else 0
        settled = JobEntry(
            agent=agent.value,
            reserved=0,
            spent=(entry.spent if entry is not None else 0) + tokens,
            status=JobStatus.SETTLED,
        )
        await self._collection.update_one(
            {"_id": case_id},
            {
                "$inc": {"reserved": -held, "spent": tokens},
                "$set": {
                    f"jobs.{job_id}": _entry_to_document(settled),
                    "updated_at": utcnow(),
                },
            },
            session=session,
        )
        after = dataclasses.replace(
            ledger,
            reserved=ledger.reserved - held,
            spent=ledger.spent + tokens,
            jobs={**ledger.jobs, job_id: settled},
        )
        overran = held > 0 and tokens > held
        if overran:
            logger.warning(
                "agent job overran its reservation",
                extra={
                    "case_id": case_id,
                    "job_id": job_id,
                    "reserved": held,
                    "actual": tokens,
                },
            )
        return SettleOutcome(True, after, overran)

    async def record_refusal(
        self,
        case_id: str,
        refusal: BudgetRefusal,
        *,
        session: AsyncClientSession,
    ) -> None:
        """Keep the latest refusal on the ledger itself, so that it survives an
        audit service that was down when the record was written.
        """
        ledger = refusal.ledger
        await self._collection.update_one(
            {"_id": case_id},
            {
                "$set": {
                    "last_refusal": {
                        "at": utcnow(),
                        "agents": [job.agent.value for job in refusal.jobs],
                        "asked": refusal.asked,
                        "spent": ledger.spent,
                        "reserved": ledger.reserved,
                        "cap": ledger.cap,
                    }
                }
            },
            session=session,
        )

    async def _ensure(
        self, case_id: str, run_id: str, session: AsyncClientSession
    ) -> CaseLedger:
        """The case's ledger, created empty at the configured cap if the case
        has none yet.
        """
        doc = await self._collection.find_one({"_id": case_id}, session=session)
        if doc is None:
            now = utcnow()
            await self._collection.update_one(
                {"_id": case_id},
                {
                    "$setOnInsert": {
                        "case_id": case_id,
                        "run_id": run_id,
                        "cap": self._cap,
                        "spent": 0,
                        "reserved": 0,
                        "jobs": {},
                        "created_at": now,
                        "updated_at": now,
                    }
                },
                upsert=True,
                session=session,
            )
            doc = await self._collection.find_one({"_id": case_id}, session=session)
        if doc is None:
            raise RuntimeError(f"case ledger {case_id} vanished inside its transaction")
        return ledger_from_document(doc)
