"""Tests for the proposal-schema gaps the ai/ layer lane flagged as
blocking real agent specs: a planned control's rating, the research/
policy_gap "declare a gap honestly" shapes, examiner groundedness quotes,
watchlist's list-change detail, and intake's new-fact flag.
"""

from __future__ import annotations

import pytest
from fcrm_contracts.enums import Domain
from fcrm_contracts.proposals.assessment import ControlProposal
from fcrm_contracts.proposals.examiner import ExaminerSentence
from fcrm_contracts.proposals.intake import FactFlag
from fcrm_contracts.proposals.policy_gap import GapSuggestion
from fcrm_contracts.proposals.provenance import (
    ClauseSourceRef,
    ControlSourceRef,
    DocSourceRef,
    Searched,
)
from fcrm_contracts.proposals.research import JurisdictionNote, PolicyGap
from fcrm_contracts.proposals.watchlist import ReflagProposal
from pydantic import ValidationError

_CONTROL_SRC = ControlSourceRef(control_id="CTL-T01", controls_v=9)


def _control(**overrides: object) -> ControlProposal:
    defaults: dict[str, object] = {
        "source_ref": _CONTROL_SRC,
        "quote": "domestic-scope audit, 2023",
        "confidence": 0.7,
        "control_id": "CTL-T01",
        "domain": Domain.CHANNEL,
        "rating": 7.6,
        "rationale": "2023 domestic audit; no corridor review yet",
        "evidence_artifact": "audit_2023.pdf",
        "evidence_date": "2023-11-15",
        "planned": False,
    }
    defaults.update(overrides)
    return ControlProposal(**defaults)  # type: ignore[arg-type]


def test_a_scored_control_requires_a_rating() -> None:
    with pytest.raises(ValidationError):
        _control(rating=None, planned=False)


def test_a_planned_control_must_not_carry_a_rating() -> None:
    with pytest.raises(ValidationError):
        _control(rating=4.5, planned=True)


def test_a_planned_control_with_no_rating_is_valid() -> None:
    control = _control(
        rating=None,
        planned=True,
        rationale="corridor DD review scheduled Nov 2026 (scope letter)",
    )
    assert control.rating is None
    assert control.planned is True


def test_a_scored_control_with_a_rating_is_valid() -> None:
    control = _control()
    assert control.rating == pytest.approx(7.6)


def test_searched_records_the_query_and_hit_count() -> None:
    s = Searched(query="cash-pickup agent networks", indexes=["policy_clauses"], hits=0)
    assert s.hits == 0
    with pytest.raises(ValidationError):
        Searched(query="x", indexes=["policy_clauses"], hits=-1)


def test_research_policy_gap_requires_a_searched_block() -> None:
    gap = PolicyGap(
        domain=Domain.CHANNEL,
        description="No clause for cash-pickup agent networks in corpus v14",
        searched=Searched(
            query="cash pickup agent", indexes=["policy_clauses"], hits=0
        ),
    )
    assert gap.searched.hits == 0


def test_jurisdiction_note_is_a_claim() -> None:
    note = JurisdictionNote(
        source_ref=ClauseSourceRef(clause_id="INT-FCRM-4.2", corpus_v=14),
        quote="new corridors require jurisdiction screening at onboarding",
        confidence=0.9,
        jurisdiction="MX",
        note="Mexico is not on the current or fixture high-risk list",
    )
    assert note.jurisdiction == "MX"


def test_examiner_sentence_quotes_default_to_empty_and_are_settable() -> None:
    bare = ExaminerSentence(text="The score was 9.8.", record_refs=[32])
    assert bare.quotes == []

    sourced = ExaminerSentence(
        text="The score was first computed at 9.8, Band 2.",
        record_refs=[32],
        quotes=["score.computed 9.8 Band 2"],
    )
    assert sourced.quotes == ["score.computed 9.8 Band 2"]


def test_reflag_proposal_carries_the_list_change_and_rerun_flag() -> None:
    reflag = ReflagProposal(
        source_ref=ClauseSourceRef(clause_id="FATF", corpus_v=14),
        quote="FATF high-risk and increased-monitoring lists",
        confidence=0.95,
        case_id="FCRM-2026-0144",
        jurisdiction="Algeria",
        list_version="v2026-10-fixture",
        explanation="Algeria, removed in June, is re-added on the fixture list",
        list_change="added",
        rerun=True,
    )
    assert reflag.rerun is True
    assert reflag.list_change == "added"


def test_gap_suggestion_uses_scope_suggestion_not_the_old_field_name() -> None:
    suggestion = GapSuggestion(
        source_ref=ClauseSourceRef(clause_id="TP71-2.3", corpus_v=14),
        quote="independent assessment of the agent network's AML controls",
        confidence=0.8,
        gap_id="GAP-1",
        domain="channel",
        scope_suggestion="Extend Standard 7.1 to cover cash-pickup agent networks",
        rationale="0151 and two other cases hit this gap",
        cases=["FCRM-2026-0151"],
        nearest_clauses=["TP71-2.3"],
        suggested_standard="Internal Third-Party AML Standard 7.1",
        searched=Searched(
            query="cash pickup agent network", indexes=["policy_clauses"], hits=0
        ),
    )
    assert suggestion.scope_suggestion.startswith("Extend Standard 7.1")
    assert not hasattr(suggestion, "suggested_clause_text")


def test_fact_flag_matches_the_demo_s_cash_pickup_case() -> None:
    flag = FactFlag(
        source_ref=DocSourceRef(
            doc_id="payout_partner_contract", page=5, line_start=1, line_end=1
        ),
        quote="cash pickup at partner agents",
        confidence=0.85,
        field_id="F21",
        text="cash pickup not in the business case",
        kind="new_fact",
    )
    assert flag.kind == "new_fact"
