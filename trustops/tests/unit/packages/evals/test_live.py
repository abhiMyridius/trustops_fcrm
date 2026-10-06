"""The live producer: packs from dataset rows, the real runtime with a fake
Bedrock, and what a blocked or failed run leaves behind."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fcrm_agents.runtime import RunVerdict
from fcrm_agents.spec import REGISTRY
from fcrm_contracts.enums import AgentName, Tier
from fcrm_contracts.proposals.screening import ScreeningProposal
from fcrm_evals.datasets import load
from fcrm_evals.instructions import InstructionRenderer
from fcrm_evals.live import (
    LiveProducer,
    RowPackBuilder,
    lifted_token_budgets,
    with_field_lines,
)
from fcrm_evals.metrics.citation import shown_sources
from fcrm_evals.usage import RowStatus
from fcrm_guardrails.budget import TIER_BUDGETS
from fcrm_seed.loader import load_raw
from fcrm_tools import build_seed_registry, seed_pins

BUILDER_SECTIONS = {
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
    AgentName.CRITIC: ["proposals", "precedent_ratings", "rules", "evidence_dates"],
    AgentName.NARRATIVE: [
        "ratings",
        "clauses",
        "planned",
        "findings",
        "gaps",
        "answers",
        "owner_facts",
    ],
    AgentName.EXAMINER: ["question", "records", "routing"],
    AgentName.WATCHLIST: ["list_diff", "candidates", "clauses"],
    AgentName.POLICY_GAP: ["gaps", "nearest"],
}
"""Each agent's sections after the prelude, as ``fcrm_agents.builders``'s own
docstring table lists them (the critic's ``score`` appears only when the
engine summary is given, which a dataset row does not carry)."""


@pytest.fixture(scope="module")
def pack_builder() -> RowPackBuilder:
    root = Path(__file__).parents[4] / "ai"
    return RowPackBuilder.from_ai(build_seed_registry(), seed_pins(load_raw()), root)


@pytest.mark.parametrize("agent", list(AgentName))
async def test_pack_sections_are_the_real_builders_in_order(
    agent: AgentName, pack_builder: RowPackBuilder
) -> None:
    pack = await pack_builder.build(load(agent)[0])
    assert pack.section_names() == ["identity", "versions", *BUILDER_SECTIONS[agent]]


@pytest.mark.parametrize("agent", list(AgentName))
async def test_every_row_of_every_dataset_builds_a_pack_under_its_cap(
    agent: AgentName, pack_builder: RowPackBuilder
) -> None:
    for row in load(agent):
        pack = await pack_builder.build(row)
        assert pack.token_estimate <= pack.token_cap


@pytest.mark.parametrize("agent", [a for a in AgentName if a is not AgentName.CRITIC])
async def test_pack_sources_are_the_rows_own_widened_by_the_lines_the_pack_printed(
    agent: AgentName, pack_builder: RowPackBuilder
) -> None:
    row = load(agent)[0]
    pack = await pack_builder.build(row)
    own = with_field_lines(
        row.sources(),
        row.case_id,
        pack.sections.get("fields"),
        pack.sections["identity"].split(" · domains in scope")[0],
    )
    assert [s.model_dump() for s in pack.sources] == [s.model_dump() for s in own]


async def test_critic_pack_widens_only_the_assessment_record(
    pack_builder: RowPackBuilder,
) -> None:
    row = load(AgentName.CRITIC)[0]
    pack = await pack_builder.build(row)
    original = {s.key: s.text for s in row.sources()}
    changed = [s.key for s in pack.sources if s.text != original[s.key]]
    assert changed == ["record:FCRM-2026-0151#31"]
    widened = next(s for s in pack.sources if s.key in changed)
    assert '"controls"' in widened.text


@pytest.mark.parametrize("agent", list(AgentName))
async def test_citation_validity_checks_every_agent_against_what_it_was_shown(
    agent: AgentName, pack_builder: RowPackBuilder
) -> None:
    """A finding quotes the rating it is about, which only the widened
    assessment record (production's ``cite_records``) contains; a screening
    reason quotes a whole field line or the change types, which only the widened
    case source (``cite_case``) contains. The metric uses the pack's sources, or
    a quote Guard 4 accepted would read as unsourced."""
    for row in load(agent):
        pack = await pack_builder.build(row)
        assert [s.model_dump() for s in shown_sources(row)] == [
            s.model_dump() for s in pack.sources
        ], row.row_id


async def test_the_change_types_the_identity_line_prints_are_a_quotable_case_fact(
    pack_builder: RowPackBuilder,
) -> None:
    """The screening agent quotes ``types vendor_onboarding, new_geography``
    (the fast-lane rule turns on the change types and the pack prints them in
    its identity line). The first live run's case source lacked the line, so
    four of five rows spent a retry on a quote that was in the pack."""
    row = next(r for r in load(AgentName.SCREENING) if r.case_id == "FCRM-2026-0151")
    pack = await pack_builder.build(row)
    identity = pack.sections["identity"]
    types_line = re.search(r"types [a-z_, ]+", identity)
    assert types_line is not None
    case = next(s for s in pack.sources if s.key == "case:FCRM-2026-0151")
    assert types_line.group(0).strip() in case.text
    assert f"Case FCRM-2026-0151 · submission v1 · {types_line.group(0).strip()}" in (
        case.text
    )


async def test_screening_fields_are_real_builder_lines_whose_values_a_quote_can_verify(
    pack_builder: RowPackBuilder,
) -> None:
    demo = next(r for r in load(AgentName.SCREENING) if r.case_id == "FCRM-2026-0151")
    pack = await pack_builder.build(demo)
    lines = pack.sections["fields"].split("\n")
    assert lines == [
        "F02 · Change description · Consumer remittances US → Mexico via payout "
        "partner · -",
        "F05 · Expected annual volume · $48.0M · -",
    ]
    case_text = demo.sources()[0].text
    for line in lines:
        value = line.split(" · ")[2]
        assert value in case_text


async def test_a_whole_field_line_the_pack_printed_is_a_quotable_case_source(
    pack_builder: RowPackBuilder,
) -> None:
    """A value shorter than 12 characters cannot stand as a quote; the whole
    line (id and label around it) can, so the case source holds the lines the
    pack printed, as production's ``cite_case`` does."""
    demo = next(r for r in load(AgentName.SCREENING) if r.case_id == "FCRM-2026-0151")
    pack = await pack_builder.build(demo)
    case = next(s for s in pack.sources if s.key == "case:FCRM-2026-0151")
    for line in pack.sections["fields"].split("\n"):
        assert line in case.text
    assert case.text.startswith(demo.sources()[0].text)


def test_with_field_lines_leaves_other_sources_and_pack_without_fields_alone() -> None:
    row = load(AgentName.SCREENING)[0]
    assert [
        s.model_dump() for s in with_field_lines(row.sources(), row.case_id, None)
    ] == [s.model_dump() for s in row.sources()]


async def test_a_volume_the_case_does_not_record_is_an_empty_field(
    pack_builder: RowPackBuilder,
) -> None:
    row = next(
        r for r in load(AgentName.SCREENING) if r.input["annual_volume_usd"] is None
    )
    pack = await pack_builder.build(row)
    assert "F05 · Expected annual volume · EMPTY · -" in pack.sections["fields"]


async def test_intake_pack_anchors_every_line_with_page_and_line_numbers(
    pack_builder: RowPackBuilder,
) -> None:
    demo = next(r for r in load(AgentName.INTAKE) if r.case_id == "FCRM-2026-0151")
    pack = await pack_builder.build(demo)
    document = pack.sections["document"]
    assert "p.1 l.2: Change owner: Priya Nair, Payments" in document
    assert "Document payout_partner_contract" in document


async def test_critic_rules_section_is_computed_by_the_real_rules(
    pack_builder: RowPackBuilder,
) -> None:
    row = next(
        r for r in load(AgentName.CRITIC) if r.row_id.endswith("~rule_violation")
    )
    pack = await pack_builder.build(row)
    assert re.search(r"R-7 · broken · ", pack.sections["rules"])


def test_instruction_renderer_renders_all_nine_without_template_markers(
    repo_ai: Path,
) -> None:
    renderer = InstructionRenderer(seed_pins(load_raw()), ai_root=repo_ai)
    for agent in AgentName:
        text = renderer.render(agent).text
        assert "{{" not in text
        assert "{%" not in text


def test_raw_instructions_are_the_unrendered_template_body(repo_ai: Path) -> None:
    renderer = InstructionRenderer(seed_pins(load_raw()), ai_root=repo_ai)
    assert "{{" in renderer.raw(AgentName.SCREENING).text
    assert "Fast lane" in renderer.render(AgentName.SCREENING).text


async def test_live_run_of_a_row_returns_the_agents_accepted_items(
    producer_for: Callable[..., LiveProducer],
) -> None:
    row = load(AgentName.SCREENING)[0]
    output = await producer_for(AgentName.SCREENING, [row]).produce(row)
    assert output.status is RowStatus.OK
    assert [type(i) for i in output.items] == [ScreeningProposal]
    run = output.run
    assert run is not None
    assert run.status is RowStatus.OK
    assert run.verdict == RunVerdict.PASS.value
    assert (run.calls, run.attempts, run.retries) == (1, 1, 0)
    assert run.input_tokens > 0
    assert run.cost_usd == 0.0  # the fake model's name has no price row
    assert run.models == ["unlabelled model"]


async def test_blocked_run_has_no_items_and_says_why(
    producer_for: Callable[..., LiveProducer],
    unresolved_source_tamper: Callable[..., Any],
) -> None:
    row = load(AgentName.SCREENING)[0]
    producer = producer_for(AgentName.SCREENING, [row], tamper=unresolved_source_tamper)
    output = await producer.produce(row)
    assert output.items == []
    assert output.status is RowStatus.BLOCKED
    run = output.run
    assert run is not None
    assert run.status is RowStatus.BLOCKED
    assert run.verdict == RunVerdict.BLOCKED.value
    assert run.stopped_at == "output_shape"
    assert (run.attempts, run.retries) == (2, 1)
    assert run.reason
    assert run.feedback
    assert all("FCRM-2026-9999" in line or "source" in line for line in run.feedback)
    first_attempt = [g for g in run.guards if g.attempt == 1]
    assert (first_attempt[-1].guard, first_attempt[-1].status) == (
        "output_shape",
        "retry",
    )


async def test_a_pack_that_cannot_be_built_is_a_failed_row(
    producer_for: Callable[..., LiveProducer],
) -> None:
    row = load(AgentName.SCREENING)[0]
    producer = producer_for(AgentName.SCREENING, [row])
    broken = row.model_copy(update={"input": {**row.input, "domains": ["moon"]}})
    output = await producer.produce(broken)
    assert output.status is RowStatus.FAILED
    assert output.items == []
    assert output.run is not None
    assert "context_pack_error" in (output.run.reason or "")


async def test_the_runtimes_token_budget_blocks_an_overlong_run(
    producer_for: Callable[..., LiveProducer],
    inflate_output: Callable[..., Any],
) -> None:
    """The cheap tier's budget is a token limit a run; a model that spends more is
    blocked by the real runtime, and the row says ``budget``."""
    row = load(AgentName.SCREENING)[0]
    producer = producer_for(AgentName.SCREENING, [row], tamper=inflate_output)
    output = await producer.produce(row)
    assert output.status is RowStatus.BLOCKED
    assert output.items == []
    assert output.run is not None
    assert output.run.stopped_at == "budget"
    assert "total_tokens_limit" in (output.run.reason or "")
    assert output.run.output_tokens > TIER_BUDGETS[Tier.CHEAP].tokens


async def test_lifted_budgets_are_a_scoped_diagnostic_and_the_limit_comes_back(
    producer_for: Callable[..., LiveProducer],
    inflate_output: Callable[..., Any],
) -> None:
    row = load(AgentName.SCREENING)[0]
    producer = producer_for(AgentName.SCREENING, [row], tamper=inflate_output)
    with lifted_token_budgets(5.0):
        lifted = await producer.produce(row)
    after = await producer.produce(row)
    assert lifted.status is RowStatus.OK
    assert after.status is RowStatus.BLOCKED
    assert after.run is not None
    assert after.run.stopped_at == "budget"


async def test_a_budget_factor_of_one_is_the_shipped_budget(
    producer_for: Callable[..., LiveProducer],
    inflate_output: Callable[..., Any],
) -> None:
    row = load(AgentName.SCREENING)[0]
    producer = producer_for(AgentName.SCREENING, [row], tamper=inflate_output)
    with lifted_token_budgets(1.0):
        output = await producer.produce(row)
    assert output.status is RowStatus.BLOCKED


def test_every_agent_has_a_system_prompt_the_producer_can_load(repo_ai: Path) -> None:
    for agent in AgentName:
        producer = LiveProducer.for_agent(agent, ai_root=repo_ai)
        assert producer.packs is not None
        assert REGISTRY[agent].name is agent


async def test_the_watchlist_pack_prints_the_list_handle_and_the_verbatim_line(
    pack_builder: RowPackBuilder,
) -> None:
    """The first live run found the model citing ``list:jurisdiction_list@...``
    because the pack never showed the ``list_id``, and quoting a line the
    pack did not print."""
    row = next(
        r
        for r in load(AgentName.WATCHLIST)
        if r.input["added"] and any(c["geographies"] for c in r.input["open_cases"])
    )
    pack = await pack_builder.build(row)
    source = row.sources()[0]
    for line in pack.sections["list_diff"].split("\n"):
        country = line.split(" · ")[0]
        assert f"{source.source_ref.list_id}@{source.source_ref.version}" in line  # type: ignore[union-attr]
        assert line.split("line: ")[1] in source.text
        assert country in line.split("line: ")[1]


async def test_the_policy_gap_pack_prints_the_gap_id_the_agent_must_copy(
    pack_builder: RowPackBuilder,
) -> None:
    row = load(AgentName.POLICY_GAP)[0]
    pack = await pack_builder.build(row)
    gap_id = row.input["gap"]["gap_id"]
    assert pack.sections["gaps"].startswith(f"{gap_id} · #")


async def test_a_narrative_pack_shows_the_locked_result_of_every_row(
    pack_builder: RowPackBuilder,
) -> None:
    """Only the demo row has a result before and after the override; the
    others carry one (``composite`` and ``band``), which the pack used to print
    as ``None``."""
    for row in load(AgentName.NARRATIVE):
        pack = await pack_builder.build(row)
        assert "None" not in pack.sections["ratings"], row.row_id
        assert (
            f"composite {row.input.get('composite_after', row.input.get('composite'))}"
            in pack.sections["ratings"]
        )


async def test_every_narrative_pack_gives_a_source_the_recommendation_can_quote(
    pack_builder: RowPackBuilder,
) -> None:
    """Either the locked-result line names the audit record that holds it and
    prints that record's text, or the pack gives the case's title: the two
    things a ``Narrative`` can cite and have its quote verify."""
    for row in load(AgentName.NARRATIVE):
        pack = await pack_builder.build(row)
        texts = [s.text for s in row.sources()]
        ratings, owner_facts = pack.sections["ratings"], pack.sections["owner_facts"]
        named = re.search(r'recorded in audit record #\d+: "([^"]+)"', ratings)
        quoted = named.group(1) if named else owner_facts.split(" · ", 1)[1]
        assert any(quoted in text for text in texts), row.row_id
