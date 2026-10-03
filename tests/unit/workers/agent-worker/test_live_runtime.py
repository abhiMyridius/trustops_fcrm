"""``fcrm_agent_worker.live_runtime``: the real guard chain with a live
model call, against :mod:`fcrm_agents`' real, offline-proven
pre-guard/post-guard machinery and a fake model (module doc:
:mod:`conftest`).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fcrm_agent_worker.live_runtime import ModelFactory, run_agent_live
from fcrm_agents.context_pack import ContextPack
from fcrm_agents.demo import research_output
from fcrm_agents.runtime import RunVerdict
from fcrm_agents.spec import AgentSpec
from fcrm_guardrails import CaseBudget, GuardName, PermitGuard
from fcrm_tools import DEMO_CASE_ID, ToolRegistry
from pydantic import BaseModel
from pydantic_ai import UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.exceptions import FallbackExceptionGroup


async def test_pass_accepts_every_proposal(
    research_pack: ContextPack,
    research_outputs: list[BaseModel],
    research_spec: AgentSpec,
    fake_tiers_config: dict[str, Any],
    seed_registry: ToolRegistry,
    allow_all_permit: PermitGuard,
    make_model_factory_returning: Callable[..., ModelFactory],
) -> None:
    """A clean model answer on attempt 1 is accepted whole."""
    result, model_used, usage = await run_agent_live(
        research_spec,
        research_pack,
        "job-pass",
        registry=seed_registry,
        permit=allow_all_permit,
        budget=CaseBudget(DEMO_CASE_ID),
        instructions=None,
        tiers_config=fake_tiers_config,
        model_factory=make_model_factory_returning(research_outputs),
    )
    assert result.verdict is RunVerdict.PASS
    assert result.stopped_at is None
    assert len(result.accepted) == len(research_outputs)
    assert result.dropped == []
    assert len(result.attempts) == 1
    assert model_used == "fake-fixed-output"
    assert usage["input_tokens"] >= 0
    assert result.tokens_used >= 0


async def test_pass_with_drops_when_a_quote_is_tampered(
    research_pack: ContextPack,
    research_spec: AgentSpec,
    fake_tiers_config: dict[str, Any],
    seed_registry: ToolRegistry,
    allow_all_permit: PermitGuard,
    make_model_factory_returning: Callable[..., ModelFactory],
) -> None:
    """Guard 4 drops the one item whose quote no longer matches verbatim
    (the CLI's ``--tamper`` scenario); the run still passes with the rest.
    """
    tampered = research_output(research_pack, tamper=True)
    result, _model_used, _usage = await run_agent_live(
        research_spec,
        research_pack,
        "job-drop",
        registry=seed_registry,
        permit=allow_all_permit,
        budget=CaseBudget(DEMO_CASE_ID),
        instructions=None,
        tiers_config=fake_tiers_config,
        model_factory=make_model_factory_returning(tampered),
    )
    assert result.verdict is RunVerdict.PASS_WITH_DROPS
    assert result.dropped
    assert len(result.accepted) == len(tampered) - len(result.dropped)


async def test_blocked_on_permit_denial(
    research_pack: ContextPack,
    research_outputs: list[BaseModel],
    research_spec: AgentSpec,
    fake_tiers_config: dict[str, Any],
    seed_registry: ToolRegistry,
    deny_all_permit: PermitGuard,
    make_model_factory_returning: Callable[..., ModelFactory],
) -> None:
    """Guard 2 fails closed: the model is never even called."""
    result, model_used, usage = await run_agent_live(
        research_spec,
        research_pack,
        "job-permit",
        registry=seed_registry,
        permit=deny_all_permit,
        budget=CaseBudget(DEMO_CASE_ID),
        instructions=None,
        tiers_config=fake_tiers_config,
        model_factory=make_model_factory_returning(research_outputs),
    )
    assert result.verdict is RunVerdict.BLOCKED
    assert result.stopped_at is GuardName.PERMIT
    assert model_used == ""
    assert usage == {}
    assert result.attempts == []


async def test_blocked_on_budget_exhaustion(
    research_pack: ContextPack,
    research_outputs: list[BaseModel],
    research_spec: AgentSpec,
    fake_tiers_config: dict[str, Any],
    seed_registry: ToolRegistry,
    allow_all_permit: PermitGuard,
    make_model_factory_returning: Callable[..., ModelFactory],
) -> None:
    """A case budget with no room left blocks before any guard 1 input scan
    or model call."""
    tiny_budget = CaseBudget(DEMO_CASE_ID, cap_tokens=1)
    result, _model_used, _usage = await run_agent_live(
        research_spec,
        research_pack,
        "job-budget",
        registry=seed_registry,
        permit=allow_all_permit,
        budget=tiny_budget,
        instructions=None,
        tiers_config=fake_tiers_config,
        model_factory=make_model_factory_returning(research_outputs),
    )
    assert result.verdict is RunVerdict.BLOCKED
    assert result.stopped_at is GuardName.BUDGET
    assert result.attempts == []
    assert not tiny_budget.has_reservation("job-budget")


@pytest.mark.parametrize(
    "exc",
    [
        UnexpectedModelBehavior("model never produced output"),
        FallbackExceptionGroup("every model failed", [ConnectionError("down")]),
        TimeoutError("attempt exceeded its wall clock"),
    ],
)
async def test_blocked_after_both_attempts_fail_the_model_call(
    research_pack: ContextPack,
    research_spec: AgentSpec,
    fake_tiers_config: dict[str, Any],
    seed_registry: ToolRegistry,
    allow_all_permit: PermitGuard,
    make_model_factory_raising: Callable[..., ModelFactory],
    exc: Exception,
) -> None:
    """A model/transport failure on attempt 1 is a retry candidate; the
    same failure on attempt 2 (the tier's last) blocks the job -- never a
    silent ``agent.done`` for a job that did not actually finish.
    """
    result, model_used, usage = await run_agent_live(
        research_spec,
        research_pack,
        "job-model-error",
        registry=seed_registry,
        permit=allow_all_permit,
        budget=CaseBudget(DEMO_CASE_ID),
        instructions=None,
        tiers_config=fake_tiers_config,
        model_factory=make_model_factory_raising(exc),
    )
    assert result.verdict is RunVerdict.BLOCKED
    assert result.stopped_at is GuardName.OUTPUT_SHAPE
    assert len(result.attempts) == 2
    assert result.attempts[0].guard_results[-1].status.value == "retry"
    assert result.attempts[1].guard_results[-1].status.value == "blocked"
    assert usage == {}


async def test_usage_limit_exceeded_blocks_without_retry(
    research_pack: ContextPack,
    research_spec: AgentSpec,
    fake_tiers_config: dict[str, Any],
    seed_registry: ToolRegistry,
    allow_all_permit: PermitGuard,
    make_model_factory_raising: Callable[..., ModelFactory],
) -> None:
    """``UsageLimitExceeded`` blocks immediately, even on attempt 1 (a
    budget failure is never retried, same as the offline runtime).
    """
    result, _model_used, _usage = await run_agent_live(
        research_spec,
        research_pack,
        "job-usage-limit",
        registry=seed_registry,
        permit=allow_all_permit,
        budget=CaseBudget(DEMO_CASE_ID),
        instructions=None,
        tiers_config=fake_tiers_config,
        model_factory=make_model_factory_raising(UsageLimitExceeded("too many tokens")),
    )
    assert result.verdict is RunVerdict.BLOCKED
    assert result.stopped_at is GuardName.BUDGET
    assert len(result.attempts) == 1
