"""Rule 2: no bulk accept (one item per decision, human pace)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fcrm_contracts.common import GateDecision
from fcrm_contracts.enums import GateType
from fcrm_guardrails.rules.r2_no_bulk_accept import DecisionAction, check, to_action

T0 = datetime(2026, 10, 3, 9, 0, tzinfo=UTC)


def test_check_single_item_bodies_pass() -> None:
    assert check({"item_ids": ["itm_41"], "actor": "u-riyer"}).ok
    assert check({"item_id": "itm_41", "actor": "u-riyer"}).ok


def test_check_bulk_body_fails() -> None:
    result = check({"item_ids": ["itm_41", "itm_42", "itm_43"], "actor": "u-riyer"})
    assert not result.ok
    assert "targets 3 items" in result.detail


def test_check_empty_or_malformed_bodies_fail() -> None:
    assert not check({"item_ids": [], "actor": "u-riyer"}).ok
    assert not check({"item_ids": "itm_41,itm_42", "actor": "u-riyer"}).ok
    assert not check({"actor": "u-riyer"}).ok


def test_check_demo_clause_decisions_at_human_pace_pass() -> None:
    decisions = [
        DecisionAction(
            actor="u-riyer",
            item_ids=[f"itm_{n}"],
            decided_at=T0 + timedelta(seconds=20 * n),
        )
        for n in range(4)
    ]
    assert check(decisions).ok


def test_check_same_actor_under_300ms_fails() -> None:
    decisions = [
        DecisionAction(actor="u-riyer", item_ids=["itm_1"], decided_at=T0),
        DecisionAction(
            actor="u-riyer",
            item_ids=["itm_2"],
            decided_at=T0 + timedelta(milliseconds=120),
        ),
    ]
    result = check(decisions)
    assert not result.ok
    assert "u-riyer" in result.detail


def test_check_different_actors_at_the_same_moment_pass() -> None:
    decisions = [
        DecisionAction(actor="u-amendes", item_ids=["vote_1"], decided_at=T0),
        DecisionAction(actor="u-jokafor", item_ids=["vote_2"], decided_at=T0),
    ]
    assert check(decisions).ok


def test_to_action_from_gate_decision() -> None:
    decision = GateDecision(
        gate_id="gate_41",
        gate_type=GateType.REVIEW_ITEM,
        case_id="FCRM-2026-0151",
        run_id="run_1",
        decision="accept",
        decided_by="u-riyer",
        decided_at=T0,
    )
    action = to_action(decision)
    assert action.item_ids == ["gate_41"]
    assert check(decision).ok
