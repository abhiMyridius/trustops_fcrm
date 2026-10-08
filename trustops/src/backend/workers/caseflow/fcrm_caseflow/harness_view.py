"""The harness monitor's snapshot of a run, assembled from what H1 holds and
pushed to S1 after every step (``PUT /internal/cases/{id}/harness``).

The monitor answers "what is the harness doing for this case": which core is
running, how many of the nine agents have run, how many tokens against the case
cap (160k) and the clean-run target (120k), what each of the seven guards last
did, and a log. Nothing in it is invented: the log is the case's own audit chain
(S4), the tokens are the case's token ledger (:mod:`fcrm_caseflow.budget`:
``tokens_used`` is what its settled jobs spent, ``tokens_budget`` its cap,
``tokens_target`` the clean-run target), the other counters are the run's, the
status comes from the run's pending work and open gates, and ``chain_verified`` is
S4's own check.

:func:`harness_snapshot` is pure. :class:`HarnessReporter` reads the inputs
and sends the snapshot; it is best effort by design: a monitor that could fail a
step would put the decision path behind an observability feature, so the reporter
logs what it could not send (with the case and run ids) and lets the step stand.
S1 shows the snapshot to the analyst, the committee and the observer and never to
the owner (ADR-19).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Final, Optional, Protocol

import httpx
from fcrm_common.errors import FcrmError
from fcrm_contracts.api.audit import AuditRecord, VerifyResult
from fcrm_contracts.api.case_analyst_read import (
    MAX_HARNESS_LINES,
    GuardStatus,
    HarnessCounters,
    HarnessLine,
    HarnessPush,
    HarnessSnapshot,
)
from fcrm_contracts.enums import AgentName, CaseState
from fcrm_guardrails.budget import CASE_TOKEN_CAP, CASE_TOKEN_TARGET

from fcrm_caseflow.budget import CaseLedger
from fcrm_caseflow.store import CaseRun

logger = logging.getLogger(__name__)

#: Where the trace link points (the compose Jaeger UI).
JAEGER_UI_URL: Final = "http://localhost:16686"

TOKENS_NOTE: Final = (
    "Token counts are the agents' recorded usage, from the case's token ledger. The "
    "budget is the cap the budget guard enforces; the target is what a clean run fits "
    "in, and a case over the target but under the cap is not an error."
)

#: The seven guards: number, name and what each one stops (design B section 4).
GUARDS: Final[tuple[tuple[int, str, str], ...]] = (
    (1, "input scan", "injection patterns, personal data masked"),
    (2, "permit", "OPA: may this agent call this tool"),
    (3, "budget", "tokens, wall time, retries, fallback order"),
    (4, "output shape", "schema valid, every claim has a source"),
    (5, "rules as code", "the eight rules before any state change"),
    (6, "critic", "second model checks sources, precedents, rules, recency"),
    (7, "human gate", "a person accepted or overrode each item"),
)

#: Which guard an audit record is the trace of. Each agent job writes a
#: ``guard.permit`` record (H2), with ``allowed`` false when OPA refused it.
GUARD_OF_TYPE: Final[dict[str, int]] = {
    "guard.input": 1,
    "guard.permit": 2,
    "agent.failed": 3,
    "budget.exhausted": 3,
    "guard.output": 4,
    "gate.validation": 5,
    "critic.findings": 6,
    "gate.decided": 7,
    "human.override": 7,
    "human.handoff": 7,
}
#: Records that mean a guard stopped something.
BLOCKED_TYPES: Final = frozenset({"agent.failed", "budget.exhausted"})


def is_blocked(record: AuditRecord) -> bool:
    """Whether ``record`` says a guard stopped something: an ``agent.failed``, or
    a ``guard.permit`` that OPA refused.
    """
    if record.type in BLOCKED_TYPES:
        return True
    return record.type == "guard.permit" and record.data.get("allowed") is False


_AGENT_OF_TYPE: Final[dict[str, str]] = {
    "proposal.extraction": "intake",
    "proposal.category": "intake",
    "proposal.gaps": "intake",
    "fact.flagged": "intake",
    "screening.proposed": "screening",
    "proposal.research": "research",
    "precedent.matched": "research",
    "proposal.assessment": "assessment",
    "critic.findings": "critic",
    "completeness.report": "narrative",
    "proposal.narrative": "narrative",
    "examiner.asked": "examiner",
    "reflag.proposed": "watchlist",
}
_S1_TYPES: Final = frozenset(
    {
        "case.created",
        "case.types_changed",
        "case.submitted",
        "triage.assigned",
        "fieldset.resolved",
        "clarification.sent",
        "owner.answered",
        "owner.corrected",
        "values.reused",
        "control.planned",
        "gap.recorded",
        "reflag.acknowledged",
        "document.stored",
    }
)
_S5_PREFIXES: Final = ("pack.", "vote.", "committee.", "outcome.")
_S2_TYPES: Final = frozenset({"score.computed", "score.recomputed", "boundary.flagged"})

_CORE_OF_STATE: Final[dict[CaseState, str]] = {
    CaseState.INTAKE: "intake core",
    CaseState.REVIEW_AND_SUBMIT: "intake core",
    CaseState.DECIDE: "decision core",
    CaseState.CLOSED: "oversight core",
}


def who_of(record_type: str) -> str:
    """Which part of the system a record is the trace of, as the monitor names it
    (``assessment agent``, ``S2 scoring``, ``H1 core``...).
    """
    agent = _AGENT_OF_TYPE.get(record_type)
    if agent is not None:
        return f"{agent} agent"
    if record_type.startswith("guard."):
        return "P5 guard"
    if record_type in _S2_TYPES:
        return "S2 scoring"
    if record_type.startswith(_S5_PREFIXES):
        return "S5 decision"
    if record_type in _S1_TYPES:
        return "S1 case"
    if record_type.startswith("chain."):
        return "S4 audit"
    if record_type.startswith("eval."):
        return "X6 evals"
    return "H1 core"


def line_of(record: AuditRecord) -> HarnessLine:
    """One audit record as a monitor line: its own sequence number, who, what, and
    which guard it is the trace of.
    """
    return HarnessLine(
        seq=record.seq,
        at=record.created_at.isoformat(),
        who=who_of(record.type),
        text=record.summary,
        sub=f"#{record.seq} · {record.type}",
        level="warn" if is_blocked(record) else "info",
        guard=GUARD_OF_TYPE.get(record.type),
        progress=None,
    )


def guard_rows(records: Sequence[AuditRecord]) -> list[GuardStatus]:
    """Each guard's last act: ``pass`` with that record's summary, ``block`` when
    the last record was a refusal, ``idle`` (with what the guard does) when it has
    not acted on this case.
    """
    last: dict[int, AuditRecord] = {}
    for found in records:
        number = GUARD_OF_TYPE.get(found.type)
        if number is not None:
            last[number] = found
    rows: list[GuardStatus] = []
    for number, name, what in GUARDS:
        record = last.get(number)
        if record is None:
            rows.append(GuardStatus(n=number, name=name, last="idle", detail=what))
            continue
        blocked = is_blocked(record)
        rows.append(
            GuardStatus(
                n=number,
                name=name,
                last="block" if blocked else "pass",
                detail=record.summary,
            )
        )
    return rows


def status_of(run: CaseRun, open_gates: int) -> str:
    """``running`` while a step or an agent is pending, ``waiting_on_person`` while
    a gate is open, else ``idle``.
    """
    if run.pending_steps or run.pending_agents:
        return "running"
    if open_gates > 0 and run.state is not CaseState.CLOSED:
        return "waiting_on_person"
    return "idle"


def agents_seen(records: Sequence[AuditRecord]) -> int:
    """How many different agents have run on the case: each job writes one
    ``guard.permit`` record naming its agent. The run's own ``agents_run``
    counter is bumped by only some steps (the first live run showed 2 for a case
    whose screening, research, assessment, critic and narrative had all run), so
    the monitor counts from the chain, which has every job.
    """
    return len(
        {
            str(r.data["agent"])
            for r in records
            if r.type == "guard.permit" and r.data.get("agent")
        }
    )


def harness_snapshot(
    run: CaseRun,
    records: Sequence[AuditRecord],
    verify: Optional[VerifyResult],
    open_gates: int,
    ledger: Optional[CaseLedger] = None,
) -> HarnessSnapshot:
    """The monitor's snapshot of ``run``: counters from the run, the tokens from
    the case's token ``ledger`` (the run's own ``tokens_used`` counter only when
    no ledger is given; a case with no job yet has neither), the log and the
    guards from its audit ``records``, the chain check from ``verify`` (``None``
    when S4 could not answer: reported as not verified, never as verified).
    """
    blocked = sum(1 for r in records if is_blocked(r))
    guarded = sum(1 for r in records if r.type in GUARD_OF_TYPE)
    trace = run.trace_id or ""
    tokens_used = (
        ledger.spent if ledger is not None else run.counters.get("tokens_used", 0)
    )
    tokens_budget = ledger.cap if ledger is not None else CASE_TOKEN_CAP
    return HarnessSnapshot(
        case_id=run.snapshot.case_id,
        trace_id=trace,
        status=status_of(run, open_gates),  # type: ignore[arg-type]
        core=_CORE_OF_STATE.get(run.state, "assessment core"),  # type: ignore[arg-type]
        counters=HarnessCounters(
            agents_run=agents_seen(records),
            agents_total=len(AgentName),
            guards_passed=guarded - blocked,
            guards_blocked=blocked,
            audit_records=verify.record_count if verify is not None else len(records),
            chain_verified=bool(verify and verify.ok),
            tokens_used=tokens_used,
            tokens_budget=tokens_budget,
            tokens_target=CASE_TOKEN_TARGET,
        ),
        guards=guard_rows(records),
        log=[line_of(r) for r in records[-MAX_HARNESS_LINES:]],
        jaeger_url=f"{JAEGER_UI_URL}/trace/{trace}" if trace else JAEGER_UI_URL,
        tokens_note=TOKENS_NOTE,
    )


class RunReader(Protocol):
    """What the reporter reads of the run."""

    async def get(self, run_id: str) -> Optional[CaseRun]:
        """The run, or ``None``."""
        ...


class LedgerReader(Protocol):
    """What the reporter reads of the case's token ledger."""

    async def get(self, case_id: str) -> Optional[CaseLedger]:
        """The case's ledger, or ``None`` when no job was ever dispatched."""
        ...


class GateCounter(Protocol):
    """What the reporter asks of the gates."""

    async def count_open_for_run(self, run_id: str) -> int:
        """Open gates of the run, of every type."""
        ...


class ChainReader(Protocol):
    """What the reporter reads of the audit chain."""

    async def list_records(self, case_id: str) -> list[AuditRecord]:
        """The case's records, oldest first (bounded)."""
        ...

    async def verify(self, case_id: str) -> VerifyResult:
        """S4's check of the whole chain."""
        ...


class SnapshotSink(Protocol):
    """Where the snapshot goes (S1's ``PUT .../harness``)."""

    async def put_harness(self, case_id: str, push: HarnessPush) -> object:
        """Store ``push`` as the case's latest snapshot."""
        ...


#: What a monitor push can fail with: a peer said no or did not answer, or the
#: database did not. Anything else is a bug and propagates.
REPORT_ERRORS: Final = (FcrmError, httpx.HTTPError)


class HarnessReporter:
    """Assembles and sends the monitor's snapshot after a step.

    Args:
        runs: Reads the run.
        gates: Counts the run's open gates.
        chain: Reads the audit chain and its verification.
        sink: S1's harness route.
        budgets: Reads the case's token ledger. Without it the monitor falls
            back to the run's ``tokens_used`` counter.
    """

    def __init__(
        self,
        runs: RunReader,
        gates: GateCounter,
        chain: ChainReader,
        sink: SnapshotSink,
        budgets: Optional[LedgerReader] = None,
    ) -> None:
        self._runs = runs
        self._gates = gates
        self._chain = chain
        self._sink = sink
        self._budgets = budgets

    async def report(self, case_id: str, run_id: str) -> bool:
        """Send the current snapshot of ``run_id``. Returns whether S1 took it.

        A failure to read or send is logged with the case and run ids and
        reported as ``False``; it never reaches the step that triggered it (the
        decision path does not wait on the monitor).
        """
        try:
            run = await self._runs.get(run_id)
            if run is None:
                logger.warning(
                    "no run to report", extra={"case_id": case_id, "run_id": run_id}
                )
                return False
            records = await self._chain.list_records(case_id)
            verify = await self._verified(case_id)
            ledger = (
                await self._budgets.get(case_id) if self._budgets is not None else None
            )
            snapshot = harness_snapshot(
                run,
                records,
                verify,
                await self._gates.count_open_for_run(run_id),
                ledger,
            )
            await self._sink.put_harness(
                case_id, HarnessPush(run_id=run_id, snapshot=snapshot)
            )
        except REPORT_ERRORS as exc:
            logger.warning(
                "harness snapshot not sent; the step stands",
                exc_info=exc,
                extra={"case_id": case_id, "run_id": run_id},
            )
            return False
        return True

    async def _verified(self, case_id: str) -> Optional[VerifyResult]:
        """S4's chain check, or ``None`` when S4 cannot answer (so the monitor says
        "not verified", never a verified it did not see).
        """
        try:
            return await self._chain.verify(case_id)
        except REPORT_ERRORS as exc:
            logger.warning(
                "chain check unavailable for the monitor",
                exc_info=exc,
                extra={"case_id": case_id},
            )
            return None
