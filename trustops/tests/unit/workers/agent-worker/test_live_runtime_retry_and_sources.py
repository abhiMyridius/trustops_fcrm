"""The live runtime's retry and its citations.

- A retry is shown why the last answer was refused (ADR-14) and keeps the
  tier's whole output allowance (a thinking model given half of it spends it
  thinking and returns an empty tool call).
- A citation resolves against the pack **and** the tool results this model
  call received, and against nothing else: not a tool it did not call, not a
  result an earlier attempt saw.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fcrm_agent_worker.live_runtime import RETRY_HEADER, ModelFactory, run_agent_live
from fcrm_agents.context_pack import ContextPack
from fcrm_agents.demo import research_output
from fcrm_agents.runtime import RunVerdict
from fcrm_agents.spec import AgentSpec
from fcrm_contracts.enums import Domain
from fcrm_contracts.proposals.provenance import ClauseSourceRef
from fcrm_contracts.proposals.research import ClauseProposal
from fcrm_guardrails import CaseBudget, GuardName, PermitGuard
from fcrm_tools import DEMO_CASE_ID, ToolRegistry
from pydantic import BaseModel
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo

POLICY_SEARCH_ARGS: dict[str, Any] = {
    "index": "policy_clauses",
    "query": "cash pickup agent network",
    "top_k": 3,
    "filters": {"corpus_version": 14},
}


def _output(items: list[BaseModel]) -> Callable[[AgentInfo, list[ModelMessage]], Any]:
    payload = [item.model_dump(mode="json") for item in items]

    def reply(info: AgentInfo, messages: list[ModelMessage]) -> ModelResponse:
        tool = info.output_tools[0]
        properties = tool.parameters_json_schema.get("properties", {})
        key = next(iter(properties)) if len(properties) == 1 else "response"
        return ModelResponse(
            parts=[ToolCallPart(tool_name=tool.name, args={key: payload})]
        )

    return reply


def _call(name: str, args: dict[str, Any]) -> Callable[[AgentInfo, Any], Any]:
    def reply(info: AgentInfo, messages: list[ModelMessage]) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart(tool_name=name, args=args)])

    return reply


def _clause_from_the_tool_result(
    quote_tail: str = "",
) -> Callable[[AgentInfo, list[ModelMessage]], Any]:
    """Propose the first quotable clause the last tool call returned (what a
    model that read its tool result and cites it does); ``quote_tail`` is
    appended to the quote to make it no longer verbatim."""

    def reply(info: AgentInfo, messages: list[ModelMessage]) -> ModelResponse:
        returned = [
            part.content
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        hit = next(h for h in returned[-1]["hits"] if h["metadata"].get("quotable"))
        clause = ClauseProposal(
            source_ref=ClauseSourceRef(clause_id=hit["id"], corpus_v=14),
            quote=hit["text"].strip("…").strip() + quote_tail,
            confidence=0.9,
            clause_id=hit["id"],
            domain=Domain.CHANNEL,
            applicability="the partner pays out through an agent network",
        )
        return _output([clause])(info, messages)

    return reply


async def _run(
    spec: AgentSpec,
    pack: ContextPack,
    registry: ToolRegistry,
    permit: PermitGuard,
    tiers: dict[str, Any],
    factory: ModelFactory,
) -> Any:
    result, _model, _usage = await run_agent_live(
        spec,
        pack,
        "job-retry-sources",
        registry=registry,
        permit=permit,
        budget=CaseBudget(DEMO_CASE_ID),
        instructions=None,
        tiers_config=tiers,
        model_factory=factory,
    )
    return result


async def test_a_retry_keeps_the_tiers_whole_output_allowance(
    research_pack: ContextPack,
    research_spec: AgentSpec,
    fake_tiers_config: dict[str, Any],
    seed_registry: ToolRegistry,
    allow_all_permit: PermitGuard,
    make_model_factory_scripted: Callable[..., ModelFactory],
) -> None:
    seen: list[Any] = []
    tampered = research_output(research_pack, tamper=True)
    clean = research_output(research_pack)
    factory = make_model_factory_scripted([[_output(tampered)], [_output(clean)]], seen)
    result = await _run(
        research_spec,
        research_pack,
        seed_registry,
        allow_all_permit,
        fake_tiers_config,
        factory,
    )
    assert result.verdict is RunVerdict.PASS
    assert [s.attempt for s in seen] == [1, 2]
    allowance = fake_tiers_config["tiers"]["strong"]["max_tokens"]
    assert [s.max_tokens for s in seen] == [allowance, allowance]


async def test_a_retry_is_shown_why_the_first_answer_was_refused(
    research_pack: ContextPack,
    research_spec: AgentSpec,
    fake_tiers_config: dict[str, Any],
    seed_registry: ToolRegistry,
    allow_all_permit: PermitGuard,
    make_model_factory_scripted: Callable[..., ModelFactory],
) -> None:
    seen: list[Any] = []
    factory = make_model_factory_scripted(
        [
            [_output(research_output(research_pack, tamper=True))],
            [_output(research_output(research_pack))],
        ],
        seen,
    )
    await _run(
        research_spec,
        research_pack,
        seed_registry,
        allow_all_permit,
        fake_tiers_config,
        factory,
    )
    first, second = seen
    assert RETRY_HEADER not in first.user_text
    assert RETRY_HEADER in second.user_text
    assert "quote_verbatim failed" in second.user_text
    assert second.user_text.startswith(first.user_text)


async def test_a_clean_first_attempt_gets_no_retry_text(
    research_pack: ContextPack,
    research_spec: AgentSpec,
    fake_tiers_config: dict[str, Any],
    seed_registry: ToolRegistry,
    allow_all_permit: PermitGuard,
    make_model_factory_scripted: Callable[..., ModelFactory],
) -> None:
    seen: list[Any] = []
    factory = make_model_factory_scripted(
        [[_output(research_output(research_pack))]], seen
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
    assert len(seen) == 1
    assert RETRY_HEADER not in seen[0].user_text


async def test_a_clause_a_tool_returned_is_a_citable_source(
    research_pack: ContextPack,
    research_spec: AgentSpec,
    fake_tiers_config: dict[str, Any],
    seed_registry: ToolRegistry,
    allow_all_permit: PermitGuard,
    make_model_factory_scripted: Callable[..., ModelFactory],
) -> None:
    """The pack holds no clause source at all, so the only thing the quote can
    resolve against is the ``policy_search`` result the model received."""
    bare = research_pack.model_copy(update={"sources": []})
    seen: list[Any] = []
    factory = make_model_factory_scripted(
        [[_call("policy_search", POLICY_SEARCH_ARGS), _clause_from_the_tool_result()]],
        seen,
    )
    result = await _run(
        research_spec,
        bare,
        seed_registry,
        allow_all_permit,
        fake_tiers_config,
        factory,
    )
    assert result.verdict is RunVerdict.PASS
    assert [type(item) for item in result.accepted] == [ClauseProposal]
    assert [s.round_trip for s in seen] == [0, 1]


async def test_a_clause_the_agent_was_never_given_does_not_resolve(
    research_pack: ContextPack,
    research_spec: AgentSpec,
    fake_tiers_config: dict[str, Any],
    seed_registry: ToolRegistry,
    allow_all_permit: PermitGuard,
    make_model_factory_scripted: Callable[..., ModelFactory],
) -> None:
    """The same proposal without the tool call cites something no one gave the
    agent: the rule that a citation must resolve to what it was shown holds."""
    bare = research_pack.model_copy(update={"sources": []})
    seen: list[Any] = []
    never_called = _output(
        [
            ClauseProposal(
                source_ref=ClauseSourceRef(clause_id="TP71-2.3", corpus_v=14),
                quote="the bank shall obtain an independent assessment",
                confidence=0.9,
                clause_id="TP71-2.3",
                domain=Domain.CHANNEL,
                applicability="the partner pays out through an agent network",
            )
        ]
    )
    result = await _run(
        research_spec,
        bare,
        seed_registry,
        allow_all_permit,
        fake_tiers_config,
        make_model_factory_scripted([[never_called]], seen),
    )
    assert result.verdict is RunVerdict.BLOCKED
    assert result.stopped_at is GuardName.OUTPUT_SHAPE


async def test_a_tool_result_of_an_earlier_attempt_is_not_citable_in_the_next(
    research_pack: ContextPack,
    research_spec: AgentSpec,
    fake_tiers_config: dict[str, Any],
    seed_registry: ToolRegistry,
    allow_all_permit: PermitGuard,
    make_model_factory_scripted: Callable[..., ModelFactory],
) -> None:
    """Attempt 1 reads the clause and quotes it wrongly; attempt 2 quotes it
    right but never called the tool, so the retry has been given nothing to
    cite and is blocked."""
    bare = research_pack.model_copy(update={"sources": []})
    seen: list[Any] = []
    first_attempt = [
        _call("policy_search", POLICY_SEARCH_ARGS),
        _clause_from_the_tool_result(" and then some words that are not in it"),
    ]
    second_attempt = [
        _output(
            [
                ClauseProposal(
                    source_ref=ClauseSourceRef(clause_id="TP71-2.3", corpus_v=14),
                    quote="the bank shall obtain an independent assessment",
                    confidence=0.9,
                    clause_id="TP71-2.3",
                    domain=Domain.CHANNEL,
                    applicability="the partner pays out through an agent network",
                )
            ]
        )
    ]
    result = await _run(
        research_spec,
        bare,
        seed_registry,
        allow_all_permit,
        fake_tiers_config,
        make_model_factory_scripted([first_attempt, second_attempt], seen),
    )
    assert result.verdict is RunVerdict.BLOCKED
    assert len(result.attempts) == 2


def _malformed(info: AgentInfo, messages: list[ModelMessage]) -> ModelResponse:
    """A call of the output tool whose argument is not a list: the model spent its
    tokens and the answer is unusable (the live runs' "30 validation errors")."""
    tool = info.output_tools[0]
    return ModelResponse(
        parts=[ToolCallPart(tool_name=tool.name, args={"response": "not a list"})]
    )


async def test_a_refused_attempts_tokens_are_charged_to_the_job(
    research_pack: ContextPack,
    research_spec: AgentSpec,
    fake_tiers_config: dict[str, Any],
    seed_registry: ToolRegistry,
    allow_all_permit: PermitGuard,
    make_model_factory_scripted: Callable[..., ModelFactory],
) -> None:
    """Attempt 1 is a model call that returns an unusable answer, attempt 2 a
    good one. Both spent tokens: the attempt record, the job's total, the usage
    H1 sums into the case and the case ledger all hold both."""
    budget = CaseBudget(DEMO_CASE_ID)
    result, _model, usage = await run_agent_live(
        research_spec,
        research_pack,
        "job-billing",
        registry=seed_registry,
        permit=allow_all_permit,
        budget=budget,
        instructions=None,
        tiers_config=fake_tiers_config,
        model_factory=make_model_factory_scripted(
            [[_malformed], [_output(research_output(research_pack))]], []
        ),
    )
    first, second = result.attempts
    assert result.verdict is RunVerdict.PASS
    assert first.tokens_used > 0
    assert second.tokens_used > 0
    assert result.tokens_used == first.tokens_used + second.tokens_used
    assert usage["input_tokens"] + usage["output_tokens"] == result.tokens_used
    assert budget.snapshot().per_agent["research"].tokens == result.tokens_used
