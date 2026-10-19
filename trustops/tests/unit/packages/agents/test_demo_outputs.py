"""The pack-derived demo outputs of all nine agents (``fcrm_agents.demo``)
through the real guard chain: each passes Guards 1-5 with every item
accepted, each tampered variant fails Guard 4, and every citation handle
and quote comes from the pack the output was derived from."""

from __future__ import annotations

import io
from typing import Optional

import pytest
from fcrm_agents import RunVerdict, run_agent_offline
from fcrm_agents.builders import builder_for
from fcrm_agents.cli import main
from fcrm_agents.context_pack import ContextPack
from fcrm_agents.demo import demo_output, demo_request
from fcrm_agents.spec import get_spec
from fcrm_contracts.enums import AgentName
from fcrm_contracts.proposals.assessment import ControlProposal
from fcrm_contracts.proposals.examiner import ExaminerAnswer
from fcrm_contracts.proposals.narrative import BusinessSummary, Narrative
from fcrm_contracts.proposals.provenance import DocSourceRef
from fcrm_guardrails import GuardName, GuardStatus, source_key
from fcrm_guardrails.output_shape import AGENT_OUTPUT_TYPES, iter_claims
from fcrm_guardrails.quote_match import line_span, normalise, normalise_quote
from fcrm_guardrails.rules.r3_po_no_restricted import restricted_terms_in
from fcrm_guardrails.thresholds import QUOTE_LINE_TOLERANCE, QUOTE_MIN_CHARS
from fcrm_tools import build_seed_registry

SEED_TOOLS = build_seed_registry()

CASES: list[tuple[AgentName, Optional[str]]] = [
    (AgentName.SCREENING, None),
    (AgentName.INTAKE, None),
    (AgentName.RESEARCH, None),
    (AgentName.ASSESSMENT, None),
    (AgentName.CRITIC, None),
    (AgentName.NARRATIVE, "completeness"),
    (AgentName.NARRATIVE, "recommendation"),
    (AgentName.EXAMINER, None),
    (AgentName.WATCHLIST, None),
    (AgentName.POLICY_GAP, None),
]
IDS = [f"{a.value}-{m}" if m else a.value for a, m in CASES]


async def _pack(agent: AgentName, mode: Optional[str]) -> ContextPack:
    return await builder_for(agent).build(demo_request(agent, mode), SEED_TOOLS)


def test_every_agent_has_a_case() -> None:
    assert {a for a, _ in CASES} == set(AgentName)


@pytest.mark.parametrize(("agent", "mode"), CASES, ids=IDS)
async def test_demo_output_passes_guards_with_every_item_accepted(
    agent: AgentName, mode: Optional[str]
) -> None:
    pack = await _pack(agent, mode)
    items = demo_output(agent, pack)
    assert items
    assert {type(i) for i in items} <= set(AGENT_OUTPUT_TYPES[agent])
    result = await run_agent_offline(get_spec(agent), pack, items)
    assert result.verdict is RunVerdict.PASS, result.reason
    assert len(result.attempts) == 1
    assert result.accepted == items
    assert result.dropped == []
    assert result.notes_for_analyst == []
    shape, rules = result.attempts[0].guard_results
    assert shape.guard is GuardName.OUTPUT_SHAPE
    assert shape.status is GuardStatus.PASSED
    assert rules.summary == "8 of 8 rules kept"


@pytest.mark.parametrize(("agent", "mode"), CASES, ids=IDS)
async def test_demo_output_cites_only_the_pack_and_quotes_verbatim(
    agent: AgentName, mode: Optional[str]
) -> None:
    pack = await _pack(agent, mode)
    shown = {s.key: s for s in pack.sources}
    for i, item in enumerate(demo_output(agent, pack)):
        for path, claim in iter_claims(item, f"outputs[{i}]"):
            source = shown.get(source_key(claim.source_ref))
            assert source is not None, f"{path} cites a source the pack lacks"
            text = source.text
            ref = claim.source_ref
            if isinstance(ref, DocSourceRef):
                text = line_span(
                    text, ref.line_start, ref.line_end, tolerance=QUOTE_LINE_TOLERANCE
                )
            quote = normalise_quote(claim.quote)
            assert len(quote) >= QUOTE_MIN_CHARS, path
            assert quote in normalise(text), f"{path}: {claim.quote!r}"


@pytest.mark.parametrize(("agent", "mode"), CASES, ids=IDS)
async def test_tampered_demo_output_is_not_a_clean_pass(
    agent: AgentName, mode: Optional[str]
) -> None:
    pack = await _pack(agent, mode)
    spec = get_spec(agent)
    tampered = demo_output(agent, pack, tamper=True)
    once = await run_agent_offline(spec, pack, tampered)
    assert once.verdict is RunVerdict.BLOCKED
    assert once.stopped_at is GuardName.OUTPUT_SHAPE
    first = once.attempts[0].guard_results[0]
    assert first.status is GuardStatus.RETRY
    assert len(first.details["failed_items"]) == 1
    twice = await run_agent_offline(spec, pack, tampered, retry_output=tampered)
    assert twice.verdict in (RunVerdict.PASS_WITH_DROPS, RunVerdict.BLOCKED)
    corrected = await run_agent_offline(
        spec, pack, tampered, retry_output=demo_output(agent, pack)
    )
    assert corrected.verdict is RunVerdict.PASS
    assert len(corrected.attempts) == 2


async def test_assessment_plans_ctl_t01_and_rates_the_rest() -> None:
    pack = await _pack(AgentName.ASSESSMENT, None)
    controls = [
        i
        for i in demo_output(AgentName.ASSESSMENT, pack)
        if isinstance(i, ControlProposal)
    ]
    planned = [c.control_id for c in controls if c.planned]
    assert planned == ["CTL-T01"]
    t01 = next(c for c in controls if c.control_id == "CTL-T01" and not c.planned)
    assert t01.rating == 7.6
    assert len({c.control_id for c in controls if not c.planned}) == 13


async def test_recommendation_summary_has_no_restricted_term() -> None:
    pack = await _pack(AgentName.NARRATIVE, "recommendation")
    items = demo_output(AgentName.NARRATIVE, pack)
    summary = next(i for i in items if isinstance(i, BusinessSummary))
    assert restricted_terms_in(summary.text) == []
    assert sum(isinstance(i, Narrative) for i in items) == 1


async def test_examiner_tamper_cites_an_unknown_record() -> None:
    pack = await _pack(AgentName.EXAMINER, None)
    answer = demo_output(AgentName.EXAMINER, pack, tamper=True)[0]
    assert isinstance(answer, ExaminerAnswer)
    result = await run_agent_offline(get_spec("examiner"), pack, answer)
    checks = result.attempts[0].guard_results[0].ledger_items
    assert [(a.item_id, a.check) for a in checks if not a.ok] == [
        ("outputs[0].sentences[0]", "records_resolve")
    ]


@pytest.mark.parametrize("agent", ["screening", "assessment", "policy_gap"])
def test_cli_runs_any_agent(agent: str) -> None:
    out = io.StringIO()
    assert main(["run", agent, "--seed"], out=out) == 0
    assert "verdict: PASS" in out.getvalue()


def test_cli_runs_narrative_recommendation_and_tamper_blocks() -> None:
    out = io.StringIO()
    args = ["run", "narrative", "--mode", "recommendation", "--seed"]
    assert main(args, out=out) == 0
    assert "accepted Condition CND-3" in out.getvalue()
    blocked = io.StringIO()
    assert main([*args, "--tamper"], out=blocked) == 2
    assert "stopped at output_shape" in blocked.getvalue()
