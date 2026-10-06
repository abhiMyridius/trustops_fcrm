"""The analyst's handoff, as plain data and pure rules (flow F-path).

A ``handoff`` gate is opened by Caseflow for each job that failed after its retry;
S1 learns of it from ``gate.opened`` and keeps a row (:class:`HandoffGateRow`) so
the analyst can see what is waiting and S1 can refuse a decision on a gate it does
not know. Completing one is one :class:`~fcrm_case.domain.analyst_commands
.AnalystCommand`: the human output and the decision are stored (``handoff_outputs``,
the ledger), and ``gate.decided handoff`` carries the output to Caseflow in the
same transaction (**store before emit**).

Pure, no I/O.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final, Optional

from fcrm_contracts.api.case_analyst_handoff import (
    HandoffCompleteBody,
    HandoffGateView,
)
from fcrm_contracts.api.case_internal import UiActor, UiEventIn
from fcrm_contracts.enums import GateType
from fcrm_contracts.events.payloads import GateOpened
from pydantic import BaseModel, ConfigDict

from fcrm_case.domain.analyst_commands import (
    AnalystCommand,
    AnalystGate,
    OnceRecord,
    activity_row,
    decision_fingerprint,
    gate_envelope,
)

#: The collection the human-supplied outputs are stored in, one row per gate.
OUTPUTS_COLLECTION: Final = "handoff_outputs"

#: The decision string Caseflow's ``handoff`` transition reads.
COMPLETE_DECISION: Final = "complete"


class HandoffGateRow(BaseModel):
    """A ``handoff`` gate S1 was told about (``_id`` is the gate id).

    Attributes:
        gate_id: The id Caseflow opened it with (deterministic from the run and
            the failed job).
        case_id: The case.
        run_id: The run the gate belongs to (Guard 7 matches it).
        agent: The agent whose job failed.
        job_id: The failed job.
        step: The stage the run was suspended from, when known.
        reason: The failure H2 reported.
        attempt: How many model calls the job made before it failed.
        raw_inputs_ref: A reference to what the agent was given, when recorded.
        raw_inputs: What the agent was given (the context pack as the model
            received it), cut to a bound; ``None`` when none was kept.
        raw_inputs_sha256: The digest of the stored text, which the audit
            record also carries.
        raw_inputs_bytes: The size of the stored text before any cut.
        raw_inputs_truncated: The text above was cut.
        raw_inputs_error: Why there are no inputs, when that is known.
        opened_at: When S1 learned of the gate.
    """

    model_config = ConfigDict(extra="forbid")

    gate_id: str
    case_id: str
    run_id: str
    agent: str
    job_id: str
    step: Optional[str] = None
    reason: str
    attempt: int
    raw_inputs_ref: Optional[str] = None
    raw_inputs: Optional[str] = None
    raw_inputs_sha256: Optional[str] = None
    raw_inputs_bytes: Optional[int] = None
    raw_inputs_truncated: bool = False
    raw_inputs_error: Optional[str] = None
    opened_at: datetime


def gate_row(opened: GateOpened, now: datetime) -> Optional[HandoffGateRow]:
    """The row for a ``gate.opened`` of a handoff gate, or ``None`` when the gate is
    another kind, or names no failed job (a gate opened before its subject did
    cannot be completed, so S1 does not offer it).
    """
    if opened.gate_type is not GateType.HANDOFF:
        return None
    subject = opened.subject
    if "agent" not in subject or "job_id" not in subject:
        return None
    return HandoffGateRow(
        gate_id=opened.gate_id,
        case_id=opened.case_id,
        run_id=opened.run_id,
        agent=subject["agent"],
        job_id=subject["job_id"],
        step=subject.get("step"),
        reason=subject.get("reason", ""),
        attempt=int(subject.get("attempt", "0") or 0),
        raw_inputs_ref=subject.get("raw_inputs_ref"),
        raw_inputs=subject.get("raw_inputs"),
        raw_inputs_sha256=subject.get("raw_inputs_sha256"),
        raw_inputs_bytes=(
            int(subject["raw_inputs_bytes"])
            if subject.get("raw_inputs_bytes", "").isdigit()
            else None
        ),
        raw_inputs_truncated=subject.get("raw_inputs_truncated") == "true",
        raw_inputs_error=subject.get("raw_inputs_error"),
        opened_at=now,
    )


_SYSTEM: Final = UiActor(id="svc:case", role="system", component="case")


def opened_event(row: HandoffGateRow) -> UiEventIn:
    """The activity row the analyst sees when an agent has failed twice. It is the
    analyst's alone (``analyst`` visibility): the PO is told the stage, nothing of
    why.
    """
    return UiEventIn(
        type="handoff.opened",
        visibility="analyst",
        summary=f"The {row.agent} agent failed twice; the step is waiting for you",
        detail={"agent": row.agent, "gate_id": row.gate_id, "reason": row.reason},
        actor=_SYSTEM,
    )


def gate_view(row: HandoffGateRow, decided: bool) -> HandoffGateView:
    """The analyst's view of one gate. The inputs text is shown while the gate
    is open; once it is decided the digest stays and the text does not (the
    list stays light, and the record names what was shown).
    """
    return HandoffGateView(
        gate_id=row.gate_id,
        agent=row.agent,
        job_id=row.job_id,
        step=row.step,
        reason=row.reason,
        attempt=row.attempt,
        raw_inputs_ref=row.raw_inputs_ref,
        raw_inputs=None if decided else row.raw_inputs,
        raw_inputs_sha256=row.raw_inputs_sha256,
        raw_inputs_truncated=row.raw_inputs_truncated,
        raw_inputs_error=row.raw_inputs_error,
        opened_at=row.opened_at,
        status="decided" if decided else "open",
    )


def output_fingerprint(output: list[dict[str, Any]], reason: str) -> str:
    """Two completions with one fingerprint say the same thing, so a retry replays
    and a different output for the same gate is a 409.
    """
    return decision_fingerprint(COMPLETE_DECISION, output, reason)


def handoff_command(
    *,
    row: HandoffGateRow,
    body: HandoffCompleteBody,
    accepted: list[dict[str, Any]],
    by: str,
    by_name: str,
    now: datetime,
) -> AnalystCommand:
    """The command that completes the handoff gate ``row`` by hand.

    ``accepted`` is the output as the check re-dumped it (what Caseflow will
    validate again); it travels in ``gate.decided``'s ``after`` and is stored in
    ``handoff_outputs`` with who, why and when.
    """
    event = gate_envelope(
        case_id=row.case_id,
        run_id=row.run_id,
        gate_id=row.gate_id,
        gate_type=GateType.HANDOFF,
        decision=COMPLETE_DECISION,
        decided_by=by,
        decided_at=now,
        subject={"kind": "handoff", "id": row.job_id},
        reason=body.reason,
        after={"output": accepted},
    )
    gate = AnalystGate(
        row.gate_id,
        GateType.HANDOFF,
        COMPLETE_DECISION,
        output_fingerprint(body.output, body.reason),
        event,
    )
    stored = OnceRecord(
        collection=OUTPUTS_COLLECTION,
        row_id=row.gate_id,
        document={
            "case_id": row.case_id,
            "run_id": row.run_id,
            "gate_id": row.gate_id,
            "agent": row.agent,
            "job_id": row.job_id,
            "output": accepted,
            "reason": body.reason,
            "decided_by": by,
            "decided_at": now,
        },
    )
    summary = f"{by_name} completed the {row.agent} step by hand"
    return AnalystCommand(
        case_id=row.case_id,
        actor=by,
        gates=[gate],
        records=[stored],
        activity=[
            activity_row(
                by,
                "handoff.completed",
                summary,
                {"agent": row.agent, "items": len(accepted)},
            )
        ],
    )
