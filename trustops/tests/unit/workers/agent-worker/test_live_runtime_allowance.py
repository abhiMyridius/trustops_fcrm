"""The live runtime's per-request allowance, kept overrun, in-run repair and
wall clock (ADR-80), and the ``LiveRun`` seams a chunked job uses.

- Each request's ``max_tokens`` is what the attempt's budget leaves; after the
  first response it is sized from the measured input.
- A valid answer that tips the attempt over its budget is kept and annotated,
  and its tokens are charged.
- A repair that fails too ends the attempt, and attempt 2 runs on the
  fallback chain naming the problem (the repair itself, and no repair when the
  budget cannot hold one, are scenario S10 of ``test_live_runtime_scenarios``).
- An attempt that outlives the tier's wall clock is a failed model call: a
  retry, then a block.
- The budget reservation and the part number are the caller's to choose.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

import pytest
from fcrm_agent_worker import live_runtime
from fcrm_agent_worker.live_runtime import (
    LiveRun,
    ModelFactory,
    describe_failure,
    run_agent_live,
)
from fcrm_agents.context_pack import ContextPack
from fcrm_agents.demo import research_output
from fcrm_agents.runtime import RunVerdict
from fcrm_agents.spec import AgentSpec
from fcrm_contracts.enums import AgentName, Tier
from fcrm_guardrails import CaseBudget, GuardName, GuardStatus, PermitGuard
from fcrm_guardrails.budget import TIER_BUDGETS, AttemptPlan, Reservation
from fcrm_tools import DEMO_CASE_ID, ToolRegistry
from pydantic import BaseModel, TypeAdapter, ValidationError
from pydantic_ai import UnexpectedModelBehavior
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo
from pydantic_ai.usage import RequestUsage

POLICY_SEARCH_ARGS: dict[str, Any] = {
    "index": "policy_clauses",
    "query": "cash pickup agent network",
    "top_k": 3,
    "filters": {"corpus_version": 14},
}
STRONG_LIMIT = TIER_BUDGETS[Tier.STRONG].tokens
"""The strong tier's attempt budget (``TIER_BUDGETS``); research runs on it."""
SEARCH_INPUT = STRONG_LIMIT // 2 - 2_000
"""The measured input of the scripted policy search: big enough that the
budget it leaves, less the next request's input, is under the tier's cap."""
TIPPED_OUTPUT = 4_500
"""The output of an answer that tips its attempt 500 tokens over the budget
(within the tier's 5,000-token ``max_tokens``, so the model may send it)."""


def _output(
    items: list[BaseModel], usage: RequestUsage | None = None
) -> Callable[[AgentInfo, list[ModelMessage]], Any]:
    payload = [item.model_dump(mode="json") for item in items]

    def reply(info: AgentInfo, messages: list[ModelMessage]) -> ModelResponse:
        tool = info.output_tools[0]
        return ModelResponse(
            parts=[ToolCallPart(tool_name=tool.name, args={"response": payload})],
            usage=usage or RequestUsage(input_tokens=4_000, output_tokens=900),
        )

    return reply


def _invalid(info: AgentInfo, messages: list[ModelMessage]) -> ModelResponse:
    """An output-tool call without its list (what a model sends as ``{}``)."""
    return ModelResponse(
        parts=[ToolCallPart(tool_name=info.output_tools[0].name, args={})],
        usage=RequestUsage(input_tokens=4_000, output_tokens=20),
    )


def _search(info: AgentInfo, messages: list[ModelMessage]) -> ModelResponse:
    return ModelResponse(
        parts=[ToolCallPart(tool_name="policy_search", args=POLICY_SEARCH_ARGS)],
        usage=RequestUsage(input_tokens=SEARCH_INPUT, output_tokens=200),
    )


class SpyBudget(CaseBudget):
    """A :class:`CaseBudget` that records which reservation ids were used."""

    def __init__(self) -> None:
        super().__init__(DEMO_CASE_ID)
        self.reserved_ids: list[str] = []
        self.settled_ids: list[str] = []

    def reserve(self, reservation_id: str, agent: AgentName, tier: Tier) -> Reservation:
        self.reserved_ids.append(reservation_id)
        return super().reserve(reservation_id, agent, tier)

    def settle(self, reservation_id: str, actual_tokens: int) -> int:
        self.settled_ids.append(reservation_id)
        return super().settle(reservation_id, actual_tokens)


async def _run(
    spec: AgentSpec,
    pack: ContextPack,
    registry: ToolRegistry,
    permit: PermitGuard,
    tiers: dict[str, Any],
    factory: ModelFactory,
    budget: CaseBudget | None = None,
) -> Any:
    result, _model, _usage = await run_agent_live(
        spec,
        pack,
        "job-allowance",
        registry=registry,
        permit=permit,
        budget=budget or CaseBudget(DEMO_CASE_ID),
        instructions=None,
        tiers_config=tiers,
        model_factory=factory,
    )
    return result


def _patch_plan(
    monkeypatch: pytest.MonkeyPatch, *, only_attempt: int | None = None, **update: Any
) -> None:
    """Change the attempt plans the live runtime builds (a field of Guard 3's
    tier table, without editing it): every attempt's, or ``only_attempt``'s."""
    real = live_runtime.attempt_plan

    def patched(tier: Tier, attempt: int) -> AttemptPlan:
        plan = real(tier, attempt)
        if only_attempt is not None and attempt != only_attempt:
            return plan
        return plan.model_copy(update=update)

    monkeypatch.setattr(live_runtime, "attempt_plan", patched)


async def test_the_second_request_is_sized_from_the_measured_first_input(
    research_pack: ContextPack,
    research_spec: AgentSpec,
    fake_tiers_config: dict[str, Any],
    seed_registry: ToolRegistry,
    allow_all_permit: PermitGuard,
    make_model_factory_scripted: Callable[..., ModelFactory],
) -> None:
    seen: list[Any] = []
    factory = make_model_factory_scripted(
        [[_search, _output(research_output(research_pack))]], seen
    )
    result = await _run(
        research_spec,
        research_pack,
        seed_registry,
        allow_all_permit,
        fake_tiers_config,
        factory,
    )
    assert result.verdict is RunVerdict.PASS
    tier_max = fake_tiers_config["tiers"]["strong"]["max_tokens"]
    first, second = result.attempts[0].allowances
    assert [s.round_trip for s in seen] == [0, 1]
    assert seen[0].max_tokens == first["allowance"] == tier_max
    assert (first["actual_input"], first["actual_output"]) == (SEARCH_INPUT, 200)
    # the measured input + 200 the model reads back, plus the tool result
    spent = SEARCH_INPUT + 200
    assert second["estimated_input"] > spent
    assert second["allowance"] == STRONG_LIMIT - spent - second["estimated_input"]
    assert seen[1].max_tokens == second["allowance"] < tier_max


async def test_a_valid_answer_that_tips_over_the_budget_is_kept_and_annotated(
    research_pack: ContextPack,
    research_spec: AgentSpec,
    fake_tiers_config: dict[str, Any],
    seed_registry: ToolRegistry,
    allow_all_permit: PermitGuard,
    make_model_factory_scripted: Callable[..., ModelFactory],
    caplog: pytest.LogCaptureFixture,
) -> None:
    seen: list[Any] = []
    tipped = RequestUsage(
        input_tokens=STRONG_LIMIT + 500 - TIPPED_OUTPUT, output_tokens=TIPPED_OUTPUT
    )
    factory = make_model_factory_scripted(
        [[_output(research_output(research_pack), tipped)]], seen
    )
    budget = CaseBudget(DEMO_CASE_ID)
    with caplog.at_level(logging.WARNING, logger=live_runtime.__name__):
        result = await _run(
            research_spec,
            research_pack,
            seed_registry,
            allow_all_permit,
            fake_tiers_config,
            factory,
            budget,
        )
    assert result.verdict is RunVerdict.PASS
    assert len(result.attempts) == 1
    overrun = next(
        r for r in result.attempts[0].guard_results if r.guard is GuardName.BUDGET
    )
    assert overrun.status is GuardStatus.ANNOTATED
    assert overrun.summary == (
        f"attempt 1: overran its {STRONG_LIMIT}-token attempt budget by 500; "
        "answer kept"
    )
    assert overrun.details == {
        "limit": STRONG_LIMIT,
        "spent": STRONG_LIMIT + 500,
        "overrun": 500,
    }
    assert result.tokens_used == STRONG_LIMIT + 500
    assert budget.used == STRONG_LIMIT + 500
    assert any("overran its budget" in r.getMessage() for r in caplog.records)


async def test_a_repair_that_also_fails_goes_to_attempt_two_naming_the_problem(
    research_pack: ContextPack,
    research_spec: AgentSpec,
    fake_tiers_config: dict[str, Any],
    seed_registry: ToolRegistry,
    allow_all_permit: PermitGuard,
    make_model_factory_scripted: Callable[..., ModelFactory],
) -> None:
    seen: list[Any] = []
    factory = make_model_factory_scripted(
        [[_invalid, _invalid], [_output(research_output(research_pack))]], seen
    )
    result = await _run(
        research_spec,
        research_pack,
        seed_registry,
        allow_all_permit,
        fake_tiers_config,
        factory,
    )
    assert result.verdict is RunVerdict.PASS
    assert [s.attempt for s in seen] == [1, 1, 2]
    failed = result.attempts[0].guard_results[-1]
    assert failed.status is GuardStatus.RETRY
    assert "Exceeded maximum output retries (1)" in failed.summary
    assert "response: Field required" in failed.summary


def _slow(info: AgentInfo, messages: list[ModelMessage]) -> Any:
    async def reply() -> ModelResponse:
        await asyncio.sleep(5)
        return _invalid(info, messages)

    return reply()


async def test_an_attempt_past_the_wall_clock_is_retried_then_blocked(
    research_pack: ContextPack,
    research_spec: AgentSpec,
    fake_tiers_config: dict[str, Any],
    seed_registry: ToolRegistry,
    allow_all_permit: PermitGuard,
    make_model_factory_scripted: Callable[..., ModelFactory],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _patch_plan(monkeypatch, wall_s=0.05)
    seen: list[Any] = []
    factory = make_model_factory_scripted([[_slow], [_slow]], seen)
    with caplog.at_level(logging.WARNING, logger=live_runtime.__name__):
        result = await _run(
            research_spec,
            research_pack,
            seed_registry,
            allow_all_permit,
            fake_tiers_config,
            factory,
        )
    assert [s.attempt for s in seen] == [1, 2]
    assert result.verdict is RunVerdict.BLOCKED
    assert result.stopped_at is GuardName.OUTPUT_SHAPE
    first, second = (a.guard_results[-1] for a in result.attempts)
    assert first.status is GuardStatus.RETRY
    assert second.status is GuardStatus.BLOCKED
    assert first.details["error"] == "TimeoutError"
    assert "wall clock" in (result.reason or "")
    assert [getattr(r, "wall_s", None) for r in caplog.records] == [0.05, 0.05]


async def test_an_attempt_past_the_wall_clock_is_retried_and_can_pass(
    research_pack: ContextPack,
    research_spec: AgentSpec,
    fake_tiers_config: dict[str, Any],
    seed_registry: ToolRegistry,
    allow_all_permit: PermitGuard,
    make_model_factory_scripted: Callable[..., ModelFactory],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Only attempt 1 is short: the passing attempt keeps the tier's wall clock,
    # so a loaded test run cannot time it out too.
    _patch_plan(monkeypatch, only_attempt=1, wall_s=0.05)
    seen: list[Any] = []
    factory = make_model_factory_scripted(
        [[_slow], [_output(research_output(research_pack))]], seen
    )
    result = await _run(
        research_spec,
        research_pack,
        seed_registry,
        allow_all_permit,
        fake_tiers_config,
        factory,
    )
    assert result.verdict is RunVerdict.PASS
    assert len(result.attempts) == 2


async def test_a_run_reserves_and_settles_under_the_reservation_it_is_given(
    research_pack: ContextPack,
    research_spec: AgentSpec,
    fake_tiers_config: dict[str, Any],
    seed_registry: ToolRegistry,
    allow_all_permit: PermitGuard,
    make_model_factory_scripted: Callable[..., ModelFactory],
) -> None:
    budget = SpyBudget()
    run = LiveRun(
        research_spec,
        research_pack,
        "job-parts",
        registry=seed_registry,
        permit=allow_all_permit,
        budget=budget,
        instructions=None,
        tiers_config=fake_tiers_config,
        model_factory=make_model_factory_scripted(
            [[_output(research_output(research_pack))]], []
        ),
        reservation_id="job-parts#p2",
        part=2,
    )
    result = await run.run_all()
    assert result.verdict is RunVerdict.PASS
    assert budget.reserved_ids == ["job-parts#p2"]
    assert budget.settled_ids == ["job-parts#p2"]
    assert not budget.has_reservation("job-parts#p2")
    assert run.base.job_id == "job-parts"
    assert [a.part for a in result.attempts] == [2]
    assert run.model_used == "scripted-1"


async def test_a_job_that_is_not_split_reserves_under_its_job_id_as_part_one(
    research_pack: ContextPack,
    research_spec: AgentSpec,
    fake_tiers_config: dict[str, Any],
    seed_registry: ToolRegistry,
    allow_all_permit: PermitGuard,
    make_model_factory_scripted: Callable[..., ModelFactory],
) -> None:
    budget = SpyBudget()
    factory = make_model_factory_scripted(
        [
            [_output(research_output(research_pack, tamper=True))],
            [_output(research_output(research_pack))],
        ],
        [],
    )
    result = await _run(
        research_spec,
        research_pack,
        seed_registry,
        allow_all_permit,
        fake_tiers_config,
        factory,
        budget,
    )
    assert result.verdict is RunVerdict.PASS
    assert budget.reserved_ids == budget.settled_ids == ["job-allowance"]
    assert [a.part for a in result.attempts] == [1, 1]


def test_a_failed_repair_is_described_by_its_validation_problem(
    research_spec: AgentSpec,
) -> None:
    try:
        TypeAdapter(research_spec.output_type()).validate_python({"wrong": []})
    except ValidationError as cause:
        exc = UnexpectedModelBehavior("Exceeded maximum output retries (1)")
        exc.__cause__ = cause
    members = frozenset(t.__name__ for t in research_spec.output_types)
    text = describe_failure(exc, members)
    assert text.startswith("Exceeded maximum output retries (1): ")
    assert "field required" in text.split(": ", 1)[1].lower()
