"""The live runtime's scenarios, for each of the nine agents (ids ``S<n>-<agent>``).

Every job runs the real :mod:`fcrm_agent_worker.live_runtime` (or, for a split
input, :func:`fcrm_agent_worker.parts.run_agent_parts`) on the demo case's pack,
with a scripted ``FunctionModel`` answering what a well-behaved model would
(:func:`fcrm_agents.demo.demo_output`): no Bedrock (ADR-59).

- S1 a clean first attempt; S2 a Guard 4 refusal, then a pass on attempt 2;
  S3 the list sent as hand-written JSON text; S4 an answer cut off at its
  output allowance; S5 an input split into parts; S6 an empty answer; S7 a tool
  round trip whose result is cited; S8 a fallback-chain failure or timeout;
  S9 a budget tip-over kept, and a large overrun blocked; S10 the in-run repair,
  and no repair when the budget cannot hold one.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Optional

import pytest
from fcrm_agent_worker import live_runtime
from fcrm_agent_worker.allowance import OUTPUT_FLOOR_TOKENS
from fcrm_agent_worker.live_runtime import (
    CUT_OFF_FEEDBACK,
    EMPTY_ANSWER_FEEDBACK,
    RETRY_HEADER,
    USAGE_KEYS,
    LiveRun,
    ModelFactory,
    run_agent_live,
)
from fcrm_agent_worker.parts import PartsOutcome, parts_summary, run_agent_parts
from fcrm_agent_worker.reduce import PARTIAL_ANSWERS, REDUCE_PART
from fcrm_agents.builders import builder_for
from fcrm_agents.consolidate import DEDUPE_KEY, SINGLETON_AGENTS
from fcrm_agents.context_pack import ContextPack
from fcrm_agents.demo import demo_output, demo_request
from fcrm_agents.runtime import FIXED_ARGUMENTS, AgentRunResult, RunVerdict
from fcrm_agents.spec import REGISTRY
from fcrm_agents.tool_sources import sources_from_result
from fcrm_contracts.enums import AgentName, Tier
from fcrm_contracts.proposals.examiner import ExaminerAnswer
from fcrm_contracts.proposals.provenance import RecordSourceRef
from fcrm_guardrails import CaseBudget, GuardName, GuardStatus, PermitGuard
from fcrm_guardrails.budget import TIER_BUDGETS, AttemptPlan, ModelSlot
from fcrm_guardrails.context import AvailableSource
from fcrm_tools import DEMO_CASE_ID, ToolRegistry
from pydantic import BaseModel
from pydantic_ai.exceptions import FallbackExceptionGroup, ModelHTTPError
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo
from pydantic_ai.usage import RequestUsage

AGENTS = list(AgentName)
JOB = "job-scenario"

Reply = Callable[[AgentInfo, list[ModelMessage]], Any]


def ids(n: int) -> list[str]:
    """``S<n>-<agent>`` for each agent."""
    return [f"S{n}-{agent.value}" for agent in AGENTS]


# --- replies --------------------------------------------------------------------


def _dumped(items: list[BaseModel]) -> list[dict[str, Any]]:
    return [item.model_dump(mode="json") for item in items]


def _call(info: AgentInfo, args: Any, **response: Any) -> ModelResponse:
    tool = info.output_tools[0].name
    return ModelResponse(parts=[ToolCallPart(tool_name=tool, args=args)], **response)


def answer(items: list[BaseModel], usage: Optional[RequestUsage] = None) -> Reply:
    """The output tool called with ``items`` as a JSON array."""
    payload = _dumped(items)

    def reply(info: AgentInfo, messages: list[ModelMessage]) -> ModelResponse:
        if usage is None:
            return _call(info, {"response": payload})
        return _call(info, {"response": payload}, usage=usage)

    return reply


def answer_text(text: str) -> Reply:
    """The output tool called with its list written as text."""

    def reply(info: AgentInfo, messages: list[ModelMessage]) -> ModelResponse:
        return _call(info, {"response": text})

    return reply


def invalid(info: AgentInfo, messages: list[ModelMessage]) -> ModelResponse:
    """An output-tool call without its list."""
    return _call(info, {}, usage=RequestUsage(input_tokens=300, output_tokens=20))


def cut_off(info: AgentInfo, messages: list[ModelMessage]) -> ModelResponse:
    """An answer stopped at ``max_tokens`` mid tool call (what Bedrock returns)."""
    return _call(info, {}, finish_reason="length")


def raising(exc: Callable[[], BaseException]) -> Reply:
    """A model call that fails with ``exc()``."""

    def reply(info: AgentInfo, messages: list[ModelMessage]) -> ModelResponse:
        raise exc()

    return reply


def chain_failed() -> FallbackExceptionGroup:
    """Every model of the attempt's fallback chain failed."""
    return FallbackExceptionGroup(
        "All models from FallbackModel failed",
        [ModelHTTPError(status_code=503, model_name="fake", body="ServiceUnavailable")],
    )


def read_timeout() -> TimeoutError:
    """A transport read timeout."""
    return TimeoutError("read timed out")


def tool_call(name: str, args: dict[str, Any]) -> Reply:
    """A call of the function tool ``name``."""

    def reply(info: AgentInfo, messages: list[ModelMessage]) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart(tool_name=name, args=args)])

    return reply


# --- jobs -----------------------------------------------------------------------


async def demo_pack(
    agent: AgentName, registry: ToolRegistry, mode: Optional[str] = None
) -> ContextPack:
    """The demo case's pack for ``agent`` (narrative: completeness unless
    ``mode`` says otherwise)."""
    return await builder_for(agent).build(demo_request(agent, mode), registry)


class Job:
    """One agent's job on the demo pack, with the fixtures it runs against."""

    def __init__(
        self,
        agent: AgentName,
        registry: ToolRegistry,
        permit: PermitGuard,
        tiers: dict[str, Any],
        scripted: Callable[..., ModelFactory],
    ) -> None:
        self.agent = agent
        self.spec = REGISTRY[agent]
        self.registry = registry
        self.permit = permit
        self.tiers = tiers
        self.scripted = scripted
        self.seen: list[Any] = []
        self.budget = CaseBudget(DEMO_CASE_ID)

    @property
    def tier_max(self) -> int:
        """The fake tier's output cap per request."""
        return int(self.tiers["tiers"][Tier(self.spec.tier).value]["max_tokens"])

    @property
    def limit(self) -> int:
        """The attempt budget of the agent's tier."""
        return TIER_BUDGETS[Tier(self.spec.tier)].tokens

    async def run(
        self, pack: ContextPack, per_attempt: list[list[Reply]]
    ) -> tuple[AgentRunResult, str, dict[str, int]]:
        """``pack`` through ``run_agent_live`` on the scripted model."""
        return await run_agent_live(
            self.spec,
            pack,
            JOB,
            registry=self.registry,
            permit=self.permit,
            budget=self.budget,
            instructions=None,
            tiers_config=self.tiers,
            model_factory=self.scripted(per_attempt, self.seen),
        )

    async def run_parts(
        self, packs: list[ContextPack], per_attempt: list[list[Reply]]
    ) -> tuple[PartsOutcome, list[Any]]:
        """``packs`` through ``run_agent_parts``; also returns the tier models
        each attempt's chain was built for."""
        chains: list[Any] = []
        factory = self.scripted(per_attempt, self.seen)

        def recording(models: Any, plan: AttemptPlan) -> Any:
            chains.append(models)
            return factory(models, plan)

        outcome = await run_agent_parts(
            self.spec,
            packs,
            JOB,
            registry=self.registry,
            permit=self.permit,
            budget=self.budget,
            instructions=None,
            tiers_config=self.tiers,
            model_factory=recording,
            reduce_tier=Tier.CHEAP,
        )
        return outcome, chains


@pytest.fixture
def job_for(
    seed_registry: ToolRegistry,
    allow_all_permit: PermitGuard,
    fake_tiers_config: dict[str, Any],
    make_model_factory_scripted: Callable[..., ModelFactory],
) -> Callable[[AgentName], Job]:
    def make(agent: AgentName) -> Job:
        return Job(
            agent,
            seed_registry,
            allow_all_permit,
            fake_tiers_config,
            make_model_factory_scripted,
        )

    return make


def passed(result: AgentRunResult) -> bool:
    """PASS or PASS_WITH_DROPS."""
    return result.verdict in (RunVerdict.PASS, RunVerdict.PASS_WITH_DROPS)


def last_guard(result: AgentRunResult, attempt: int = 0) -> Any:
    """The last guard result of attempt ``attempt`` (0-based)."""
    return result.attempts[attempt].guard_results[-1]


# --- S1 clean first attempt -------------------------------------------------------


@pytest.mark.parametrize("agent", AGENTS, ids=ids(1))
async def test_a_clean_first_attempt(
    agent: AgentName, seed_registry: ToolRegistry, job_for: Callable[[AgentName], Job]
) -> None:
    job = job_for(agent)
    pack = await demo_pack(agent, seed_registry)
    items = demo_output(agent, pack)
    result, model_used, usage = await job.run(pack, [[answer(items)]])

    assert result.verdict is RunVerdict.PASS, result.reason
    assert len(result.attempts) == 1
    assert _dumped(result.accepted) == _dumped(items), "every item, byte for byte"
    assert set(usage) == set(USAGE_KEYS)
    assert result.tokens_used == usage["input_tokens"] + usage["output_tokens"]
    assert job.budget.used == result.tokens_used, "the reservation was settled"
    assert not job.budget.has_reservation(JOB)
    assert model_used == "scripted-1"
    assert all(RETRY_HEADER not in s.user_text for s in job.seen)


# --- S2 Guard 4 refusal, then a pass ---------------------------------------------


@pytest.mark.parametrize("agent", AGENTS, ids=ids(2))
async def test_a_refused_answer_is_retried_and_passes(
    agent: AgentName, seed_registry: ToolRegistry, job_for: Callable[[AgentName], Job]
) -> None:
    job = job_for(agent)
    pack = await demo_pack(agent, seed_registry)
    tampered = demo_output(agent, pack, tamper=True)
    items = demo_output(agent, pack)
    result, _model, usage = await job.run(pack, [[answer(tampered)], [answer(items)]])

    assert result.verdict is RunVerdict.PASS, result.reason
    assert len(result.attempts) == 2
    refused = last_guard(result)
    assert refused.guard is GuardName.OUTPUT_SHAPE
    assert refused.status is GuardStatus.RETRY
    retry = [s for s in job.seen if s.attempt == 2]
    assert len(retry) == 1
    feedback = retry[0].user_text.split(RETRY_HEADER, 1)[1]
    assert "outputs[" in feedback, "the feedback names the refused item"
    assert [s.max_tokens for s in job.seen] == [job.tier_max, job.tier_max], (
        "the retry keeps the whole output allowance"
    )
    assert usage["input_tokens"] + usage["output_tokens"] == sum(
        a.tokens_used for a in result.attempts
    )
    assert result.tokens_used == job.budget.used


# --- S3 hand-written JSON text ----------------------------------------------------


def _hand_written(items: list[BaseModel]) -> dict[str, str]:
    """The list as text, the ways a model writes it."""
    payload = _dumped(items)
    compact = json.dumps(payload, ensure_ascii=False)
    junk = [{**item, "source_ref_note": None, "#": ""} for item in payload]
    return {
        "json_text": compact,
        "trailing_comma": compact[:-1] + ",]",
        "fenced": "```json\n" + json.dumps(payload, indent=2) + "\n```",
        "double_encoded": json.dumps(compact),
        "junk_empty_keys": json.dumps(junk),
    }


@pytest.mark.parametrize("agent", AGENTS, ids=ids(3))
async def test_a_list_written_as_json_text_passes_on_the_first_attempt(
    agent: AgentName, seed_registry: ToolRegistry, job_for: Callable[[AgentName], Job]
) -> None:
    pack = await demo_pack(agent, seed_registry)
    items = demo_output(agent, pack)
    for variant, text in _hand_written(items).items():
        job = job_for(agent)
        result, _model, _usage = await job.run(pack, [[answer_text(text)]])
        assert result.verdict is RunVerdict.PASS, (variant, result.reason)
        assert len(result.attempts) == 1, variant
        assert [s.round_trip for s in job.seen] == [0], f"{variant}: no repair"
        assert _dumped(result.accepted) == _dumped(items), variant


# --- S4 cut off at the output allowance -------------------------------------------


@pytest.mark.parametrize("agent", AGENTS, ids=ids(4))
async def test_a_cut_off_answer_is_repaired_inside_the_attempt(
    agent: AgentName, seed_registry: ToolRegistry, job_for: Callable[[AgentName], Job]
) -> None:
    """The budget can hold a second request, so the cut-off answer is repaired
    within attempt 1, the repair carrying the brevity note."""
    job = job_for(agent)
    pack = await demo_pack(agent, seed_registry)
    items = demo_output(agent, pack)
    result, _model, _usage = await job.run(pack, [[cut_off, answer(items)]])

    assert result.verdict is RunVerdict.PASS, result.reason
    assert len(result.attempts) == 1
    assert [(s.attempt, s.round_trip) for s in job.seen] == [(1, 0), (1, 1)]
    assert CUT_OFF_FEEDBACK not in job.seen[0].user_text
    assert CUT_OFF_FEEDBACK in job.seen[1].user_text
    assert len(result.attempts[0].allowances) == 2


@pytest.mark.parametrize("agent", AGENTS, ids=ids(4))
async def test_a_repair_cut_off_again_goes_to_a_brief_retry(
    agent: AgentName, seed_registry: ToolRegistry, job_for: Callable[[AgentName], Job]
) -> None:
    job = job_for(agent)
    pack = await demo_pack(agent, seed_registry)
    items = demo_output(agent, pack)
    result, _model, _usage = await job.run(pack, [[cut_off], [answer(items)]])

    assert result.verdict is RunVerdict.PASS, result.reason
    assert len(result.attempts) == 2
    refused = last_guard(result)
    assert refused.status is GuardStatus.RETRY
    assert refused.details["stop_reason"] == "length"
    assert "output cut off at the" in refused.summary
    retry = next(s for s in job.seen if s.attempt == 2)
    assert CUT_OFF_FEEDBACK in retry.user_text.split(RETRY_HEADER, 1)[1]
    assert "not a valid call" not in retry.user_text


# --- S5 input split into parts ----------------------------------------------------

#: Agents whose demo input has a single item in its split section (one precedent,
#: one candidate): no cap splits it, so its pack is copied into two parts, as
#: ``test_parts`` does for the recommendation.
COPIED_INTO_PARTS = (AgentName.SCREENING, AgentName.WATCHLIST)
PART_FRACTION = 0.9
"""The pack total each split agent is given: 90 % of its whole demo pack."""


async def split_packs(
    agent: AgentName, registry: ToolRegistry, mode: Optional[str] = None
) -> list[ContextPack]:
    """The demo input as two or more parts."""
    whole = await demo_pack(agent, registry, mode)
    if agent in COPIED_INTO_PARTS:
        return [
            whole.model_copy(
                update={
                    "part_index": i,
                    "part_count": 2,
                    "pack_hash": f"{whole.pack_hash}{i}",
                }
            )
            for i in (1, 2)
        ]
    builder = type(builder_for(agent))(
        pack_totals={
            tier.value: int(whole.token_estimate * PART_FRACTION) for tier in Tier
        }
    )
    packs = await builder.build_parts(demo_request(agent, mode), registry, max_parts=8)
    assert len(packs) >= 2, f"{agent.value} did not split"
    return packs


def part_answer(agent: AgentName, pack: ContextPack) -> list[BaseModel]:
    """What a model answers from one part: the demo answer, and for the
    examiner (whose demo answer is the seed's, citing the whole chain) only the
    sentences whose records the part shows."""
    items = demo_output(agent, pack)
    if agent is not AgentName.EXAMINER:
        return items
    shown = {
        s.source_ref.seq
        for s in pack.sources
        if isinstance(s.source_ref, RecordSourceRef)
    }
    kept = []
    for item in items:
        assert isinstance(item, ExaminerAnswer)
        sentences = [s for s in item.sentences if set(s.record_refs) <= shown]
        if sentences:
            kept.append(item.model_copy(update={"sentences": sentences}))
    return kept


def _keys(items: list[BaseModel]) -> list[Any]:
    return [
        (type(item).__name__, DEDUPE_KEY[type(item)](item))
        for item in items
        if type(item) in DEDUPE_KEY
    ]


def _check_split(job: Job, packs: list[ContextPack], outcome: PartsOutcome) -> None:
    result = outcome.result
    assert passed(result), result.reason
    assert [p["part"] for p in parts_summary(outcome.parts)] == [
        p.part_index for p in packs
    ]
    for key in USAGE_KEYS:
        assert outcome.usage[key] >= sum(p.usage.get(key, 0) for p in outcome.parts)
    assert any(f"in {len(packs)} parts" in n for n in result.notes_for_analyst)
    if packs[0].sections.get("part"):
        for i in range(1, len(packs) + 1):
            assert f"Part {i} of {len(packs)}" in job.seen[i - 1].user_text


@pytest.mark.parametrize("agent", AGENTS, ids=ids(5))
async def test_a_split_input_runs_each_part_and_merges_by_code(
    agent: AgentName, seed_registry: ToolRegistry, job_for: Callable[[AgentName], Job]
) -> None:
    job = job_for(agent)
    packs = await split_packs(agent, seed_registry)
    answers = [part_answer(agent, p) for p in packs]
    outcome, _chains = await job.run_parts(packs, [[answer(a)] for a in answers])

    _check_split(job, packs, outcome)
    assert len(job.seen) == len(packs), "one request per part, no merge call"
    assert outcome.usage["input_tokens"] == sum(
        p.usage["input_tokens"] for p in outcome.parts
    )
    accepted = outcome.result.accepted
    union = [item for a in answers for item in a]
    keys = _keys(accepted)
    assert len(keys) == len(set(keys)), "no duplicate proposal survives the merge"
    if agent in SINGLETON_AGENTS:
        again, _ = await job_for(agent).run_parts(
            list(reversed(packs)), [[answer(a)] for a in reversed(answers)]
        )
        assert _dumped(again.result.accepted) == _dumped(accepted), (
            "the singleton merge does not depend on the order the parts ran in"
        )
    else:
        assert set(keys) == set(_keys(union)), "every part's proposals are kept"
        dumped = _dumped(union)
        assert all(item in dumped for item in _dumped(accepted))


async def test_a_split_recommendation_is_merged_by_one_reduce_call(
    seed_registry: ToolRegistry, job_for: Callable[[AgentName], Job]
) -> None:
    job = job_for(AgentName.NARRATIVE)
    packs = await split_packs(AgentName.NARRATIVE, seed_registry, "recommendation")
    answers = [demo_output(AgentName.NARRATIVE, p) for p in packs]
    whole = await demo_pack(AgentName.NARRATIVE, seed_registry, "recommendation")
    reduced = demo_output(AgentName.NARRATIVE, whole)
    outcome, chains = await job.run_parts(
        packs, [*([answer(a)] for a in answers), [answer(reduced)]]
    )

    _check_split(job, packs, outcome)
    result = outcome.result
    assert len(job.seen) == len(packs) + 1, "the parts, then one reduce call"
    assert [a.part for a in result.attempts][-1] == REDUCE_PART
    assert PARTIAL_ANSWERS in job.seen[-1].user_text
    cheap = job.tiers["tiers"]["cheap"]["max_tokens"]
    assert chains[-1].max_tokens == cheap, "the reduce call runs on the cheap tier"
    assert outcome.usage["input_tokens"] > sum(
        p.usage["input_tokens"] for p in outcome.parts
    )
    assert _dumped(result.accepted) == _dumped(reduced)
    assert any("consolidated" in n for n in result.notes_for_analyst)


# --- S6 empty answer ---------------------------------------------------------------


@pytest.mark.parametrize("agent", AGENTS, ids=ids(6))
async def test_an_empty_answer(
    agent: AgentName, seed_registry: ToolRegistry, job_for: Callable[[AgentName], Job]
) -> None:
    """``may_be_empty`` agents pass with no items on the first attempt. The
    others are told their empty answer was empty and retried once (an empty
    list from an agent that must answer is a failed answer, most often a model
    that did not call the output tool): a second empty answer blocks."""
    pack = await demo_pack(agent, seed_registry)
    items = demo_output(agent, pack)
    for second, answers in ((answer(items), True), (answer([]), False)):
        job = job_for(agent)
        result, _model, _usage = await job.run(pack, [[answer([])], [second]])
        if job.spec.may_be_empty:
            assert len(result.attempts) == 1
            assert result.verdict is RunVerdict.PASS
            assert result.accepted == []
            continue
        assert len(result.attempts) == 2
        assert EMPTY_ANSWER_FEEDBACK in job.seen[-1].user_text
        if answers:
            assert result.verdict is RunVerdict.PASS
            assert result.accepted
        else:
            assert result.verdict is RunVerdict.BLOCKED
            assert result.stopped_at is GuardName.OUTPUT_SHAPE
            assert result.reason == "no agent output to check; failing closed"


# --- S7 tool round trip --------------------------------------------------------------

#: Per agent with tool calls, the call whose result its answer cites.
TOOL_CALLS: dict[AgentName, tuple[str, dict[str, Any]]] = {
    AgentName.RESEARCH: ("policy_search", {"query": "third party risk", "top_k": 10}),
    AgentName.ASSESSMENT: ("control_inventory", {"domain": "product"}),
    AgentName.CRITIC: ("evidence_ledger", {}),
    AgentName.NARRATIVE: ("evidence_ledger", {}),
    AgentName.POLICY_GAP: ("case_search", {"query": "remittance"}),
}
#: The narrative's completeness answer cites nothing: its recommendation does.
TOOL_MODE: dict[AgentName, str] = {AgentName.NARRATIVE: "recommendation"}


async def tool_sources(
    registry: ToolRegistry, pack: ContextPack, name: str, args: dict[str, Any]
) -> list[AvailableSource]:
    """What calling ``name`` with ``args`` lets the agent cite (the tool run
    directly, scoped as H2 scopes it)."""
    tool = registry.get(name)
    scope = {
        "case_id": pack.case_id,
        "run_id": pack.run_id,
        "controls_v": pack.versions.controls_v,
        "fieldset_v": pack.versions.fieldset_v,
        **FIXED_ARGUMENTS.get(name, {}),
    }
    fields = tool.input_model.model_fields
    request = tool.input_model.model_validate(
        {**args, **{k: v for k, v in scope.items() if k in fields}}
    )
    result = await tool.call(request)
    return sources_from_result(
        name, request, result, case_id=pack.case_id, versions=pack.versions
    )


def _ref(source_ref: Any) -> str:
    return str(source_ref.model_dump_json())


def _cites(item: BaseModel) -> Optional[str]:
    """The source ``item`` cites, as a comparable key (``None``: none)."""
    ref = getattr(item, "source_ref", None)
    return _ref(ref) if ref is not None else None


@pytest.mark.parametrize("agent", AGENTS, ids=ids(7))
async def test_a_tool_result_is_a_citable_source(
    agent: AgentName, seed_registry: ToolRegistry, job_for: Callable[[AgentName], Job]
) -> None:
    job = job_for(agent)
    pack = await demo_pack(agent, seed_registry, TOOL_MODE.get(agent))
    items = demo_output(agent, pack)
    if job.spec.tool_call_cap == 0:
        offered: list[Any] = []

        def record(info: AgentInfo, messages: list[ModelMessage]) -> ModelResponse:
            offered.extend(info.function_tools)
            return answer(items)(info, messages)

        result, _model, _usage = await job.run(pack, [[record]])
        assert result.verdict is RunVerdict.PASS, result.reason
        assert offered == [], "an agent without tool calls is offered no tool"
        return

    name, args = TOOL_CALLS[agent]
    from_tool = await tool_sources(seed_registry, pack, name, args)
    refs = {_ref(s.source_ref) for s in from_tool}
    if not any(_cites(i) in refs for i in items):
        # Its demo answer cites nothing a tool returns: cite the result itself.
        source = from_tool[0]
        quote = source.text.splitlines()[0]
        items = [
            items[0].model_copy(
                update={"source_ref": source.source_ref, "quote": quote}
            )
        ]
    cited = [i for i in items if _cites(i) in refs]
    assert cited, "the answer cites the tool result"
    # The pack keeps none of them: they resolve against the tool result only.
    bare = pack.model_copy(
        update={"sources": [s for s in pack.sources if _ref(s.source_ref) not in refs]}
    )
    result, _model, _usage = await job.run(
        bare, [[tool_call(name, args), answer(items)]]
    )

    assert result.verdict is RunVerdict.PASS, result.reason
    assert len(result.attempts) == 1
    assert result.attempts[0].tools_called == [name]
    assert [s.round_trip for s in job.seen] == [0, 1]
    assert {_cites(i) for i in result.accepted} & refs, "the tool-sourced item is kept"

    unbacked, _model, _usage = await job_for(agent).run(bare, [[answer(items)]])
    assert unbacked.verdict is RunVerdict.BLOCKED, (
        "without the call the same citations do not resolve"
    )


# --- S8 fallback-chain failure or timeout -------------------------------------------


@pytest.mark.parametrize("agent", AGENTS, ids=ids(8))
async def test_a_failed_model_call_runs_attempt_two_on_the_fallbacks(
    agent: AgentName, seed_registry: ToolRegistry, job_for: Callable[[AgentName], Job]
) -> None:
    pack = await demo_pack(agent, seed_registry)
    items = demo_output(agent, pack)
    for failure in (chain_failed, read_timeout):
        job = job_for(agent)
        result, model_used, _usage = await job.run(
            pack, [[raising(failure)], [answer(items)]]
        )
        assert result.verdict is RunVerdict.PASS, result.reason
        assert len(result.attempts) == 2
        assert [a.slot for a in result.attempts] == [
            ModelSlot.PRIMARY_WITH_FALLBACK.value,
            ModelSlot.FALLBACK_ONLY.value,
        ]
        assert last_guard(result).details["error"] == type(failure()).__name__
        retry = next(s for s in job.seen if s.attempt == 2)
        assert RETRY_HEADER not in retry.user_text, "a failed call is not refused"
        assert model_used == "scripted-2"

    job = job_for(agent)
    result, _model, _usage = await job.run(
        pack, [[raising(chain_failed)], [raising(read_timeout)]]
    )
    assert result.verdict is RunVerdict.BLOCKED
    assert result.stopped_at is GuardName.OUTPUT_SHAPE
    assert (result.reason or "").startswith("attempt 2: model call failed")
    assert not job.budget.has_reservation(JOB)


# --- S9 budget tip-over -------------------------------------------------------------


@pytest.mark.parametrize("agent", AGENTS, ids=ids(9))
async def test_a_tipped_over_answer_is_kept_and_a_large_overrun_blocked(
    agent: AgentName, seed_registry: ToolRegistry, job_for: Callable[[AgentName], Job]
) -> None:
    pack = await demo_pack(agent, seed_registry)
    items = demo_output(agent, pack)

    job = job_for(agent)
    tipped = RequestUsage(input_tokens=job.limit - 1_000, output_tokens=1_500)
    result, _model, _usage = await job.run(pack, [[answer(items, tipped)]])
    assert result.verdict is RunVerdict.PASS, result.reason
    assert len(result.attempts) == 1
    overrun = next(
        g for g in result.attempts[0].guard_results if g.guard is GuardName.BUDGET
    )
    assert overrun.status is GuardStatus.ANNOTATED
    assert overrun.details == {
        "limit": job.limit,
        "spent": job.limit + 500,
        "overrun": 500,
    }
    assert job.budget.used == job.limit + 500

    job = job_for(agent)
    huge = RequestUsage(input_tokens=job.limit + job.tier_max, output_tokens=1)
    result, _model, _usage = await job.run(
        pack, [[answer(items, huge)], [answer(items)]]
    )
    assert result.verdict is RunVerdict.BLOCKED
    assert result.stopped_at is GuardName.BUDGET
    assert len(result.attempts) == 1, "a budget block is never retried"
    assert [s.attempt for s in job.seen] == [1]
    assert result.tokens_used == job.limit + job.tier_max + 1, "its tokens are charged"


# --- S10 in-run repair --------------------------------------------------------------


@pytest.mark.parametrize("agent", AGENTS, ids=ids(10))
async def test_an_invalid_call_is_repaired_once_inside_the_attempt(
    agent: AgentName, seed_registry: ToolRegistry, job_for: Callable[[AgentName], Job]
) -> None:
    job = job_for(agent)
    pack = await demo_pack(agent, seed_registry)
    items = demo_output(agent, pack)
    result, _model, _usage = await job.run(pack, [[invalid, answer(items)]])

    assert result.verdict is RunVerdict.PASS, result.reason
    assert len(result.attempts) == 1
    assert [(s.attempt, s.round_trip) for s in job.seen] == [(1, 0), (1, 1)]
    assert _dumped(result.accepted) == _dumped(items)
    allowances = result.attempts[0].allowances
    assert len(allowances) == 2
    assert result.tokens_used == sum(
        a["actual_input"] + a["actual_output"] for a in allowances
    ), "both requests of the repaired attempt are charged"


@pytest.mark.parametrize("agent", AGENTS, ids=ids(10))
async def test_no_repair_when_the_budget_cannot_hold_one(
    agent: AgentName,
    seed_registry: ToolRegistry,
    job_for: Callable[[AgentName], Job],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = job_for(agent)
    pack = await demo_pack(agent, seed_registry)
    items = demo_output(agent, pack)
    run = LiveRun(
        job.spec,
        pack,
        JOB,
        registry=job.registry,
        permit=job.permit,
        budget=CaseBudget(pack.case_id),
        instructions=None,
        tiers_config=job.tiers,
    )
    tools = (
        live_runtime.tool_functions(job.spec, job.registry)
        if job.spec.tool_call_cap
        else []
    )
    first = run.first_request_estimate(pack.user_text(), tools)
    tight = 2 * first + OUTPUT_FLOOR_TOKENS - 1
    real = live_runtime.attempt_plan

    def plan(tier: Tier, attempt: int) -> AttemptPlan:
        return real(tier, attempt).model_copy(update={"token_limit": tight})

    monkeypatch.setattr(live_runtime, "attempt_plan", plan)
    small = RequestUsage(input_tokens=first, output_tokens=500)
    result, _model, _usage = await job.run(pack, [[invalid], [answer(items, small)]])

    assert result.verdict is RunVerdict.PASS, result.reason
    assert [(s.attempt, s.round_trip) for s in job.seen] == [(1, 0), (2, 0)]
    assert "Exceeded maximum output retries (0)" in last_guard(result).summary
