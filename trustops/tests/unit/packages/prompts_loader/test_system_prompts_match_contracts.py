"""The rendered system prompts against the output contracts they ask for.

The first live run found prompts that named fields the contract does not have
(the examiner's ``answered`` and ``records_read``, the intake's ``not_found``),
that put control ids where Guard 4 accepts sequence numbers (the critic's
``record_refs``) and that never said which source a claim cites. These tests
keep the nine prompts and the contracts from drifting apart again."""

from __future__ import annotations

from pathlib import Path

import pytest
from fcrm_contracts.enums import AgentName
from fcrm_contracts.versions import PinnedVersions
from fcrm_guardrails.output_shape import AGENT_OUTPUT_TYPES
from fcrm_prompts import InstructionRenderer

REPO_AI = Path(__file__).parents[4] / "ai"
PINS = PinnedVersions(
    scoring_v=3,
    corpus_v=14,
    controls_v=9,
    fieldset_v=6,
    jurisdictions_v="2026-09",
    routing_v=4,
    prompts_v="2026-10-06.2",
)
NARRATIVE_ITEMS = {
    "completeness": ("CompletenessReport", "Question"),
    "recommendation": ("Narrative", "Condition", "BusinessSummary"),
}
CITING = (
    AgentName.INTAKE,
    AgentName.RESEARCH,
    AgentName.ASSESSMENT,
    AgentName.WATCHLIST,
    AgentName.POLICY_GAP,
)

pytestmark = pytest.mark.skipif(
    not (REPO_AI / "prompts").is_dir(), reason="ai/ lane not in this checkout"
)


def _text(agent: AgentName, mode: str | None = None) -> str:
    return InstructionRenderer(ai_root=REPO_AI).render(agent, PINS, mode).text


@pytest.mark.parametrize(
    "agent", [a for a in AgentName if a is not AgentName.NARRATIVE]
)
def test_a_system_prompt_names_every_item_type_its_contract_allows(
    agent: AgentName,
) -> None:
    text = _text(agent)
    for item in AGENT_OUTPUT_TYPES[agent]:
        assert item.__name__ in text, f"{agent.value}: {item.__name__}"


@pytest.mark.parametrize("mode", sorted(NARRATIVE_ITEMS))
def test_the_narrative_prompt_names_the_item_types_of_its_mode(mode: str) -> None:
    text = _text(AgentName.NARRATIVE, mode)
    for name in NARRATIVE_ITEMS[mode]:
        assert name in text
    other = next(m for m in NARRATIVE_ITEMS if m != mode)
    for name in NARRATIVE_ITEMS[other]:
        assert name not in text, f"{mode} prompt mentions {name}"


def test_the_narrative_modes_together_are_the_contract() -> None:
    names = {n for items in NARRATIVE_ITEMS.values() for n in items}
    assert names == {t.__name__ for t in AGENT_OUTPUT_TYPES[AgentName.NARRATIVE]}


@pytest.mark.parametrize("agent", list(AgentName))
def test_no_system_prompt_pastes_the_schema_the_output_tool_already_sends(
    agent: AgentName,
) -> None:
    assert "The output is a JSON array" not in _text(agent)
    assert '"properties"' not in _text(agent)


@pytest.mark.parametrize(
    ("agent", "mode"),
    [(a, None) for a in AgentName if a is not AgentName.NARRATIVE]
    + [(AgentName.NARRATIVE, m) for m in sorted(NARRATIVE_ITEMS)],
)
def test_no_system_prompt_carries_case_data(agent: AgentName, mode: str | None) -> None:
    text = _text(agent, mode)
    assert "FCRM-2026-0" not in text
    assert "Remesas" not in text
    assert "Priya" not in text


@pytest.mark.parametrize("agent", CITING)
def test_an_agent_that_cites_is_told_the_case_field_handle(agent: AgentName) -> None:
    assert "{kind: case, case_id}" in _text(agent)


def test_screening_is_told_its_one_citation_with_an_example() -> None:
    text = _text(AgentName.SCREENING)
    assert '{kind: "case", case_id:' in text
    assert 'never `kind: "doc"`' in text
    assert "Example:" in text


def test_screening_applies_the_whole_r4_predicate_itself() -> None:
    """Nothing applies the volume or the change-type test to the agent's answer
    (H1 opens the fast-lane gate on the proposal as given), so the prompt must not
    say code did, and must state the two cases ``is_fast_lane_candidate`` accepts."""
    from fcrm_caseflow.routing import is_fast_lane_candidate

    text = _text(AgentName.SCREENING)
    assert "Code already applied" not in text
    assert "the only change type on the identity line is vendor_onboarding" in text
    assert "the only change type is new_feature" in text
    assert "under $5M" in text
    assert "EMPTY or missing F05" in text
    # the two cases the prompt names are the two the code accepts
    assert is_fast_lane_candidate(
        ["vendor_onboarding"], 0.0, new_geography=False, new_segment=False
    )
    assert is_fast_lane_candidate(
        ["new_feature"], 4_999_999.0, new_geography=False, new_segment=False
    )
    assert not is_fast_lane_candidate(
        ["new_feature"], 5_000_000.0, new_geography=False, new_segment=False
    )


def test_research_says_a_clause_proposals_two_ids_are_one() -> None:
    """Guard 4 ``clause_ref_matches`` refuses a proposal whose ``source_ref``
    names another clause than its ``clause_id``; one such item cost a demo-case
    run its retry."""
    assert "the same id as in its source_ref" in _text(AgentName.RESEARCH)


def test_the_examiner_prompt_asks_for_what_examiner_answer_can_hold() -> None:
    text = _text(AgentName.EXAMINER)
    for field in ("answered", "records_read", "refusal reason"):
        assert field not in text
    assert "record_refs" in text
    assert "quotes" in text
    assert "no sentences" in text


def test_the_critic_prompt_keeps_control_ids_out_of_record_refs() -> None:
    text = _text(AgentName.CRITIC)
    assert "digits only" in text
    assert "ctl:" not in text
    assert "{kind: record, case_id, seq}" in text


def test_the_watchlist_prompt_matches_reflag_proposal() -> None:
    text = _text(AgentName.WATCHLIST)
    assert "cases_considered" not in text
    assert "rerun" in text
    assert "empty list" in text


def test_the_policy_gap_prompt_matches_gap_suggestion() -> None:
    text = _text(AgentName.POLICY_GAP)
    for stale in ("closed_gaps", "suggested_clause_text"):
        assert stale not in text
    assert "scope_suggestion" in text
    assert "gap_id" in text


def test_the_intake_prompt_asks_for_nothing_the_contract_cannot_carry() -> None:
    text = _text(AgentName.INTAKE)
    for stale in ("not_found", "facts_flagged", "notes_for_analyst"):
        assert stale not in text


def test_agents_that_may_return_nothing_are_told_it_is_a_valid_answer() -> None:
    assert "empty list" in _text(AgentName.CRITIC)
    assert "empty list" in _text(AgentName.WATCHLIST)
    assert "empty list" in _text(AgentName.POLICY_GAP)
