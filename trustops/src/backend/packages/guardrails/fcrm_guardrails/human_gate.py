"""Guard 7: human gate decision validation (state; H1; design B §4.8,
design A §3.2: "H1 matches gate_id (or subject), refuses decisions from the
wrong role, and advances").

:func:`validate_gate_decision` is the pure check H1 runs on every
``gate.decided`` before it advances the state machine. It refuses, listing
every reason at once:

- a gate that is not ``open`` (already decided, or expired);
- a decision for another gate, gate type, case or run;
- a principal whose token is not an access token (the cloned identity
  service accepted refresh tokens as access tokens; this is a second line);
- a principal without the gate's ``required_role``;
- a decision whose ``decided_by`` is not the principal;
- a decision that needs a reason and has none: every committee vote
  (routing R2, "rationale required on each vote"), every unlock (R6), every
  handoff (a person completing a step the machine could not),
  every override/remove/adjust/explain decision, and every decision whose
  ``after`` differs from ``before`` (rule 6, through the same function);
- a vote whose decision is not one of the four outcomes.

Nothing here approves anything: a valid decision only lets H1 *record* the
human's choice.
"""

from __future__ import annotations

import time
from typing import Final

from fcrm_contracts.common import GateDecision, Principal
from fcrm_contracts.enums import GateStatus, GateType, Role, VoteOption
from fcrm_contracts.events.payloads import GateOpened
from pydantic import BaseModel, ConfigDict, Field

from fcrm_guardrails.base import AUDIT_TYPES, GuardName, GuardResult, GuardStatus
from fcrm_guardrails.rules.r6_override_has_reason import OverrideCheck
from fcrm_guardrails.rules.r6_override_has_reason import check as override_has_reason
from fcrm_guardrails.thresholds import OVERRIDE_REASON_MIN_CHARS

REASON_REQUIRED_GATES: Final[frozenset[GateType]] = frozenset(
    {GateType.VOTE, GateType.UNLOCK, GateType.HANDOFF}
)
REASON_REQUIRED_DECISIONS: Final[frozenset[str]] = frozenset(
    {"override", "remove", "adjust", "explain"}
)


class GateRecord(BaseModel):
    """The gate as H1 stores it (``fcrm_harness.gates``): what a decision is
    validated against."""

    model_config = ConfigDict(extra="forbid")

    gate_id: str
    gate_type: GateType
    case_id: str
    run_id: str
    required_role: Role
    status: GateStatus = GateStatus.OPEN
    subject: dict[str, str] = Field(default_factory=dict)

    @classmethod
    def from_opened(
        cls, opened: GateOpened, status: GateStatus = GateStatus.OPEN
    ) -> GateRecord:
        """Build from the ``gate.opened`` payload plus the current status."""
        return cls(
            gate_id=opened.gate_id,
            gate_type=opened.gate_type,
            case_id=opened.case_id,
            run_id=opened.run_id,
            required_role=Role(opened.required_role),
            status=status,
            subject=dict(opened.subject),
        )


def _identity_problems(
    gate: GateRecord, decision: GateDecision, principal: Principal
) -> list[str]:
    problems: list[str] = []
    if gate.status is not GateStatus.OPEN:
        problems.append(f"gate is {gate.status.value}, not open")
    pairs = (
        ("gate_id", gate.gate_id, decision.gate_id),
        ("gate_type", gate.gate_type, decision.gate_type),
        ("case_id", gate.case_id, decision.case_id),
        ("run_id", gate.run_id, decision.run_id),
    )
    problems.extend(
        f"{name} mismatch: gate {expected}, decision {got}"
        for name, expected, got in pairs
        if expected != got
    )
    if principal.typ != "access":
        problems.append(f"token type {principal.typ!r} is not an access token")
    if gate.required_role not in principal.roles:
        problems.append(f"requires role {gate.required_role.value}")
    if decision.decided_by != principal.sub:
        problems.append("decided_by is not the authenticated principal")
    return problems


def _content_problems(
    gate: GateRecord, decision: GateDecision, min_reason_chars: int
) -> list[str]:
    problems: list[str] = []
    reason = (decision.reason or "").strip()
    needs_reason = (
        gate.gate_type in REASON_REQUIRED_GATES
        or decision.decision in REASON_REQUIRED_DECISIONS
    )
    if needs_reason and len(reason) < min_reason_chars:
        problems.append(
            f"{gate.gate_type.value}/{decision.decision} needs a reason of at least "
            f"{min_reason_chars} characters"
        )
    if decision.before is not None and decision.after is not None:
        rule = override_has_reason(
            OverrideCheck(
                proposed_value=decision.before,
                confirmed_value=decision.after,
                reason=decision.reason,
            ),
            min_chars=min_reason_chars,
        )
        if not rule.ok:
            problems.append(f"R-6: {rule.detail}")
    if gate.gate_type is GateType.VOTE and decision.decision not in set(VoteOption):
        problems.append(f"vote {decision.decision!r} is not one of the four outcomes")
    return problems


def validate_gate_decision(
    gate: GateRecord,
    decision: GateDecision,
    principal: Principal,
    *,
    min_reason_chars: int = OVERRIDE_REASON_MIN_CHARS,
) -> GuardResult:
    """``passed`` when ``principal`` may record ``decision`` on ``gate``;
    ``blocked`` (the case waits) with every reason otherwise."""
    started = time.perf_counter()
    problems = _identity_problems(gate, decision, principal) + _content_problems(
        gate, decision, min_reason_chars
    )
    details: dict[str, object] = {
        "gate_id": gate.gate_id,
        "gate_type": gate.gate_type.value,
        "decision": decision.decision,
        "decided_by": decision.decided_by,
        "problems": problems,
    }
    if problems:
        status = GuardStatus.BLOCKED
        summary = f"{gate.gate_type.value} decision refused: " + "; ".join(problems)
    else:
        status = GuardStatus.PASSED
        summary = (
            f"{gate.gate_type.value} decided by {decision.decided_by}: "
            f"{decision.decision}"
        )
    return GuardResult(
        guard=GuardName.HUMAN_GATE,
        status=status,
        summary=summary,
        details=details,
        audit_type=AUDIT_TYPES[GuardName.HUMAN_GATE],
        duration_ms=int((time.perf_counter() - started) * 1000),
    )
