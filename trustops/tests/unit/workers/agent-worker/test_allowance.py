"""The output allowance of each model request (ADR-80).

- ``max_tokens`` is what the attempt's budget leaves after what it spent and
  the request's estimated input, never above the tier's cap and never below the
  floor.
- The first request is estimated from its characters; a later one from the
  input the provider measured for the previous request.
- The repair of an answer cut off at its allowance carries a brevity note.
"""

from __future__ import annotations

import json
from math import ceil
from typing import Any

from fcrm_agent_worker.allowance import (
    CHARS_PER_TOKEN,
    OUTPUT_FLOOR_TOKENS,
    REQUEST_OVERHEAD_TOKENS,
    OutputAllowanceModel,
    RequestAllowance,
    estimate_prompt_tokens,
    estimate_request_tokens,
    output_allowance,
)
from pydantic import BaseModel
from pydantic_ai import Agent, AgentRetries, ToolOutput
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.usage import RequestUsage

NOTE = "answer more briefly"


class Answer(BaseModel):
    """A one-field answer for the scripted agents below."""

    text: str


def test_a_roomy_budget_gives_the_tiers_whole_cap() -> None:
    assert (
        output_allowance(
            attempt_limit=24_000,
            spent=0,
            estimated_input=3_000,
            tier_max=5_000,
            floor=1_024,
        )
        == 5_000
    )


def test_a_tight_budget_gives_what_is_left() -> None:
    assert (
        output_allowance(
            attempt_limit=24_000,
            spent=12_000,
            estimated_input=9_000,
            tier_max=5_000,
            floor=1_024,
        )
        == 3_000
    )


def test_a_spent_budget_still_gets_the_floor() -> None:
    assert (
        output_allowance(
            attempt_limit=24_000,
            spent=20_000,
            estimated_input=9_000,
            tier_max=5_000,
            floor=1_024,
        )
        == OUTPUT_FLOOR_TOKENS
    )


def test_the_floor_never_exceeds_the_tiers_cap() -> None:
    assert (
        output_allowance(
            attempt_limit=1_000,
            spent=0,
            estimated_input=2_000,
            tier_max=800,
            floor=1_024,
        )
        == 800
    )


def test_a_request_is_estimated_from_its_messages_instructions_and_tools() -> None:
    text = "x" * 3_600
    tool = ToolDefinition(
        name="lookup",
        description="find it",
        parameters_json_schema={"type": "object", "properties": {"q": {}}},
    )
    messages: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart(text)], instructions="i" * 360)
    ]
    schema = {
        "name": "lookup",
        "description": "find it",
        "parameters": tool.parameters_json_schema,
    }
    schema_chars = len(json.dumps(schema, separators=(",", ":")))
    estimated = estimate_request_tokens(
        messages, ModelRequestParameters(function_tools=[tool])
    )
    assert estimated == (
        ceil((3_600 + 360 + schema_chars) / CHARS_PER_TOKEN) + REQUEST_OVERHEAD_TOKENS
    )


def test_a_first_request_is_estimated_before_it_is_built() -> None:
    schema = {"type": "object"}
    chars = 720 + 36 + len(json.dumps(schema, separators=(",", ":")))
    assert (
        estimate_prompt_tokens("a" * 720, None, "b" * 36, schemas=[schema])
        == ceil(chars / CHARS_PER_TOKEN) + REQUEST_OVERHEAD_TOKENS
    )


def test_an_allowance_record_names_no_token_key() -> None:
    """The record is logged and audited: the log scrubber drops any key with
    "token" in it."""
    record = RequestAllowance(
        estimated_input=1, allowance=2, actual_input=3, actual_output=4
    )
    assert record.as_record() == {
        "estimated_input": 1,
        "allowance": 2,
        "actual_input": 3,
        "actual_output": 4,
    }
    assert not any("token" in key for key in record.as_record())


def _scripted(replies: list[Any], seen: list[int | None]) -> FunctionModel:
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append((info.model_settings or {}).get("max_tokens"))
        responses = sum(1 for m in messages if isinstance(m, ModelResponse))
        return replies[min(responses, len(replies) - 1)](info, messages)

    return FunctionModel(respond, model_name="scripted-allowance")


def _tool_call(info: AgentInfo, messages: list[ModelMessage]) -> ModelResponse:
    return ModelResponse(
        parts=[ToolCallPart(tool_name="lookup", args={"q": "x"})],
        usage=RequestUsage(input_tokens=6_000, output_tokens=300),
    )


def _answer(info: AgentInfo, messages: list[ModelMessage]) -> ModelResponse:
    return ModelResponse(
        parts=[ToolCallPart(tool_name=info.output_tools[0].name, args={"text": "ok"})],
        usage=RequestUsage(input_tokens=6_400, output_tokens=50),
    )


def _agent(
    model: OutputAllowanceModel, *, output_retries: int = 0
) -> Agent[None, Answer]:
    agent: Agent[None, Answer] = Agent(
        model,
        output_type=ToolOutput(Answer, name="answer"),
        model_settings={"max_tokens": 5_000},
        retries=AgentRetries(tools=0, output=output_retries),
    )

    @agent.tool_plain
    def lookup(q: str) -> str:
        """Look something up."""
        return "found " * 100

    return agent


async def test_the_second_request_is_sized_from_the_measured_first_input() -> None:
    seen: list[int | None] = []
    model = OutputAllowanceModel(
        _scripted([_tool_call, _answer], seen), attempt_limit=15_000, tier_max=5_000
    )
    result = await _agent(model).run("question " * 50)
    assert result.output == Answer(text="ok")
    first, second = model.allowances
    assert first.allowance == 5_000
    assert (first.actual_input, first.actual_output) == (6_000, 300)
    # measured input and output of the first request + the tool result since
    assert second.estimated_input > 6_300
    assert second.estimated_input < 6_300 + 400
    assert second.allowance == 15_000 - 6_300 - second.estimated_input
    assert seen == [first.allowance, second.allowance]
    assert model.spent == 6_300 + 6_450


async def test_a_request_past_the_budget_gets_the_floor() -> None:
    seen: list[int | None] = []
    model = OutputAllowanceModel(
        _scripted([_tool_call, _answer], seen),
        attempt_limit=12_000,
        tier_max=5_000,
        floor=1_024,
    )
    await _agent(model).run("question")
    assert seen[1] == 1_024


def _cut_off(info: AgentInfo, messages: list[ModelMessage]) -> ModelResponse:
    return ModelResponse(
        parts=[ToolCallPart(tool_name=info.output_tools[0].name, args={})],
        finish_reason="length",
    )


async def test_the_repair_of_a_cut_off_answer_carries_the_brevity_note() -> None:
    notes: list[bool] = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        last = messages[-1]
        notes.append(
            isinstance(last, ModelRequest)
            and any(
                isinstance(p, UserPromptPart) and p.content == NOTE for p in last.parts
            )
        )
        responses = sum(1 for m in messages if isinstance(m, ModelResponse))
        return (_cut_off if responses == 0 else _answer)(info, messages)

    model = OutputAllowanceModel(
        FunctionModel(respond, model_name="scripted-cut-off"),
        attempt_limit=24_000,
        tier_max=5_000,
        cut_off_note=NOTE,
    )
    agent: Agent[None, Answer] = Agent(
        model,
        output_type=ToolOutput(Answer, name="answer"),
        retries=AgentRetries(tools=0, output=1),
    )
    with_history = await agent.run("question")
    assert with_history.output == Answer(text="ok")
    assert notes == [False, True]
    # the run's own history is left as the model was asked it
    assert not any(
        isinstance(p, UserPromptPart) and p.content == NOTE
        for m in with_history.all_messages()
        if isinstance(m, ModelRequest)
        for p in m.parts
    )


async def test_an_ordinary_repair_carries_no_note() -> None:
    notes: list[bool] = []

    def bad(info: AgentInfo, messages: list[ModelMessage]) -> ModelResponse:
        return ModelResponse(parts=[TextPart("I think the answer is ok")])

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        notes.append(
            any(
                isinstance(p, UserPromptPart) and p.content == NOTE
                for m in messages
                if isinstance(m, ModelRequest)
                for p in m.parts
            )
        )
        responses = sum(1 for m in messages if isinstance(m, ModelResponse))
        return (bad if responses == 0 else _answer)(info, messages)

    model = OutputAllowanceModel(
        FunctionModel(respond, model_name="scripted-text"),
        attempt_limit=24_000,
        tier_max=5_000,
        cut_off_note=NOTE,
    )
    agent: Agent[None, Answer] = Agent(
        model,
        output_type=ToolOutput(Answer, name="answer"),
        retries=AgentRetries(tools=0, output=1),
    )
    assert (await agent.run("question")).output == Answer(text="ok")
    assert notes == [False, False]


def test_the_wrapper_names_the_model_it_wraps() -> None:
    inner = FunctionModel(lambda m, i: ModelResponse(parts=[]), model_name="inner-name")
    model = OutputAllowanceModel(inner, attempt_limit=1, tier_max=1)
    assert model.model_name == "inner-name"
