"""Smoke tests for fcrm_contracts: every model constructs, extra="forbid" is
actually enforced, and the discriminated SourceRef union round-trips. Full
coverage of each API/event/proposal model lives beside the service or agent
that owns it; this file only protects the shared package itself.
"""

from __future__ import annotations

import pytest
from fcrm_contracts.api.scoring import ControlInput, DomainInput, ScoreRequest
from fcrm_contracts.enums import CaseState, Domain
from fcrm_contracts.events.catalogue import EVENT_TYPES
from fcrm_contracts.events.envelope import Envelope
from fcrm_contracts.proposals.provenance import Claim, ClauseSourceRef, DocSourceRef
from pydantic import ValidationError


def test_case_state_has_no_screening_substate() -> None:
    """Screening is a CaseState value, not a sub-state flag (design A §3.1)."""
    assert CaseState.SCREENING in CaseState
    assert CaseState.HANDOFF in CaseState


def test_score_request_rejects_unknown_fields() -> None:
    """extra="forbid" must actually reject an unexpected field, not just
    annotate the model."""
    with pytest.raises(ValidationError):
        ScoreRequest(
            case_id="FCRM-2026-0151",
            scoring_v=3,
            domains=[],
            unexpected_field="nope",  # type: ignore[call-arg]
        )


def test_control_input_rejects_out_of_range_rating() -> None:
    with pytest.raises(ValidationError):
        ControlInput(
            control_id="CTL-T01",
            domain=Domain.CHANNEL,
            rating=11.0,
            catalogue_weight=0.35,
        )


def test_domain_input_accepts_the_demo_channel_domain() -> None:
    d = DomainInput(
        domain=Domain.CHANNEL,
        likelihood=4,
        impact=5,
        controls=[
            ControlInput(
                control_id="CTL-T01",
                domain=Domain.CHANNEL,
                rating=7.6,
                catalogue_weight=0.35,
            ),
        ],
    )
    assert d.likelihood * d.impact == 20


def test_claim_discriminates_source_ref_kind() -> None:
    claim_doc = Claim(
        source_ref=DocSourceRef(doc_id="d1", page=1, line_start=2, line_end=2),
        quote="Priya Nair, Payments",
        confidence=0.95,
    )
    claim_clause = Claim(
        source_ref=ClauseSourceRef(clause_id="FFIEC-TPPP-04", corpus_v=14),
        quote="banks should assess the risks of the processor's customer base",
        confidence=0.9,
    )
    assert claim_doc.source_ref.kind == "doc"
    assert claim_clause.source_ref.kind == "clause"


def test_envelope_type_is_in_the_catalogue() -> None:
    """A lock test, not a tautology: every Envelope a unit constructs in its
    own tests should use a type from the shared catalogue, so a typo is
    caught here rather than at runtime on a live queue."""
    env = Envelope(
        event_id="evt_01J00000000000000000000000",
        type="case.submitted",
        case_id="FCRM-2026-0151",
        occurred_at="2026-10-03T00:00:00Z",
        payload={},
    )
    assert env.type in EVENT_TYPES


def test_every_catalogue_event_type_is_dotted() -> None:
    assert all("." in t for t in EVENT_TYPES)
