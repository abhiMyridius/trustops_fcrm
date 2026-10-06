"""Which tools a live run offers the model, and how many round trips it allows.

The first live runs showed the cheap model calling a tool it did not need on
every intake row, which tripled the tokens of the request (17k against 6k) for
no change in the answer. An agent whose ``tool_call_cap`` is 0 has a complete
pack and is offered no tool; H2's ``AGENT_MAX_TOOL_CALLS`` setting can lower an
agent's cap but never raise it (it used to replace it, so every agent got the
setting's 12)."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest
from fcrm_agent_worker.live_runtime import LiveRun, ModelFactory, run_agent_live
from fcrm_agents.context_pack import ContextPack
from fcrm_agents.spec import AgentSpec
from fcrm_guardrails import CaseBudget, PermitGuard
from fcrm_tools import DEMO_CASE_ID, ToolRegistry
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo


def _no_tools_offered(
    seen: list[list[str]],
) -> Callable[[AgentInfo, list[Any]], ModelResponse]:
    def reply(info: AgentInfo, messages: list[Any]) -> ModelResponse:
        seen.append([t.name for t in info.function_tools])
        tool = info.output_tools[0]
        properties = tool.parameters_json_schema.get("properties", {})
        key = next(iter(properties)) if len(properties) == 1 else "response"
        return ModelResponse(parts=[ToolCallPart(tool_name=tool.name, args={key: []})])

    return reply


async def _run(
    spec: AgentSpec,
    pack: ContextPack,
    registry: ToolRegistry,
    permit: PermitGuard,
    tiers: dict[str, Any],
    factory: ModelFactory,
    cap: int | None = None,
) -> Any:
    result, _model, _usage = await run_agent_live(
        spec,
        pack,
        "job-tools",
        registry=registry,
        permit=permit,
        budget=CaseBudget(DEMO_CASE_ID),
        instructions=None,
        tiers_config=tiers,
        model_factory=factory,
        tool_call_cap=cap,
    )
    return result


async def test_an_agent_with_cap_zero_is_offered_no_tool(
    research_pack: ContextPack,
    research_spec: AgentSpec,
    fake_tiers_config: dict[str, Any],
    seed_registry: ToolRegistry,
    allow_all_permit: PermitGuard,
    make_model_factory_scripted: Callable[..., ModelFactory],
) -> None:
    seen: list[list[str]] = []
    capped = research_spec.model_copy(update={"tool_call_cap": 0})
    await _run(
        capped,
        research_pack,
        seed_registry,
        allow_all_permit,
        fake_tiers_config,
        make_model_factory_scripted([[_no_tools_offered(seen)]], []),
    )
    assert seen == [[]]


async def test_the_output_tool_schema_the_model_sees_is_compact(
    research_pack: ContextPack,
    research_spec: AgentSpec,
    fake_tiers_config: dict[str, Any],
    seed_registry: ToolRegistry,
    allow_all_permit: PermitGuard,
    make_model_factory_scripted: Callable[..., ModelFactory],
) -> None:
    """Title keywords repeat the field name on every property; 200 to 300 tokens
    of every request of every agent. The contract is still validated whole."""
    shown: list[str] = []

    def record(info: AgentInfo, messages: list[Any]) -> ModelResponse:
        shown.append(json.dumps(info.output_tools[0].parameters_json_schema))
        return _no_tools_offered([])(info, messages)

    await _run(
        research_spec,
        research_pack,
        seed_registry,
        allow_all_permit,
        fake_tiers_config,
        make_model_factory_scripted([[record]], []),
    )
    (schema,) = shown
    assert '"title"' not in schema
    assert '"default": null' not in schema
    assert "ClauseProposal" in schema


async def test_an_agent_with_a_cap_is_offered_its_tools(
    research_pack: ContextPack,
    research_spec: AgentSpec,
    fake_tiers_config: dict[str, Any],
    seed_registry: ToolRegistry,
    allow_all_permit: PermitGuard,
    make_model_factory_scripted: Callable[..., ModelFactory],
) -> None:
    seen: list[list[str]] = []
    await _run(
        research_spec,
        research_pack,
        seed_registry,
        allow_all_permit,
        fake_tiers_config,
        make_model_factory_scripted([[_no_tools_offered(seen)]], []),
    )
    assert seen == [list(research_spec.tools)]


async def test_the_h2_setting_lowers_an_agents_cap_to_zero_and_offers_no_tool(
    research_pack: ContextPack,
    research_spec: AgentSpec,
    fake_tiers_config: dict[str, Any],
    seed_registry: ToolRegistry,
    allow_all_permit: PermitGuard,
    make_model_factory_scripted: Callable[..., ModelFactory],
) -> None:
    seen: list[list[str]] = []
    await _run(
        research_spec,
        research_pack,
        seed_registry,
        allow_all_permit,
        fake_tiers_config,
        make_model_factory_scripted([[_no_tools_offered(seen)]], []),
        cap=0,
    )
    assert seen == [[]]


@pytest.mark.parametrize(
    ("setting", "expected"),
    [(None, 3), (12, 3), (3, 3), (2, 2), (0, 0)],
)
def test_the_h2_setting_can_lower_an_agents_cap_but_never_raise_it(
    research_pack: ContextPack,
    research_spec: AgentSpec,
    fake_tiers_config: dict[str, Any],
    seed_registry: ToolRegistry,
    allow_all_permit: PermitGuard,
    setting: int | None,
    expected: int,
) -> None:
    run = LiveRun(
        research_spec,
        research_pack,
        "job-cap",
        registry=seed_registry,
        permit=allow_all_permit,
        budget=CaseBudget(DEMO_CASE_ID),
        instructions=None,
        tiers_config=fake_tiers_config,
        tool_call_cap=setting,
    )
    assert research_spec.tool_call_cap == 3
    assert run.tool_call_cap == expected


def test_the_cheap_tier_agents_are_offered_no_tool() -> None:
    from fcrm_agents.spec import REGISTRY
    from fcrm_contracts.enums import AgentName

    for name in (AgentName.SCREENING, AgentName.INTAKE, AgentName.WATCHLIST):
        assert REGISTRY[name].tool_call_cap == 0, name
