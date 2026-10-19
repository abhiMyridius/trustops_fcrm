"""Guard 4: schema, source resolution and verbatim quotes, using the demo
case's real clause quotes and a business-case page."""

from __future__ import annotations

from typing import Any

from fcrm_contracts.enums import AgentName, ChangeType, Domain
from fcrm_contracts.proposals.critic import CriticFinding
from fcrm_contracts.proposals.examiner import ExaminerAnswer, ExaminerSentence
from fcrm_contracts.proposals.intake import ExtractedField, ExtractionProposal
from fcrm_contracts.proposals.narrative import BusinessSummary, Narrative
from fcrm_contracts.proposals.provenance import (
    CaseSourceRef,
    ClauseSourceRef,
    DocSourceRef,
    ListSourceRef,
    RecordSourceRef,
    Searched,
)
from fcrm_contracts.proposals.research import (
    ClauseProposal,
    JurisdictionNote,
    PolicyGap,
    PrecedentMatch,
)
from fcrm_guardrails.base import GuardStatus
from fcrm_guardrails.context import AvailableSource, GuardContext
from fcrm_guardrails.output_shape import (
    AGENT_OUTPUT_TYPES,
    OutputShapeGuard,
    models_not_forbidding_extra,
)
from fcrm_seed.loader import demo_case

CASE = "FCRM-2026-0151"
DEMO = demo_case()
CLAUSE_TEXT = {c["clause_id"]: c["quote"] for c in DEMO["clauses"]}
CLAUSE_SOURCES = [
    AvailableSource(source_ref=ClauseSourceRef(clause_id=cid, corpus_v=14), text=text)
    for cid, text in CLAUSE_TEXT.items()
]
PAGE_1 = "\n".join(
    [
        "Business case: consumer remittances",
        "Change owner: Priya Nair, Payments",
        "Consumer remittances US → Mexico through payout partner "
        "Remesas del Norte S.A.",
        "Segment: retail consumers",
        "Annual volume forecast: $4.8M",
    ]
)
DOC_SOURCE = AvailableSource(
    source_ref=DocSourceRef(doc_id="doc_bc", page=1, line_start=1, line_end=5),
    text=PAGE_1,
)
GUARD = OutputShapeGuard()


def _clause(
    cid: str, quote: str, *, corpus_v: int = 14, confidence: float = 0.9
) -> ClauseProposal:
    return ClauseProposal(
        source_ref=ClauseSourceRef(clause_id=cid, corpus_v=corpus_v),
        quote=quote,
        confidence=confidence,
        clause_id=cid,
        domain=Domain.CHANNEL,
        applicability="payout partner corridor",
    )


def _ctx(
    agent: AgentName, outputs: list[Any], *, attempt: int = 1, **kw: Any
) -> GuardContext:
    return GuardContext.model_validate(
        {
            "case_id": CASE,
            "run_id": "run_1",
            "agent": agent,
            "attempt": attempt,
            "outputs": outputs,
            "sources": kw.pop("sources", [*CLAUSE_SOURCES, DOC_SOURCE]),
            **kw,
        }
    )


GOOD_CLAUSES = [_clause(cid, text) for cid, text in CLAUSE_TEXT.items()]
PARAPHRASE = _clause(
    "FFIEC-TPPP-04",
    "banks must evaluate the processor's customers, volumes and geographic footprint",
)


def test_every_declared_output_model_forbids_extra() -> None:
    for types_ in AGENT_OUTPUT_TYPES.values():
        for model in types_:
            assert models_not_forbidding_extra(model) == []


async def test_check_research_four_verbatim_clauses_pass() -> None:
    result = await GUARD.check(_ctx(AgentName.RESEARCH, GOOD_CLAUSES))
    assert result.status is GuardStatus.PASSED
    assert result.summary == "4 of 4 claims carry verbatim sources · 0 rejected"
    assert result.audit_type == "guard.output"
    assert all(a.ok for a in result.ledger_items)


async def test_check_research_attempt_1_paraphrase_is_retried_with_feedback() -> None:
    """Walkthrough record #28: 'research attempt 1 rejected: 1 clause without
    a verbatim quote'."""
    result = await GUARD.check(
        _ctx(AgentName.RESEARCH, [*GOOD_CLAUSES[1:], PARAPHRASE])
    )
    assert result.status is GuardStatus.RETRY
    assert result.summary.startswith(
        "research attempt 1 rejected: 1 claim(s) without a verbatim quote"
    )
    assert "outputs[3]: quote_verbatim failed" in result.details["feedback"][0]


async def test_check_attempt_2_quote_only_failure_is_dropped() -> None:
    result = await GUARD.check(
        _ctx(AgentName.RESEARCH, [*GOOD_CLAUSES[1:], PARAPHRASE], attempt=2)
    )
    assert result.status is GuardStatus.ANNOTATED
    assert result.details["drop"] == ["outputs[3]"]
    assert result.details["notes_for_analyst"] == ["dropped: unsourced outputs[3]"]


async def test_check_research_policy_gap_passes_with_no_claim_to_verify() -> None:
    """``PolicyGap`` is not a ``Claim`` (it proves an absence via
    ``searched``, not a quote), so rule 2/3 never run on it; only the
    schema check (rule 1) applies. ADR-24 reconciliation: research's
    declared-gap output was missing from ``AGENT_OUTPUT_TYPES``."""
    gap = PolicyGap(
        domain=Domain.CHANNEL,
        description="No clause for cash-pickup agent networks in corpus v14",
        searched=Searched(
            query="cash-pickup agent networks", indexes=["policy_corpus_v14"], hits=0
        ),
    )
    result = await GUARD.check(_ctx(AgentName.RESEARCH, [gap]))
    assert result.status is GuardStatus.PASSED


async def test_check_research_jurisdiction_note_verifies_like_any_claim() -> None:
    """``JurisdictionNote`` is a ``Claim``, so it gets the same source and
    verbatim-quote checks as a ``ClauseProposal``."""
    list_source = AvailableSource(
        source_ref=ListSourceRef(list_id="fatf", version="v2026-06"),
        text="Mexico: not listed",
    )
    note = JurisdictionNote(
        source_ref=ListSourceRef(list_id="fatf", version="v2026-06"),
        quote="Mexico: not listed",
        confidence=0.9,
        jurisdiction="Mexico",
        note="not on the current FATF list",
    )
    result = await GUARD.check(
        _ctx(
            AgentName.RESEARCH,
            [note],
            sources=[*CLAUSE_SOURCES, DOC_SOURCE, list_source],
        )
    )
    assert result.status is GuardStatus.PASSED


async def test_check_attempt_2_unresolved_source_blocks() -> None:
    wrong_version = _clause("OFAC-1.2", CLAUSE_TEXT["OFAC-1.2"], corpus_v=13)
    result = await GUARD.check(_ctx(AgentName.RESEARCH, [wrong_version], attempt=2))
    assert result.status is GuardStatus.BLOCKED
    failed = [a for a in result.ledger_items if not a.ok]
    assert failed[0].check == "source_resolves"
    assert "clause:OFAC-1.2@13" in (failed[0].detail or "")


async def test_check_fuzzy_match_is_not_a_pass() -> None:
    shouted = _clause("OFAC-1.2", CLAUSE_TEXT["OFAC-1.2"].upper())
    result = await GUARD.check(_ctx(AgentName.RESEARCH, [shouted]))
    assert result.status is GuardStatus.RETRY
    assert result.details["fuzzy"] == 1


async def test_check_clause_citing_another_clause_fails_invariant() -> None:
    swapped = ClauseProposal(
        source_ref=ClauseSourceRef(clause_id="OFAC-1.2", corpus_v=14),
        quote=CLAUSE_TEXT["OFAC-1.2"],
        confidence=0.9,
        clause_id="TP71-2.3",
        domain=Domain.CHANNEL,
        applicability="x",
    )
    result = await GUARD.check(_ctx(AgentName.RESEARCH, [swapped]))
    assert any(
        a.check == "clause_ref_matches" and not a.ok for a in result.ledger_items
    )


async def test_check_low_confidence_is_annotated_and_kept() -> None:
    low = _clause("TP71-2.3", CLAUSE_TEXT["TP71-2.3"], confidence=0.3)
    result = await GUARD.check(_ctx(AgentName.RESEARCH, [low]))
    assert result.status is GuardStatus.ANNOTATED
    assert result.details["low_confidence"] == ["outputs[0]"]


async def test_check_intake_page_line_sources_pass() -> None:
    field = ExtractedField(
        source_ref=DocSourceRef(doc_id="doc_bc", page=1, line_start=3, line_end=3),
        quote="Consumer remittances US -> Mexico through payout partner".replace(
            "->", "→"
        ),
        confidence=0.95,
        field_id="F02",
        label="Change description",
        value=DEMO["fields"][1]["value"],
    )
    proposal = ExtractionProposal(case_id=CASE, window_id="w1", fields=[field])
    result = await GUARD.check(_ctx(AgentName.INTAKE, [proposal]))
    assert result.status is GuardStatus.PASSED
    assert result.ledger_items[1].item_id == "outputs[0].fields[0]"


async def test_check_intake_line_outside_page_does_not_resolve() -> None:
    field = ExtractedField(
        source_ref=DocSourceRef(doc_id="doc_bc", page=1, line_start=9, line_end=9),
        quote="Annual volume forecast: $4.8M",
        confidence=0.9,
        field_id="F05",
        label="Volume",
        value="$4.8M",
    )
    proposal = ExtractionProposal(case_id=CASE, window_id="w1", fields=[field])
    result = await GUARD.check(_ctx(AgentName.INTAKE, [proposal]))
    assert result.status is GuardStatus.RETRY
    assert result.ledger_items[1].check == "source_resolves"


async def test_check_wrong_output_type_for_agent_fails_schema() -> None:
    result = await GUARD.check(_ctx(AgentName.SCREENING, GOOD_CLAUSES[:1]))
    assert result.status is GuardStatus.RETRY
    assert result.ledger_items[0].check == "schema"


async def test_check_model_construct_bypass_is_caught() -> None:
    forged = ClauseProposal.model_construct(
        source_ref=ClauseSourceRef(clause_id="OFAC-1.2", corpus_v=14),
        quote=CLAUSE_TEXT["OFAC-1.2"],
        confidence=7.0,  # out of range: never validated
        clause_id="OFAC-1.2",
        domain=Domain.CHANNEL,
        applicability="x",
    )
    result = await GUARD.check(_ctx(AgentName.RESEARCH, [forged]))
    assert result.ledger_items[0].check == "schema"
    assert not result.ledger_items[0].ok


async def test_check_subclass_with_score_field_is_not_the_contract() -> None:
    class ScoredClause(ClauseProposal):
        score: float = 9.8

    sneaky = ScoredClause(**GOOD_CLAUSES[0].model_dump())
    result = await GUARD.check(_ctx(AgentName.RESEARCH, [sneaky]))
    assert result.status is GuardStatus.RETRY
    assert result.ledger_items[0].check == "schema"


async def test_check_precedent_must_be_a_case_the_agent_saw() -> None:
    match = PrecedentMatch(
        case_id="FCRM-2026-0118", similarity=0.82, outcome="approve", note=""
    )
    seen = AvailableSource(source_ref=CaseSourceRef(case_id="FCRM-2026-0118"), text="t")
    ok = await GUARD.check(_ctx(AgentName.RESEARCH, [match], sources=[seen]))
    unseen = await GUARD.check(_ctx(AgentName.RESEARCH, [match], sources=[]))
    assert ok.status is GuardStatus.PASSED
    assert unseen.status is GuardStatus.RETRY


def _records(*seqs: int) -> list[AvailableSource]:
    return [
        AvailableSource(
            source_ref=RecordSourceRef(case_id=CASE, seq=s), text=f"record {s}"
        )
        for s in seqs
    ]


async def test_check_examiner_sentences_cite_existing_records() -> None:
    sentences = [
        ExaminerSentence(text=s["t"], record_refs=s["refs"])
        for s in DEMO["examiner"]["sentences"]
    ]
    answer = ExaminerAnswer(question=DEMO["examiner"]["question"], sentences=sentences)
    all_refs = sorted({r for s in sentences for r in s.record_refs})
    passed = await GUARD.check(
        _ctx(AgentName.EXAMINER, [answer], sources=_records(*all_refs))
    )
    missing = await GUARD.check(
        _ctx(AgentName.EXAMINER, [answer], sources=_records(32, 33))
    )
    assert passed.status is GuardStatus.PASSED
    assert missing.status is GuardStatus.RETRY


async def test_check_critic_finding_records_resolve() -> None:
    raw = DEMO["critic_findings"][0]
    finding = CriticFinding(
        source_ref=RecordSourceRef(case_id=CASE, seq=30),
        quote="record 30 content",
        confidence=0.8,
        finding_type=raw["type"],
        severity=raw["severity"],
        record_refs=raw["records"],
        text=raw["text"],
    )
    sources = [
        AvailableSource(
            source_ref=RecordSourceRef(case_id=CASE, seq=30), text="record 30 content"
        )
    ]
    result = await GUARD.check(_ctx(AgentName.CRITIC, [finding], sources=sources))
    assert result.status is GuardStatus.PASSED


async def test_check_business_summary_with_restricted_term_fails() -> None:
    leaky = BusinessSummary(
        text=DEMO["recommendation"]["text"]
    )  # analyst text: "Band 3"
    clean = BusinessSummary(
        text=(
            "Approved with conditions: a corridor-specific vendor review must be "
            "filed before go-live."
        )
    )
    bad = await GUARD.check(_ctx(AgentName.NARRATIVE, [leaky]))
    good = await GUARD.check(_ctx(AgentName.NARRATIVE, [clean]))
    assert bad.status is GuardStatus.RETRY
    assert "band" in (bad.ledger_items[1].detail or "")
    assert good.status is GuardStatus.PASSED


async def test_check_numbers_must_be_grounded_when_given() -> None:
    narrative = Narrative(
        source_ref=RecordSourceRef(case_id=CASE, seq=50),
        quote="engine recomputed to 10.4",
        confidence=0.9,
        text="The composite moved from 9.8 to 10.4 after the override to 4.5.",
    )
    sources = _records(50)
    sources[0] = AvailableSource(
        source_ref=sources[0].source_ref, text="engine recomputed to 10.4"
    )
    grounded = await GUARD.check(
        _ctx(
            AgentName.NARRATIVE,
            [narrative],
            sources=sources,
            grounding_numbers=frozenset({"9.8", "10.4", "4.5"}),
        )
    )
    invented = await GUARD.check(
        _ctx(
            AgentName.NARRATIVE,
            [narrative],
            sources=sources,
            grounding_numbers=frozenset({"9.8", "10.4"}),
        )
    )
    assert grounded.status is GuardStatus.PASSED
    assert invented.status is GuardStatus.RETRY
    assert "4.5" in (invented.ledger_items[-1].detail or "")


async def test_a_failed_quote_is_named_in_the_feedback_the_retry_is_shown() -> None:
    """The retry is told which quote failed, not only that one did."""
    result = await GUARD.check(_ctx(AgentName.RESEARCH, [PARAPHRASE]))
    assert result.status is GuardStatus.RETRY
    feedback = result.details["feedback"][0]
    assert "quote was" in feedback
    assert "banks must evaluate the processor" in feedback


async def test_check_no_output_fails_closed() -> None:
    result = await GUARD.check(_ctx(AgentName.RESEARCH, []))
    assert result.status is GuardStatus.BLOCKED


async def test_check_no_output_fails_closed_unless_the_contract_allows_it() -> None:
    """The watchlist's "no candidate needs a re-flag" is a correct answer."""
    closed = await GUARD.check(_ctx(AgentName.WATCHLIST, []))
    assert closed.status is GuardStatus.BLOCKED
    assert "failing closed" in closed.summary
    allowed = await GUARD.check(_ctx(AgentName.WATCHLIST, [], may_be_empty=True))
    assert allowed.status is GuardStatus.PASSED
    assert allowed.details["claims"] == 0
    assert allowed.details["empty"] is True


async def test_an_empty_refusal_says_it_was_empty() -> None:
    """A chunked job tolerates a part that found nothing to say when another part
    answered; the merge tells that refusal from any other by ``details["empty"]``."""
    closed = await GUARD.check(_ctx(AgentName.RESEARCH, [], attempt=2))
    assert closed.status is GuardStatus.BLOCKED
    assert closed.details == {"empty": True, "attempt": 2}
    refused = await GUARD.check(_ctx(AgentName.RESEARCH, [PARAPHRASE], attempt=2))
    assert "empty" not in refused.details


async def test_check_may_be_empty_does_not_loosen_a_non_empty_check() -> None:
    """Emptiness is the only thing the flag changes: a bad item still fails."""
    result = await GUARD.check(
        _ctx(AgentName.RESEARCH, [PARAPHRASE], may_be_empty=True)
    )
    assert result.status is GuardStatus.RETRY


async def test_check_category_proposal_quotes_the_page() -> None:
    from fcrm_contracts.proposals.intake import CategoryProposal

    category = CategoryProposal(
        source_ref=DocSourceRef(doc_id="doc_bc", page=1, line_start=3, line_end=3),
        quote="through payout partner Remesas del Norte S.A.",
        confidence=0.9,
        change_type=ChangeType.VENDOR_ONBOARDING,
    )
    result = await GUARD.check(_ctx(AgentName.INTAKE, [category]))
    assert result.status is GuardStatus.PASSED
