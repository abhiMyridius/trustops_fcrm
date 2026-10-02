"""Guard 3: tier budgets, the case ledger, and the attempt plan."""

from __future__ import annotations

import pytest
from fcrm_contracts.enums import AgentName, Tier
from fcrm_guardrails.base import GuardStatus
from fcrm_guardrails.budget import (
    CASE_TOKEN_CAP,
    TIER_BUDGETS,
    AttemptsExhaustedError,
    BudgetExceededError,
    BudgetGuard,
    CaseBudget,
    ModelSlot,
    UnknownReservationError,
    attempt_plan,
    call_exceeds_tier,
)
from fcrm_guardrails.context import GuardContext
from fcrm_seed.loader import load_raw

SEED_BUDGETS = load_raw()["budgets"]


@pytest.mark.parametrize(
    ("tier", "seed_key"),
    [
        (Tier.CHEAP, "cheap"),
        (Tier.MID, "mid"),
        (Tier.STRONG, "strong"),
        (Tier.JUDGE, "strong + judge"),
    ],
)
def test_tier_budgets_match_the_seed(tier: Tier, seed_key: str) -> None:
    budget = TIER_BUDGETS[tier]
    assert budget.tokens == SEED_BUDGETS[seed_key]["tokens_per_run"]
    assert budget.wall_s == SEED_BUDGETS[seed_key]["wall_s"]
    assert budget.retries == SEED_BUDGETS[seed_key]["retries"]


def test_case_token_cap_matches_the_seed() -> None:
    assert CASE_TOKEN_CAP == SEED_BUDGETS["per_case"] == 120_000


def test_reserve_holds_tokens_times_attempts() -> None:
    budget = CaseBudget("c1")
    reservation = budget.reserve("job-1", AgentName.RESEARCH, Tier.STRONG)
    assert reservation.tokens == 40_000
    assert budget.remaining() == 80_000


def test_reserve_is_idempotent_per_id() -> None:
    budget = CaseBudget("c1")
    budget.reserve("job-1", AgentName.SCREENING, Tier.CHEAP)
    budget.reserve("job-1", AgentName.SCREENING, Tier.CHEAP)
    assert budget.reserved == 12_000


def test_reserve_past_the_cap_raises() -> None:
    budget = CaseBudget("c1")
    for n in range(3):
        budget.reserve(f"job-{n}", AgentName.RESEARCH, Tier.STRONG)
    assert budget.would_exceed(Tier.CHEAP)
    with pytest.raises(BudgetExceededError):
        budget.reserve("job-x", AgentName.SCREENING, Tier.CHEAP)


def test_settle_releases_and_records_actual_usage() -> None:
    budget = CaseBudget("c1")
    budget.reserve("job-1", AgentName.RESEARCH, Tier.STRONG)
    assert budget.settle("job-1", 8_000) == 8_000
    assert budget.reserved == 0
    assert budget.remaining() == 112_000
    assert budget.snapshot().per_agent["research"].tokens == 8_000


def test_settle_unknown_or_negative_raises() -> None:
    budget = CaseBudget("c1")
    with pytest.raises(UnknownReservationError):
        budget.settle("nope", 10)
    budget.reserve("job-1", AgentName.CRITIC, Tier.JUDGE)
    with pytest.raises(ValueError, match=">= 0"):
        budget.settle("job-1", -1)


def test_settle_overrun_is_recorded_and_blocks_further_reservations() -> None:
    budget = CaseBudget("c1", cap_tokens=20_000)
    budget.reserve("job-1", AgentName.SCREENING, Tier.CHEAP)
    budget.settle("job-1", 19_000)
    assert budget.remaining() == 1_000
    assert budget.would_exceed(Tier.CHEAP)


def test_release_drops_without_usage() -> None:
    budget = CaseBudget("c1")
    budget.reserve("job-1", AgentName.NARRATIVE, Tier.MID)
    budget.release("job-1")
    assert budget.used == 0
    assert budget.reserved == 0
    with pytest.raises(UnknownReservationError):
        budget.release("job-1")


def test_snapshot_round_trips() -> None:
    budget = CaseBudget("c1")
    budget.reserve("job-1", AgentName.RESEARCH, Tier.STRONG)
    budget.reserve("job-2", AgentName.ASSESSMENT, Tier.STRONG)
    budget.settle("job-1", 9_500)
    restored = CaseBudget.from_snapshot(budget.snapshot())
    assert restored.snapshot() == budget.snapshot()
    assert restored.remaining() == budget.remaining()


def test_attempt_plan_fallback_order() -> None:
    first = attempt_plan(Tier.STRONG, 1)
    second = attempt_plan(Tier.STRONG, 2)
    assert first.slot is ModelSlot.PRIMARY_WITH_FALLBACK
    assert first.max_tokens_factor == 1.0
    assert second.slot is ModelSlot.FALLBACK_ONLY
    assert second.max_tokens_factor == 0.5
    assert second.token_limit == 20_000
    assert second.wall_s == 180
    with pytest.raises(AttemptsExhaustedError):
        attempt_plan(Tier.STRONG, 3)
    with pytest.raises(ValueError, match=">= 1"):
        attempt_plan(Tier.STRONG, 0)


def test_call_exceeds_tier() -> None:
    assert not call_exceeds_tier(Tier.CHEAP, tokens_used=6_000, elapsed_s=60)
    assert call_exceeds_tier(Tier.CHEAP, tokens_used=6_001, elapsed_s=1)
    assert call_exceeds_tier(Tier.CHEAP, tokens_used=1, elapsed_s=60.5)


def _ctx(budget: CaseBudget | None, **kwargs: object) -> GuardContext:
    return GuardContext.model_validate(
        {
            "case_id": "c1",
            "run_id": "r1",
            "tier": Tier.STRONG,
            "budget": budget,
            **kwargs,
        }
    )


async def test_check_h1_passes_when_a_run_fits() -> None:
    result = await BudgetGuard().check(_ctx(CaseBudget("c1")))
    assert result.status is GuardStatus.PASSED
    assert result.audit_type == "guard.budget"
    assert result.details["slot"] == "primary_with_fallback"


async def test_check_h1_blocks_when_exhausted() -> None:
    budget = CaseBudget("c1", cap_tokens=30_000)
    result = await BudgetGuard().check(_ctx(budget))
    assert result.status is GuardStatus.BLOCKED
    assert "exhausted" in result.summary


async def test_check_h2_requires_the_reservation() -> None:
    budget = CaseBudget("c1")
    budget.reserve("job-1", AgentName.RESEARCH, Tier.STRONG)
    held = await BudgetGuard().check(_ctx(budget, reservation_id="job-1"))
    missing = await BudgetGuard().check(_ctx(budget, reservation_id="job-2"))
    assert held.status is GuardStatus.PASSED
    assert missing.status is GuardStatus.BLOCKED


async def test_check_blocks_a_third_attempt() -> None:
    result = await BudgetGuard().check(_ctx(CaseBudget("c1"), attempt=3))
    assert result.status is GuardStatus.BLOCKED


async def test_check_without_ledger_fails_closed() -> None:
    result = await BudgetGuard().check(_ctx(None))
    assert result.status is GuardStatus.BLOCKED
