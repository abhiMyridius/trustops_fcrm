"""Guard 7: validating a human gate decision against the gate and caller."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fcrm_contracts.common import GateDecision, Principal
from fcrm_contracts.enums import GateStatus, GateType, Role
from fcrm_contracts.events.payloads import GateOpened
from fcrm_guardrails.base import GuardStatus
from fcrm_guardrails.human_gate import GateRecord, validate_gate_decision

AT = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
RAHUL = Principal(sub="u-riyer", roles=[Role.ANALYST], name="Rahul Iyer", typ="access")
LIN = Principal(sub="u-lchen", roles=[Role.COMMITTEE], name="Lin Chen", typ="access")
PRIYA = Principal(sub="u-pnair", roles=[Role.PO], name="Priya Nair", typ="access")

REVIEW_GATE = GateRecord(
    gate_id="gate_49",
    gate_type=GateType.REVIEW_ITEM,
    case_id="FCRM-2026-0151",
    run_id="run_1",
    required_role=Role.ANALYST,
    subject={"kind": "control", "id": "CTL-T01"},
)
VOTE_GATE = REVIEW_GATE.model_copy(
    update={
        "gate_id": "gate_63",
        "gate_type": GateType.VOTE,
        "required_role": Role.COMMITTEE,
    }
)


def _decision(gate: GateRecord, by: str, decision: str, **kw: Any) -> GateDecision:
    return GateDecision(
        gate_id=gate.gate_id,
        gate_type=gate.gate_type,
        case_id=gate.case_id,
        run_id=gate.run_id,
        decision=decision,
        decided_by=by,
        decided_at=AT,
        **kw,
    )


def test_validate_demo_override_with_reason_passes() -> None:
    decision = _decision(
        REVIEW_GATE,
        "u-riyer",
        "override",
        reason="audit predates the corridor arrangement",
        before={"rating": 7.6},
        after={"rating": 4.5},
    )
    result = validate_gate_decision(REVIEW_GATE, decision, RAHUL)
    assert result.status is GuardStatus.PASSED
    assert result.audit_type == "gate.decided"
    assert result.summary == "review_item decided by u-riyer: override"


def test_validate_override_without_reason_is_refused() -> None:
    decision = _decision(
        REVIEW_GATE,
        "u-riyer",
        "override",
        before={"rating": 7.6},
        after={"rating": 4.5},
    )
    result = validate_gate_decision(REVIEW_GATE, decision, RAHUL)
    assert result.status is GuardStatus.BLOCKED
    assert any("R-6" in p for p in result.details["problems"])


def test_validate_confirm_unchanged_needs_no_reason() -> None:
    decision = _decision(
        REVIEW_GATE,
        "u-riyer",
        "confirm",
        before={"rating": 8.4},
        after={"rating": 8.40},
    )
    assert validate_gate_decision(REVIEW_GATE, decision, RAHUL).passed


def test_validate_wrong_role_is_refused() -> None:
    decision = _decision(REVIEW_GATE, "u-pnair", "confirm")
    result = validate_gate_decision(REVIEW_GATE, decision, PRIYA)
    assert not result.passed
    assert "requires role fcrm_analyst" in result.details["problems"]


def test_validate_closed_gate_is_refused() -> None:
    gate = REVIEW_GATE.model_copy(update={"status": GateStatus.DECIDED})
    result = validate_gate_decision(gate, _decision(gate, "u-riyer", "confirm"), RAHUL)
    assert "gate is decided, not open" in result.details["problems"]


def test_validate_mismatched_gate_and_impersonation_are_all_listed() -> None:
    decision = _decision(REVIEW_GATE, "u-someone-else", "confirm").model_copy(
        update={"gate_id": "gate_x", "case_id": "FCRM-2026-0147"}
    )
    problems = validate_gate_decision(REVIEW_GATE, decision, RAHUL).details["problems"]
    assert any(p.startswith("gate_id mismatch") for p in problems)
    assert any(p.startswith("case_id mismatch") for p in problems)
    assert "decided_by is not the authenticated principal" in problems


def test_validate_refresh_token_is_refused() -> None:
    refresh = RAHUL.model_copy(update={"typ": "refresh"})
    result = validate_gate_decision(
        REVIEW_GATE, _decision(REVIEW_GATE, "u-riyer", "confirm"), refresh
    )
    assert not result.passed


def test_validate_vote_needs_rationale_and_a_real_outcome() -> None:
    no_reason = _decision(VOTE_GATE, "u-lchen", "defer")
    bad_option = _decision(
        VOTE_GATE, "u-lchen", "approve_automatically", reason="corridor scope unclear"
    )
    good = _decision(
        VOTE_GATE, "u-lchen", "defer", reason="wait for the corridor DD review"
    )
    assert not validate_gate_decision(VOTE_GATE, no_reason, LIN).passed
    assert not validate_gate_decision(VOTE_GATE, bad_option, LIN).passed
    assert validate_gate_decision(VOTE_GATE, good, LIN).passed


def test_gate_record_from_opened() -> None:
    opened = GateOpened(
        gate_id="gate_1",
        gate_type=GateType.FAST_LANE_CONFIRM,
        case_id="FCRM-2026-0151",
        run_id="run_1",
        subject={"kind": "screening"},
        required_role="fcrm_analyst",
    )
    record = GateRecord.from_opened(opened)
    assert record.required_role is Role.ANALYST
    assert record.status is GateStatus.OPEN
