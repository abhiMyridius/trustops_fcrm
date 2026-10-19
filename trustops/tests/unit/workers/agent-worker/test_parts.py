"""A job whose input was split into parts (ADR-77, ADR-78), on fake models.

- The demo intake document does not fit the cheap pack total: it runs as two
  parts, each answered from its own pages, merged into one result.
- A part blocked for a reason other than an empty answer stops the job; an
  empty part is tolerated when another part answered.
- A reduce call (the narrative recommendation's merge; its success path is
  scenario S5 of ``test_live_runtime_scenarios``) that keeps failing blocks the
  job with the partial answers kept, and an empty reduce answer is retried.
- Through the consumer: one ``agent.done`` per job, with ``output.parts``, a
  ``guard.output`` record per part and no ``agent.retry`` between parts.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fcrm_agent_worker.handler import AgentJobConsumer
from fcrm_agent_worker.live_runtime import ModelFactory
from fcrm_agent_worker.parts import PartsOutcome, run_agent_parts
from fcrm_agent_worker.reduce import PARTIAL_ANSWERS, REDUCE_PART
from fcrm_agents.builders import builder_for
from fcrm_agents.context_pack import ContextPack
from fcrm_agents.demo import demo_output, demo_request
from fcrm_agents.runtime import RunVerdict
from fcrm_agents.spec import get_spec
from fcrm_common.events import build_envelope
from fcrm_contracts.enums import AgentName, Tier
from fcrm_contracts.events.payloads import JobPayload
from fcrm_guardrails import CaseBudget, GuardName, PermitGuard
from fcrm_tools import DEMO_CASE_ID, ToolRegistry
from pydantic import BaseModel
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo

Reply = Callable[[AgentInfo, list[ModelMessage]], ModelResponse]


def _answer(items: list[BaseModel]) -> Reply:
    payload = [item.model_dump(mode="json") for item in items]

    def reply(info: AgentInfo, messages: list[ModelMessage]) -> ModelResponse:
        tool = info.output_tools[0]
        return ModelResponse(
            parts=[ToolCallPart(tool_name=tool.name, args={"response": payload})]
        )

    return reply


def _invalid(info: AgentInfo, messages: list[ModelMessage]) -> ModelResponse:
    tool = info.output_tools[0]
    return ModelResponse(
        parts=[ToolCallPart(tool_name=tool.name, args={"response": "not a list"})]
    )


async def _intake_parts(registry: ToolRegistry) -> list[ContextPack]:
    packs = await builder_for(AgentName.INTAKE).build_parts(
        demo_request(AgentName.INTAKE), registry, max_parts=4
    )
    assert len(packs) == 2, "the demo document is two parts on the cheap tier"
    return packs


async def _recommendation_parts(registry: ToolRegistry) -> list[ContextPack]:
    """The demo recommendation pack as if it had been split in two (it fits;
    the reduce path is what is under test)."""
    pack = await builder_for(AgentName.NARRATIVE).build(
        demo_request(AgentName.NARRATIVE, "recommendation"), registry
    )
    return [
        pack.model_copy(
            update={
                "part_index": i,
                "part_count": 2,
                "pack_hash": f"{pack.pack_hash}{i}",
            }
        )
        for i in (1, 2)
    ]


async def _run(
    agent: AgentName,
    packs: list[ContextPack],
    registry: ToolRegistry,
    permit: PermitGuard,
    tiers: dict[str, Any],
    factory: ModelFactory,
) -> PartsOutcome:
    return await run_agent_parts(
        get_spec(agent),
        packs,
        "job-parts",
        registry=registry,
        permit=permit,
        budget=CaseBudget(DEMO_CASE_ID),
        instructions=None,
        tiers_config=tiers,
        model_factory=factory,
        reduce_tier=Tier.CHEAP,
    )


async def test_a_split_intake_job_answers_each_part_and_merges_them(
    seed_registry: ToolRegistry,
    allow_all_permit: PermitGuard,
    fake_tiers_config: dict[str, Any],
    make_model_factory_scripted: Callable[..., ModelFactory],
) -> None:
    packs = await _intake_parts(seed_registry)
    seen: list[Any] = []
    factory = make_model_factory_scripted(
        [[_answer(demo_output(AgentName.INTAKE, p))] for p in packs], seen
    )
    outcome = await _run(
        AgentName.INTAKE,
        packs,
        seed_registry,
        allow_all_permit,
        fake_tiers_config,
        factory,
    )
    result = outcome.result
    assert result.verdict is RunVerdict.PASS, result.reason
    assert [a.part for a in result.attempts] == [1, 2]
    assert "Part 1 of 2" in seen[0].user_text
    assert "Part 2 of 2" in seen[1].user_text
    # Pages 12-14 hold no fields: part 2 only repeats part 1's gap questions,
    # which the merge folds (H1's own key), keeping part 1's extraction.
    kinds = [type(i).__name__ for i in result.accepted]
    assert kinds.count("ExtractionProposal") == 1
    assert kinds.count("GapQuestion") == 3
    assert any("duplicate" in n for n in result.notes_for_analyst)
    assert [p.index for p in outcome.parts] == [1, 2]
    assert outcome.usage["input_tokens"] == sum(
        p.usage["input_tokens"] for p in outcome.parts
    )
    assert any("input analysed in 2 parts" in n for n in result.notes_for_analyst)
    assert outcome.failed_pack is None


async def test_a_blocked_part_stops_the_job_before_the_next_part(
    seed_registry: ToolRegistry,
    allow_all_permit: PermitGuard,
    fake_tiers_config: dict[str, Any],
    make_model_factory_scripted: Callable[..., ModelFactory],
) -> None:
    packs = await _intake_parts(seed_registry)
    seen: list[Any] = []
    factory = make_model_factory_scripted([[_invalid], [_invalid]], seen)
    outcome = await _run(
        AgentName.INTAKE,
        packs,
        seed_registry,
        allow_all_permit,
        fake_tiers_config,
        factory,
    )
    assert outcome.result.verdict is RunVerdict.BLOCKED
    assert (outcome.result.reason or "").startswith("part 1 of 2: ")
    assert all("Part 1 of 2" in s.user_text for s in seen)
    assert outcome.failed_pack is packs[0]


async def test_an_empty_part_is_tolerated_when_another_part_answered(
    seed_registry: ToolRegistry,
    allow_all_permit: PermitGuard,
    fake_tiers_config: dict[str, Any],
    make_model_factory_scripted: Callable[..., ModelFactory],
) -> None:
    packs = await _intake_parts(seed_registry)
    factory = make_model_factory_scripted(
        [[_answer(demo_output(AgentName.INTAKE, packs[0]))], [_answer([])]], []
    )
    outcome = await _run(
        AgentName.INTAKE,
        packs,
        seed_registry,
        allow_all_permit,
        fake_tiers_config,
        factory,
    )
    assert outcome.result.verdict is RunVerdict.PASS, outcome.result.reason
    assert any("nothing to report" in n for n in outcome.result.notes_for_analyst)


async def test_a_reduce_call_that_keeps_failing_blocks_with_the_partial_answers_kept(
    seed_registry: ToolRegistry,
    allow_all_permit: PermitGuard,
    fake_tiers_config: dict[str, Any],
    make_model_factory_scripted: Callable[..., ModelFactory],
) -> None:
    packs = await _recommendation_parts(seed_registry)
    answer = demo_output(AgentName.NARRATIVE, packs[0])
    factory = make_model_factory_scripted(
        [[_answer(answer)], [_answer(answer)], [_invalid], [_invalid]], []
    )
    outcome = await _run(
        AgentName.NARRATIVE,
        packs,
        seed_registry,
        allow_all_permit,
        fake_tiers_config,
        factory,
    )
    assert outcome.result.verdict is RunVerdict.BLOCKED
    assert outcome.result.stopped_at is GuardName.OUTPUT_SHAPE
    assert (outcome.result.reason or "").startswith("reduce_failed: ")
    assert outcome.failed_pack is not None
    assert PARTIAL_ANSWERS in outcome.failed_pack.sections


async def test_an_empty_reduce_answer_is_retried_like_an_unsplit_job(
    seed_registry: ToolRegistry,
    allow_all_permit: PermitGuard,
    fake_tiers_config: dict[str, Any],
    make_model_factory_scripted: Callable[..., ModelFactory],
) -> None:
    """Only a part may answer empty; the reduce call always has partial
    answers to merge, so an empty reply gets the empty-answer retry."""
    packs = await _recommendation_parts(seed_registry)
    answer = demo_output(AgentName.NARRATIVE, packs[0])
    factory = make_model_factory_scripted(
        [[_answer(answer)], [_answer(answer)], [_answer([])], [_answer(answer)]], []
    )
    outcome = await _run(
        AgentName.NARRATIVE,
        packs,
        seed_registry,
        allow_all_permit,
        fake_tiers_config,
        factory,
    )
    assert outcome.result.verdict is RunVerdict.PASS, outcome.result.reason
    assert [a.part for a in outcome.result.attempts] == [1, 2, REDUCE_PART, REDUCE_PART]


@pytest.fixture
def intake_job(demo_job_payload: JobPayload) -> JobPayload:
    request = demo_request(AgentName.INTAKE)
    return demo_job_payload.model_copy(
        update={
            "job_id": "job-intake-parts",
            "agent": AgentName.INTAKE,
            "tier": get_spec(AgentName.INTAKE).tier,
            "step_key": "intake",
            "refs": {"doc_id": request.doc_id},
        }
    )


async def test_a_split_job_publishes_one_agent_done_with_its_parts(
    intake_job: JobPayload,
    seed_registry: ToolRegistry,
    allow_all_opa_client: Any,
    recording_audit: Any,
    recording_publisher: Any,
    fake_tiers_config: dict[str, Any],
    fake_consumed: Any,
    consumer_deps: Any,
    make_model_factory_scripted: Callable[..., ModelFactory],
) -> None:
    packs = await _intake_parts(seed_registry)
    consumer = AgentJobConsumer(
        fake_consumed,
        recording_publisher,
        **consumer_deps.kwargs,
        registry=seed_registry,
        permit_client=allow_all_opa_client,
        audit=recording_audit,
        tiers_config=fake_tiers_config,
        model_factory=make_model_factory_scripted(
            [[_answer(demo_output(AgentName.INTAKE, p))] for p in packs], []
        ),
    )
    envelope = build_envelope(
        "agent.run",
        intake_job.model_dump(mode="json"),
        case_id=intake_job.case_id,
        run_id=intake_job.run_id,
    )
    await consumer.handle(envelope, None)

    events = consumer_deps.events
    assert [key for key, _ in events] == ["agent.done"]
    output = events[0][1].payload["output"]
    assert [p["part"] for p in output["parts"]] == [1, 2]
    assert output["guard_summary"]["claims_total"] > 0
    records = recording_audit.records
    assert "agent.retry" not in [r.type for r in records]
    outputs = [r for r in records if r.type == "guard.output"]
    assert [r.data.get("part") for r in outputs] == [1, 2]
    assert outputs[0].summary.startswith("intake part 1 attempt 1: ")
