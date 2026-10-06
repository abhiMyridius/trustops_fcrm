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
from fcrm_agents.runtime import TOOL_CAP_MESSAGE, request_limit
from fcrm_agents.spec import AgentSpec
from fcrm_contracts.enums import Domain
from fcrm_contracts.proposals.provenance import ClauseSourceRef
from fcrm_contracts.proposals.research import ClauseProposal
from fcrm_guardrails import CaseBudget, PermitGuard
from fcrm_tools import DEMO_CASE_ID, ToolRegistry
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo

SEARCH_ARGS: dict[str, Any] = {
    "query": "cash pickup agent network",
    "top_k": 3,
    "filters": {"corpus_version": 14},
}


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
    [(None, 1), (12, 1), (1, 1), (0, 0)],
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
    assert research_spec.tool_call_cap == 1
    assert run.tool_call_cap == expected


def test_the_agents_whose_pack_is_complete_are_offered_no_tool() -> None:
    from fcrm_agents.spec import REGISTRY
    from fcrm_contracts.enums import AgentName

    for name in (
        AgentName.SCREENING,
        AgentName.INTAKE,
        AgentName.WATCHLIST,
        AgentName.EXAMINER,  # the pack holds the chain; a round trip re-sent all of it
    ):
        assert REGISTRY[name].tool_call_cap == 0, name


def test_every_other_agent_may_make_one_tool_call() -> None:
    from fcrm_agents.spec import REGISTRY

    caps = {a.value: s.tool_call_cap for a, s in REGISTRY.items()}
    assert {n: c for n, c in caps.items() if c} == dict.fromkeys(
        ("research", "assessment", "critic", "narrative", "policy_gap"), 1
    )


@pytest.mark.parametrize(("cap", "limit"), [(0, 1), (1, 3), (2, 4)])
def test_the_request_limit_leaves_room_to_answer_after_a_refused_call(
    cap: int, limit: int
) -> None:
    assert request_limit(cap) == limit


async def test_a_tool_call_over_the_cap_is_refused_and_the_model_still_answers(
    research_pack: ContextPack,
    research_spec: AgentSpec,
    fake_tiers_config: dict[str, Any],
    seed_registry: ToolRegistry,
    allow_all_permit: PermitGuard,
    make_model_factory_scripted: Callable[..., ModelFactory],
) -> None:
    """The model calls a second tool the cap does not allow: the call is answered
    with an error (it is not run), and the job goes on to a valid answer. A hard
    request limit would have ended the job and thrown the work away."""
    bare = research_pack.model_copy(update={"sources": []})
    returns: list[Any] = []

    def search(info: AgentInfo, messages: list[Any]) -> ModelResponse:
        return ModelResponse(
            parts=[ToolCallPart(tool_name="policy_search", args=SEARCH_ARGS)]
        )

    def answer_from_the_first_result(
        info: AgentInfo, messages: list[Any]
    ) -> ModelResponse:
        returns.extend(
            part.content
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        )
        hit = next(h for h in returns[0]["hits"] if h["metadata"].get("quotable"))
        clause = ClauseProposal(
            source_ref=ClauseSourceRef(clause_id=hit["id"], corpus_v=14),
            quote=hit["text"].strip("…").strip(),
            confidence=0.9,
            clause_id=hit["id"],
            domain=Domain.CHANNEL,
            applicability="the partner pays out through an agent network",
        )
        tool = info.output_tools[0]
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name=tool.name,
                    args={"response": [clause.model_dump(mode="json")]},
                )
            ]
        )

    result = await _run(
        research_spec,
        bare,
        seed_registry,
        allow_all_permit,
        fake_tiers_config,
        make_model_factory_scripted(
            [[search, search, answer_from_the_first_result]], []
        ),
    )
    assert result.verdict.value == "pass"
    assert result.attempts[0].tools_called == ["policy_search", "policy_search"]
    assert returns[1] == {"error": TOOL_CAP_MESSAGE}
    assert [type(i) for i in result.accepted] == [ClauseProposal]


def _validation_failure() -> Exception:
    """What Pydantic AI raises when an output tool call fails validation: its own
    wrapper, with the ``ValidationError`` as the cause.
    """
    from pydantic import BaseModel, ValidationError
    from pydantic_ai import UnexpectedModelBehavior

    class Field(BaseModel):
        source_ref: dict[str, str]

    class Answer(BaseModel):
        fields: list[Field]

    try:
        Answer.model_validate({"fields": [{"source_ref": {}}, {"source_ref": None}]})
    except ValidationError as err:
        failure = UnexpectedModelBehavior("Exceeded maximum output retries (0)")
        failure.__cause__ = err
        return failure
    raise AssertionError("the answer was meant to be invalid")


def test_a_validation_failure_is_described_by_where_and_what_not_by_the_validator() -> (
    None
):
    """The first live runs logged the repr of the validator ("function-before
    [functools.partial(...") and cut the line before the problem: the audit record
    and the retry's feedback said nothing about what to fix.
    """
    from fcrm_agent_worker.live_runtime import describe_failure

    text = describe_failure(_validation_failure())

    assert text.startswith("Exceeded maximum output retries (0): ")
    assert "fields.1.source_ref: Input should be a valid dictionary" in text
    assert "function-" not in text
    assert "ValidationError" not in text
