"""The nine context-pack builders against the seed-backed tools: non-empty
sections in fixed order, caps respected, only pack tools called, citable
sources registered, truncation visible, overflow refused."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import pytest
from fcrm_agents import BUILDERS, PackOverflowError, builder_for, estimate_tokens
from fcrm_agents.builders import IntakePackBuilder, ResearchPackBuilder
from fcrm_agents.context_pack import (
    CHARS_PER_TOKEN,
    ContextPackBuilder,
    PackBuildError,
    ScopedTools,
    fit_items,
)
from fcrm_agents.demo import demo_request
from fcrm_agents.spec import get_spec
from fcrm_contracts.enums import AgentName, Domain
from fcrm_contracts.proposals.provenance import ClauseSourceRef, DocSourceRef
from fcrm_contracts.tools.audit_log import AuditLogInput, AuditLogOutput
from fcrm_contracts.tools.case_record import CaseRecordInput, CaseRecordOutput
from fcrm_contracts.tools.control_inventory import (
    ControlInventoryInput,
    ControlInventoryOutput,
)
from fcrm_seed.loader import load_raw
from fcrm_tools import (
    DEMO_CASE_ID,
    ToolNotPermittedError,
    ToolScopeError,
    build_seed_registry,
    seed_pins,
)

REGISTRY = build_seed_registry()
REPO_AI = Path(__file__).parents[4] / "ai"
CASES: list[tuple[AgentName, Optional[str]]] = [
    (a, None) for a in AgentName if a is not AgentName.NARRATIVE
] + [(AgentName.NARRATIVE, "completeness"), (AgentName.NARRATIVE, "recommendation")]
EXPECTED_ORDER = {
    AgentName.SCREENING: ["fields", "precedents"],
    AgentName.INTAKE: ["field_set", "document"],
    AgentName.RESEARCH: [
        "fields",
        "clauses",
        "precedents",
        "jurisdictions",
        "controls",
    ],
    AgentName.ASSESSMENT: [
        "fields",
        "controls",
        "evidence",
        "precedent_ratings",
        "clauses_brief",
    ],
    AgentName.CRITIC: [
        "score",
        "proposals",
        "precedent_ratings",
        "rules",
        "evidence_dates",
    ],
    AgentName.EXAMINER: ["question", "records", "routing"],
    AgentName.WATCHLIST: ["list_diff", "candidates", "clauses"],
    AgentName.POLICY_GAP: ["gaps", "nearest"],
}


def _id(case: tuple[AgentName, Optional[str]]) -> str:
    return f"{case[0].value}-{case[1]}" if case[1] else case[0].value


def test_builders_cover_all_nine_agents() -> None:
    assert set(BUILDERS) == set(AgentName)


@pytest.mark.parametrize("case", CASES, ids=_id)
async def test_builder_sections_non_empty_and_within_caps(
    case: tuple[AgentName, Optional[str]],
) -> None:
    agent, mode = case
    pack = await builder_for(agent).build(demo_request(agent, mode), REGISTRY)
    assert pack.section_names()[:2] == ["identity", "versions"]
    assert all(text.strip() for text in pack.sections.values())
    for section in pack.section_meta:
        assert len(section.text) <= section.cap_tokens * CHARS_PER_TOKEN
        assert section.tokens == estimate_tokens(section.text)
    assert 0 < pack.token_estimate <= pack.token_cap
    assert pack.token_estimate == estimate_tokens(pack.user_text())
    assert set(pack.tools_called) <= set(get_spec(agent).pack_tools)
    assert pack.sources


@pytest.mark.parametrize(("agent", "order"), list(EXPECTED_ORDER.items()))
async def test_builder_section_order_is_fixed(
    agent: AgentName, order: list[str]
) -> None:
    pack = await builder_for(agent).build(demo_request(agent), REGISTRY)
    assert pack.section_names()[2:] == order


async def test_builder_pack_hash_is_stable_across_builds() -> None:
    request = demo_request(AgentName.RESEARCH)
    first = await ResearchPackBuilder().build(request, REGISTRY)
    second = await ResearchPackBuilder().build(request, build_seed_registry())
    assert first.pack_hash == second.pack_hash
    assert first.user_text() == second.user_text()


async def test_research_pack_cites_the_four_demo_clauses_verbatim() -> None:
    pack = await ResearchPackBuilder().build(demo_request(AgentName.RESEARCH), REGISTRY)
    clause_sources = {
        s.source_ref.clause_id: s.text
        for s in pack.sources
        if isinstance(s.source_ref, ClauseSourceRef)
    }
    demo = {c["clause_id"]: c["quote"] for c in load_raw()["demo"]["clauses"]}
    assert clause_sources == demo
    assert "FCRM-2026-0118" in pack.sections["precedents"].splitlines()[0]
    assert "Mexico: not listed" in pack.sections["jurisdictions"]


async def test_intake_pack_truncates_whole_pages_and_cites_only_shown_pages() -> None:
    pack = await IntakePackBuilder().build(demo_request(AgentName.INTAKE), REGISTRY)
    meta = next(s for s in pack.section_meta if s.name == "document")
    assert meta.truncated is True
    assert meta.items_total == 14
    assert "in a later window]" in pack.sections["document"]
    pages = sorted(
        s.source_ref.page
        for s in pack.sources
        if isinstance(s.source_ref, DocSourceRef)
    )
    assert pages == list(range(1, meta.items_shown + 1))


async def test_intake_page_window_limits_document_pages() -> None:
    request = demo_request(AgentName.INTAKE).model_copy(update={"page_window": (6, 7)})
    pack = await IntakePackBuilder().build(request, REGISTRY)
    assert "p.6 l.1:" in pack.sections["document"]
    assert "p.5 l.1:" not in pack.sections["document"]


async def test_intake_without_doc_id_is_refused() -> None:
    request = demo_request(AgentName.RESEARCH)
    with pytest.raises(PackBuildError):
        await IntakePackBuilder().build(request, REGISTRY)


async def test_examiner_without_question_is_refused() -> None:
    request = demo_request(AgentName.EXAMINER).model_copy(update={"question": None})
    with pytest.raises(PackBuildError):
        await builder_for(AgentName.EXAMINER).build(request, REGISTRY)


async def test_watchlist_candidates_are_the_two_fixture_reflag_cases() -> None:
    pack = await builder_for(AgentName.WATCHLIST).build(
        demo_request(AgentName.WATCHLIST), REGISTRY
    )
    assert "FCRM-2026-0144" in pack.sections["candidates"]
    assert "FCRM-2026-0147" in pack.sections["candidates"]
    assert "Algeria · added" in pack.sections["list_diff"]


async def test_critic_pack_shows_code_rules_and_stale_flag() -> None:
    pack = await builder_for(AgentName.CRITIC).build(
        demo_request(AgentName.CRITIC), REGISTRY
    )
    assert "R-8 · kept" in pack.sections["rules"]
    assert "CTL-T01 · 2023-11-15 · stale" in pack.sections["evidence_dates"]
    assert "composite 9.8" in pack.sections["score"]


async def test_pack_over_tier_total_raises() -> None:
    builder = ResearchPackBuilder(pack_totals={"strong": 100})
    with pytest.raises(PackOverflowError, match="tier cap"):
        await builder.build(demo_request(AgentName.RESEARCH), REGISTRY)


async def test_tight_section_cap_truncates_visibly() -> None:
    builder = ResearchPackBuilder(
        section_caps={"research": {"fields": 700, "clauses": 120, "controls": 500}}
    )
    pack = await builder.build(demo_request(AgentName.RESEARCH), REGISTRY)
    meta = next(s for s in pack.section_meta if s.name == "clauses")
    assert meta.truncated is True
    assert "omitted by budget: ids" in pack.sections["clauses"]
    assert len(pack.sections["clauses"]) <= 120 * CHARS_PER_TOKEN


@pytest.mark.skipif(not (REPO_AI / "guards").is_dir(), reason="ai/ lane absent")
def test_builder_from_ai_reads_thresholds_caps() -> None:
    builder = ResearchPackBuilder.from_ai(REPO_AI)
    assert isinstance(builder, ContextPackBuilder)
    assert builder.token_cap() == 9000


def test_fit_items_never_truncate_overflow_raises() -> None:
    items = [(f"F{n:02d}", "x" * 50) for n in range(10)]
    with pytest.raises(PackOverflowError):
        fit_items("fields", items, 50, never_truncate=True)


def test_fit_items_cuts_at_whole_items_with_note() -> None:
    items = [(f"C{n}", "y" * 40) for n in range(10)]
    section = fit_items("clauses", items, 60)
    lines = section.text.splitlines()
    assert lines[-1].startswith(f"[showing {section.items_shown} of 10;")
    assert all(line == "y" * 40 for line in lines[:-1])
    assert len(section.text) <= 240


def _scoped(agent: AgentName) -> ScopedTools:
    return ScopedTools(
        REGISTRY,
        agent=agent,
        allowed=get_spec(agent).pack_tools,
        case_id=DEMO_CASE_ID,
        versions=seed_pins(load_raw()),
    )


async def test_scoped_tools_refuses_tool_outside_pack_tools() -> None:
    with pytest.raises(ToolNotPermittedError):
        await _scoped(AgentName.RESEARCH).call(
            "audit_log", AuditLogInput(case_id=DEMO_CASE_ID), AuditLogOutput
        )


async def test_scoped_tools_refuses_another_case() -> None:
    with pytest.raises(ToolScopeError):
        await _scoped(AgentName.RESEARCH).call(
            "case_record", CaseRecordInput(case_id="FCRM-2026-0118"), CaseRecordOutput
        )


async def test_scoped_tools_refuses_unpinned_controls_version() -> None:
    with pytest.raises(ToolScopeError):
        await _scoped(AgentName.RESEARCH).call(
            "control_inventory",
            ControlInventoryInput(domain=Domain.CHANNEL, controls_v=8),
            ControlInventoryOutput,
        )
