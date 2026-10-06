"""Guard 4's source-free checks over a handoff's manual output
(``fcrm_guardrails.manual_output``): the same proposal contracts an agent's
output goes through, nothing the person types is trusted more."""

from __future__ import annotations

from typing import Any

import pytest
from fcrm_agents import REGISTRY
from fcrm_contracts.enums import AgentName, Domain
from fcrm_contracts.proposals.critic import CriticFinding
from fcrm_contracts.proposals.narrative import BusinessSummary
from fcrm_contracts.proposals.provenance import ClauseSourceRef
from fcrm_contracts.proposals.research import ClauseProposal
from fcrm_contracts.proposals.screening import ScreeningProposal
from fcrm_guardrails.manual_output import (
    EMPTY_ALLOWED,
    MAX_ITEMS,
    check_manual_output,
)
from fcrm_guardrails.output_shape import AGENT_OUTPUT_TYPES

CLAUSE_QUOTE = "A bank must evaluate the processor's customers and volumes."


def _clause(clause_id: str = "FFIEC-TPPP-04", *, ref_id: str = "") -> dict[str, Any]:
    return ClauseProposal(
        source_ref=ClauseSourceRef(clause_id=ref_id or clause_id, corpus_v=14),
        quote=CLAUSE_QUOTE,
        confidence=0.9,
        clause_id=clause_id,
        domain=Domain.CHANNEL,
        applicability="payout partner corridor",
    ).model_dump(mode="json")


def test_a_valid_item_is_accepted_in_the_shape_an_agent_dump_has() -> None:
    item = _clause()

    check = check_manual_output(AgentName.RESEARCH, [item])

    assert check.ok
    assert check.accepted == (item,)
    assert check.claims == 1
    assert check.notes == ()


def test_an_item_matching_no_declared_model_is_refused_naming_the_closest() -> None:
    check = check_manual_output(AgentName.RESEARCH, [{"text": "a free paragraph"}])

    assert not check.ok
    assert check.accepted == ()
    [problem] = check.problems
    assert problem.startswith("outputs[0] matches none of ClauseProposal")
    assert "closest is" in problem


def test_a_stray_key_such_as_a_score_is_a_schema_error() -> None:
    item = {**_clause(), "score": 4.1}

    check = check_manual_output(AgentName.RESEARCH, [item])

    assert not check.ok
    assert "score" in check.problems[0]


def test_every_bad_item_is_listed_not_just_the_first() -> None:
    check = check_manual_output(
        AgentName.RESEARCH, [{"nope": 1}, _clause(), "not an object"]
    )

    assert [p.split(" ")[0] for p in check.problems] == ["outputs[0]", "outputs[2]"]
    assert check.problems[1] == "outputs[2] is not an object"
    assert check.accepted == ()


def test_an_item_must_belong_to_the_agents_own_models() -> None:
    """A critic finding is not a research output."""
    finding = CriticFinding(
        source_ref=ClauseSourceRef(clause_id="FFIEC-TPPP-04", corpus_v=14),
        quote=CLAUSE_QUOTE,
        confidence=0.8,
        finding_type="other",
        severity="low",
        record_refs=["12"],
        text="note",
    ).model_dump(mode="json")

    assert check_manual_output(AgentName.CRITIC, [finding]).ok
    assert not check_manual_output(AgentName.RESEARCH, [finding]).ok


def test_empty_fails_closed_unless_the_agent_may_be_empty() -> None:
    refused = check_manual_output(AgentName.RESEARCH, [])
    allowed = check_manual_output(AgentName.CRITIC, [])

    assert refused.problems == ("no output to check; failing closed",)
    assert allowed.ok
    assert allowed.accepted == ()


def test_the_empty_allowed_set_equals_the_agent_registry() -> None:
    """``ai/agents/<name>.agent.yaml may_be_empty``, through the registry."""
    assert {n for n, s in REGISTRY.items() if s.may_be_empty} == EMPTY_ALLOWED


def test_a_clause_whose_source_is_another_clause_is_refused() -> None:
    check = check_manual_output(AgentName.RESEARCH, [_clause(ref_id="OTHER-01")])

    assert check.problems == ("outputs[0]: source_ref is not clause FFIEC-TPPP-04",)


def test_low_confidence_is_noted_and_kept() -> None:
    item = {**_clause(), "confidence": 0.3}

    check = check_manual_output(AgentName.RESEARCH, [item])

    assert check.ok
    assert check.accepted[0]["confidence"] == 0.3
    assert check.notes == ("outputs[0]: low confidence (0.3)",)


def test_a_business_summary_naming_a_restricted_term_is_refused() -> None:
    summary = BusinessSummary(text="The residual score is 10.4 and in band 3.")

    check = check_manual_output(AgentName.NARRATIVE, [summary.model_dump(mode="json")])

    assert not check.ok
    assert check.problems[0].startswith("outputs[0]: restricted:")


def test_a_clean_business_summary_is_accepted() -> None:
    summary = BusinessSummary(text="The vendor review is scheduled for November.")

    assert check_manual_output(
        AgentName.NARRATIVE, [summary.model_dump(mode="json")]
    ).ok


def test_the_screening_proposal_round_trips() -> None:
    item = ScreeningProposal(
        case_id="FCRM-2026-0151", fast_lane=False, reasons=[]
    ).model_dump(mode="json")

    check = check_manual_output(AgentName.SCREENING, [item])

    assert check.ok
    assert check.claims == 0


def test_more_than_the_cap_is_refused_without_reading_the_items() -> None:
    check = check_manual_output(AgentName.RESEARCH, [_clause()] * (MAX_ITEMS + 1))

    assert check.problems == (
        f"{MAX_ITEMS + 1} items; at most {MAX_ITEMS} are accepted",
    )


@pytest.mark.parametrize("agent", list(AGENT_OUTPUT_TYPES))
def test_every_agent_has_declared_models_to_check_against(agent: AgentName) -> None:
    assert AGENT_OUTPUT_TYPES[agent]
    assert not check_manual_output(agent, [{"unknown": "shape"}]).ok


def test_a_claim_with_a_quote_too_short_to_prove_anything_is_refused() -> None:
    item = {**_clause(), "quote": "the bank"}

    check = check_manual_output(AgentName.RESEARCH, [item])

    assert check.problems == ("outputs[0]: quote is under 12 characters",)
