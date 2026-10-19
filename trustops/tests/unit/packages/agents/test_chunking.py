"""Splitting a job's input into parts: the demo packs forced to split through
the builders' own cap tables (seed-backed tools, no model, no network)."""

from __future__ import annotations

import re

import pytest
from fcrm_agents import builders
from fcrm_agents.builders import (
    AssessmentPackBuilder,
    IntakePackBuilder,
    ResearchPackBuilder,
)
from fcrm_agents.chunking import (
    SPLIT_SPECS,
    TooManyPartsError,
    part_line,
    plan_parts,
    section_head,
)
from fcrm_agents.context_pack import (
    DEFAULT_SECTION_TOKEN_CAPS,
    PRELUDE,
    ContextPack,
    PackDraft,
    PackOverflowError,
)
from fcrm_agents.demo import demo_request
from fcrm_contracts.enums import AgentName
from fcrm_contracts.proposals.provenance import ClauseSourceRef, DocSourceRef
from fcrm_tools import build_seed_registry

REGISTRY = build_seed_registry()
MAX_TOP_K = 25
"""The search tools' own ``top_k`` limit: research keeps at most that many."""
RESEARCH_CAPS = {
    "research": {
        "fields": 700,
        "clauses": 300,
        "precedents": 900,
        "jurisdictions": 300,
        "controls": 500,
    }
}
ASSESSMENT_CAPS = {
    "assessment": {
        "fields": 700,
        "controls": 150,
        "evidence": 150,
        "precedent_ratings": 150,
        "clauses_brief": 400,
    }
}
_HEAD = re.compile(r"^\[part \d+ of \d+: [^\]]*\]$")


def _items(pack: ContextPack, section: str) -> list[str]:
    """The item lines of a split section (its head note left out)."""
    lines = pack.sections[section].split("\n")
    assert _HEAD.match(lines[0])
    return lines[1:]


def test_every_agent_has_a_split_spec_naming_its_own_sections() -> None:
    assert set(SPLIT_SPECS) == set(AgentName)
    for agent, spec in SPLIT_SPECS.items():
        assert not set(spec.sharded) & set(spec.replicated)
        keys = [k for k in DEFAULT_SECTION_TOKEN_CAPS if k.startswith(agent.value)]
        known = {name for key in keys for name in DEFAULT_SECTION_TOKEN_CAPS[key]}
        assert set(spec.sharded) <= known
        assert set(spec.replicated) <= known | PRELUDE


def test_the_part_line_and_head_note_say_what_the_part_holds() -> None:
    line = part_line(
        2,
        3,
        {"clauses": [5, 6, 7, 9], "precedents": []},
        {
            "clauses": 12,
            "precedents": 5,
        },
    )
    assert line.startswith(
        "Part 2 of 3 of this job's input: clauses: items 6–8, 10 of 12; "
        "precedents: none of 5 items. "
    )
    assert "merged by code" in line
    assert line.endswith("Answer only from what is shown here.")
    assert section_head(1, 2, [0], 4) == "[part 1 of 2: item 1 of 4]"


async def test_research_clauses_split_with_every_clause_in_exactly_one_part(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(builders, "CLAUSES_KEPT", MAX_TOP_K)
    builder = ResearchPackBuilder(section_caps=RESEARCH_CAPS)
    request = demo_request(AgentName.RESEARCH)
    (whole,) = await ResearchPackBuilder(
        section_caps={"research": {**RESEARCH_CAPS["research"], "clauses": 99_999}},
        pack_totals={"strong": 99_999},
    ).build_parts(request, REGISTRY, max_parts=1)
    all_clauses = whole.sections["clauses"].split("\n")
    assert len(all_clauses) > 15

    packs = await builder.build_parts(request, REGISTRY, max_parts=8)

    assert len(packs) >= 3
    assert [(p.part_index, p.part_count) for p in packs] == [
        (i, len(packs)) for i in range(1, len(packs) + 1)
    ]
    shown = [line for p in packs for line in _items(p, "clauses")]
    assert sorted(shown) == sorted(all_clauses)
    for name in ("identity", "versions", "fields", "jurisdictions", "controls"):
        assert len({p.sections[name] for p in packs}) == 1
    assert len({p.pack_hash for p in packs}) == len(packs)
    for pack in packs:
        assert list(pack.sections)[:3] == ["identity", "versions", "part"]
        assert pack.sections["part"].startswith(
            f"Part {pack.part_index} of {pack.part_count} of this job's input"
        )
        assert "omitted by budget" not in pack.sections["clauses"]
        assert pack.token_estimate <= pack.token_cap
        assert len(pack.sections["clauses"]) <= 300 * 4
        first, last, total = pack.part_items["clauses"]
        assert 1 <= first <= last <= total == len(all_clauses)


async def test_a_parts_clause_sources_are_the_clauses_it_shows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clause shown in part 2 is not citable in part 1: the source belongs to
    its item."""
    monkeypatch.setattr(builders, "CLAUSES_KEPT", MAX_TOP_K)
    packs = await ResearchPackBuilder(section_caps=RESEARCH_CAPS).build_parts(
        demo_request(AgentName.RESEARCH), REGISTRY, max_parts=8
    )
    for pack in packs:
        ids = {line.split(" · ")[0] for line in _items(pack, "clauses")}
        cited = {
            s.source_ref.clause_id
            for s in pack.sources
            if isinstance(s.source_ref, ClauseSourceRef)
        }
        assert cited <= ids
        assert any(k.startswith("case:") for k in (s.key for s in pack.sources))
    first = {s.key for s in packs[0].sources}
    later_only = {s.key for p in packs[1:] for s in p.sources} - first
    assert later_only
    assert all(k.startswith("clause:") for k in later_only)


async def test_assessment_evidence_and_ratings_travel_with_their_control() -> None:
    builder = AssessmentPackBuilder(section_caps=ASSESSMENT_CAPS)
    packs = await builder.build_parts(
        demo_request(AgentName.ASSESSMENT), REGISTRY, max_parts=8
    )
    assert len(packs) > 1
    seen: list[str] = []
    for pack in packs:
        controls = {line.split(" · ")[0] for line in _items(pack, "controls")}
        seen += sorted(controls)
        evidence = {line.split(" · ")[0] for line in _items(pack, "evidence")}
        ratings = {line.split(" · ")[1] for line in _items(pack, "precedent_ratings")}
        assert evidence <= controls
        assert ratings <= controls
        cited = {s.key.split(":")[1].split("@")[0] for s in pack.sources}
        assert {k for k in cited if k.startswith("CTL-")} == controls
    assert len(seen) == len(set(seen)) == 16


async def test_intake_splits_whole_pages_and_offers_only_its_pages() -> None:
    packs = await IntakePackBuilder().build_parts(
        demo_request(AgentName.INTAKE), REGISTRY, max_parts=4
    )
    assert len(packs) == 2
    pages: list[int] = []
    for pack in packs:
        text = pack.sections["document"]
        assert "in a later window" not in text
        shown = sorted({int(n) for n in re.findall(r"^p\.(\d+) l\.1:", text, re.M)})
        cited = sorted(
            s.source_ref.page
            for s in pack.sources
            if isinstance(s.source_ref, DocSourceRef)
        )
        assert cited == shown
        pages += shown
        assert pack.sections["field_set"] == packs[0].sections["field_set"]
    assert pages == list(range(1, 15))


async def test_more_parts_than_allowed_raises_too_many_parts() -> None:
    builder = AssessmentPackBuilder(section_caps=ASSESSMENT_CAPS)
    with pytest.raises(TooManyPartsError) as caught:
        await builder.build_parts(
            demo_request(AgentName.ASSESSMENT), REGISTRY, max_parts=2
        )
    assert isinstance(caught.value, PackOverflowError)
    assert caught.value.allowed == 2
    assert caught.value.needed > 2
    assert "needs" in str(caught.value)


async def test_a_lone_never_truncated_item_over_its_cap_still_raises() -> None:
    caps = {"assessment": {**ASSESSMENT_CAPS["assessment"], "controls": 10}}
    builder = AssessmentPackBuilder(section_caps=caps)
    with pytest.raises(PackOverflowError) as caught:
        await builder.build_parts(
            demo_request(AgentName.ASSESSMENT), REGISTRY, max_parts=40
        )
    assert not isinstance(caught.value, TooManyPartsError)


async def test_a_replicated_section_over_its_cap_cannot_be_split() -> None:
    caps = {"research": {**RESEARCH_CAPS["research"], "fields": 20}}
    with pytest.raises(PackOverflowError, match="'fields'"):
        await ResearchPackBuilder(section_caps=caps).build_parts(
            demo_request(AgentName.RESEARCH), REGISTRY, max_parts=8
        )


async def test_one_part_keeps_cutting_and_raising_as_build_always_did(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(builders, "CLAUSES_KEPT", MAX_TOP_K)
    builder = ResearchPackBuilder(section_caps=RESEARCH_CAPS)
    request = demo_request(AgentName.RESEARCH)
    (pack,) = await builder.build_parts(request, REGISTRY, max_parts=1)
    assert "omitted by budget" in pack.sections["clauses"]
    assert pack.model_dump() == (await builder.build(request, REGISTRY)).model_dump()
    with pytest.raises(ValueError, match="at least 1"):
        await builder.build_parts(request, REGISTRY, max_parts=0)


def test_a_draft_that_fits_plans_one_whole_part() -> None:
    draft = PackDraft({"clauses": 400})
    draft.add_line("identity", "Case X")
    draft.add("clauses", [("C-1", "C-1 · text"), ("C-2", "C-2 · text")])
    (plan,) = plan_parts(AgentName.RESEARCH, draft, token_cap=1000, max_parts=4)
    assert plan.whole


def test_a_pack_over_the_total_with_nothing_to_split_raises() -> None:
    draft = PackDraft({"fields": 400})
    draft.add_line("identity", "Case X")
    draft.add("fields", [("F01", "F01 · " + "word " * 300)])
    with pytest.raises(PackOverflowError, match="tier cap 100"):
        plan_parts(AgentName.RESEARCH, draft, token_cap=100, max_parts=4)


def test_groups_stay_together_when_they_fit() -> None:
    draft = PackDraft({"candidates": 40})
    draft.add_line("identity", "Jurisdiction list a -> b")
    rows = [
        (f"CASE-{n}", f"CASE-{n} · a title of some length · {c}")
        for n, c in enumerate(["DZ", "AO", "DZ", "AO", "DZ"])
    ]
    draft.add("candidates", rows, groups=["DZ", "AO", "DZ", "AO", "DZ"])
    plans = plan_parts(AgentName.WATCHLIST, draft, token_cap=4600, max_parts=4)
    assert [p.shown["candidates"] for p in plans] == [(0, 2, 4), (1, 3)]
