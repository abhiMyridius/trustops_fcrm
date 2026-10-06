"""``fcrm_prompts.InstructionRenderer``: the one place a system prompt is
rendered, for H2 and for the evals alike. Every one of the nine templates
must reach the model fully resolved, with the pinned excerpts the loader's
own ``config()`` cannot supply."""

from __future__ import annotations

from pathlib import Path

import pytest
from fcrm_contracts.enums import AgentName
from fcrm_contracts.versions import PinnedVersions
from fcrm_prompts import (
    NARRATIVE_MODES,
    InstructionRenderer,
    PromptNotFoundError,
    SchemaResolutionError,
    schema_text,
)

REPO_AI = Path(__file__).parents[4] / "ai"
PINS = PinnedVersions(
    scoring_v=3,
    corpus_v=14,
    controls_v=9,
    fieldset_v=6,
    jurisdictions_v="2026-09",
    routing_v=4,
    prompts_v="2026-10-05",
)
OTHER_PINS = PINS.model_copy(update={"routing_v": 5})

pytestmark = pytest.mark.skipif(
    not (REPO_AI / "prompts").is_dir(), reason="ai/ lane not in this checkout"
)


def test_render_all_nine_agents_leaves_no_template_markers() -> None:
    renderer = InstructionRenderer(ai_root=REPO_AI)
    for agent in AgentName:
        text = renderer.render(agent, PINS).text
        assert "{{" not in text, agent
        assert "{%" not in text, agent


def test_render_resolves_the_pinned_routing_excerpt_for_screening() -> None:
    text = InstructionRenderer(ai_root=REPO_AI).render(AgentName.SCREENING, PINS).text
    assert "routing 4" in text
    assert "R4" in text
    assert "{{" not in text


def test_render_resolves_the_corpus_catalogue_for_research() -> None:
    text = InstructionRenderer(ai_root=REPO_AI).render(AgentName.RESEARCH, PINS).text
    assert "clauses" in text


def test_render_narrative_modes_differ_in_their_envelope() -> None:
    renderer = InstructionRenderer(ai_root=REPO_AI)
    completeness = renderer.render(AgentName.NARRATIVE, PINS, "completeness").text
    recommendation = renderer.render(AgentName.NARRATIVE, PINS, "recommendation").text
    assert completeness != recommendation
    assert renderer.render(AgentName.NARRATIVE, PINS).text == recommendation


def test_render_rejects_an_unknown_narrative_mode() -> None:
    with pytest.raises(ValueError, match="narrative mode"):
        InstructionRenderer(ai_root=REPO_AI).render(AgentName.NARRATIVE, PINS, "x")


def test_render_ignores_mode_for_every_other_agent() -> None:
    renderer = InstructionRenderer(ai_root=REPO_AI)
    first = renderer.render(AgentName.SCREENING, PINS, "completeness")
    assert first == renderer.render(AgentName.SCREENING, PINS)


def test_render_is_cached_per_agent_mode_and_pins() -> None:
    renderer = InstructionRenderer(ai_root=REPO_AI)
    first = renderer.render(AgentName.SCREENING, PINS)
    assert renderer.render(AgentName.SCREENING, PINS) is first
    assert renderer.render(AgentName.SCREENING, OTHER_PINS) is not first


def test_render_is_deterministic_across_instances() -> None:
    a = InstructionRenderer(ai_root=REPO_AI).render(AgentName.ASSESSMENT, PINS)
    b = InstructionRenderer(ai_root=REPO_AI).render(AgentName.ASSESSMENT, PINS)
    assert a.sha256 == b.sha256
    assert a.text == b.text


def test_raw_is_the_unrendered_template_body() -> None:
    renderer = InstructionRenderer(ai_root=REPO_AI)
    assert "{{" in renderer.raw(AgentName.SCREENING).text
    assert "{{" not in renderer.render(AgentName.SCREENING, PINS).text


def test_render_without_an_ai_root_raises_prompt_not_found(tmp_path: Path) -> None:
    with pytest.raises(PromptNotFoundError):
        InstructionRenderer(ai_root=tmp_path).render(AgentName.SCREENING, PINS)


def test_schema_text_composes_an_agent_envelope_from_guard_four_types() -> None:
    text = schema_text("WatchlistOutput")
    assert text.startswith("The output is a JSON array")
    assert "ReflagProposal" in text


def test_schema_text_names_a_contract_model_directly() -> None:
    assert "fast_lane" in schema_text("ScreeningProposal")


def test_schema_text_refuses_an_unknown_name() -> None:
    with pytest.raises(SchemaResolutionError):
        schema_text("NoSuchOutput")


def test_narrative_modes_name_both_envelopes() -> None:
    assert NARRATIVE_MODES == {
        "completeness": "CompletenessOutput",
        "recommendation": "RecommendationOutput",
    }
