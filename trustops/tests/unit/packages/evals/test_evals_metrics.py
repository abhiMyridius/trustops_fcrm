"""Deterministic metrics on real dataset rows, correct and mutated outputs."""

from __future__ import annotations

from fcrm_contracts.api.audit import AuditRecord
from fcrm_contracts.enums import AgentName, ChangeType, Domain
from fcrm_contracts.proposals.assessment import ControlProposal, FactorProposal
from fcrm_contracts.proposals.critic import CriticFinding
from fcrm_contracts.proposals.examiner import ExaminerAnswer, ExaminerSentence
from fcrm_contracts.proposals.intake import (
    CategoryProposal,
    ExtractedField,
    ExtractionProposal,
    GapQuestion,
)
from fcrm_contracts.proposals.narrative import BusinessSummary, Narrative
from fcrm_contracts.proposals.policy_gap import GapSuggestion
from fcrm_contracts.proposals.provenance import ClauseSourceRef, ControlSourceRef
from fcrm_contracts.proposals.research import ClauseProposal
from fcrm_contracts.proposals.screening import ScreeningProposal
from fcrm_contracts.proposals.watchlist import ReflagProposal
from fcrm_evals.chains import chain_for, demo_chain
from fcrm_evals.context import EvalContext
from fcrm_evals.datasets import load
from fcrm_evals.metrics import suite_for
from fcrm_evals.metrics.assessment import (
    control_match_precision,
    control_match_recall,
    evidence_date_exact,
    li_within_one,
)
from fcrm_evals.metrics.base import Observation, aggregate, ratio
from fcrm_evals.metrics.citation import citation_validity
from fcrm_evals.metrics.critic import (
    critic_finding_groundedness,
    parse_record_ref,
    planted_error_detection,
)
from fcrm_evals.metrics.examiner import (
    examiner_grounding,
    must_cite_coverage,
    refusal_rate,
)
from fcrm_evals.metrics.intake import (
    category_f1,
    field_extraction_accuracy,
    gap_question_precision,
    normalise_value,
    source_span_accuracy,
)
from fcrm_evals.metrics.narrative import (
    conditions_traceable,
    narrative_restricted_terms,
    number_grounding,
)
from fcrm_evals.metrics.policy_gap import gap_targets_recorded_gap, suggestion_novelty
from fcrm_evals.metrics.research import clause_precision, clause_recall, precedent_top1
from fcrm_evals.metrics.screening import fast_lane_precision, fast_lane_recall
from fcrm_evals.metrics.watchlist import reflag_precision, reflag_recall
from fcrm_evals.outputs import AgentOutput, decode_items
from fcrm_evals.rows import EvalRow
from fcrm_evals.text import NUMBER_PATTERN, numbers_in
from fcrm_guardrails.output_shape import _NUMBER

CTX = EvalContext()


def _row(agent: AgentName, row_id: str) -> EvalRow:
    return next(r for r in load(agent) if r.row_id == row_id)


def _echo(row: EvalRow) -> AgentOutput:
    items = decode_items(row.agent, row.expected.reference_outputs)
    return AgentOutput(row_id=row.row_id, producer="test", items=items)


def _with(row: EvalRow, items: list[object]) -> AgentOutput:
    return AgentOutput.model_validate(
        {"row_id": row.row_id, "producer": "test", "items": items}
    )


RESEARCH = _row(AgentName.RESEARCH, "FCRM-2026-0151")
CLAUSES = [i for i in _echo(RESEARCH).items if isinstance(i, ClauseProposal)]


def test_ratio_returns_none_without_a_denominator() -> None:
    assert ratio(0, 0) is None
    assert ratio(1, 2) == Observation(numerator=1, denominator=2)


def test_aggregate_is_micro_not_a_mean_of_ratios() -> None:
    observations = [ratio(1, 1), ratio(0, 3), None]
    assert aggregate(observations) == 0.25
    assert aggregate([None]) is None


def test_citation_validity_demo_clauses_all_verify() -> None:
    obs = citation_validity(RESEARCH, _echo(RESEARCH), CTX)
    assert obs is not None
    assert (obs.numerator, obs.denominator) == (4, 4)  # PrecedentMatch is no Claim


def test_citation_validity_paraphrase_and_wrong_version_fail() -> None:
    paraphrase = CLAUSES[0].model_copy(
        update={"quote": "banks must evaluate processors' customers and reach"}
    )
    old = CLAUSES[1].model_copy(
        update={"source_ref": ClauseSourceRef(clause_id="FCRM42-3.1", corpus_v=13)}
    )
    obs = citation_validity(
        RESEARCH, _with(RESEARCH, [paraphrase, old, *CLAUSES[2:]]), CTX
    )
    assert obs is not None
    assert (obs.numerator, obs.denominator) == (2, 4)
    assert any("quote_verbatim" in f for f in obs.flags)
    assert any("source_resolves" in f for f in obs.flags)


def test_citation_validity_schema_failure_counts_as_a_failed_claim() -> None:
    wrong_type = ScreeningProposal(case_id="x", fast_lane=False, reasons=[])
    obs = citation_validity(RESEARCH, _with(RESEARCH, [CLAUSES[0], wrong_type]), CTX)
    assert obs is not None
    assert (obs.numerator, obs.denominator) == (1, 2)


def test_citation_validity_empty_output_is_not_applicable() -> None:
    assert citation_validity(RESEARCH, _with(RESEARCH, []), CTX) is None


def test_fast_lane_precision_and_recall_count_the_confusion_matrix() -> None:
    rows = load(AgentName.SCREENING)
    positive = next(r for r in rows if r.label("fast_lane"))
    negative = next(r for r in rows if not r.label("fast_lane"))

    def proposal(row: EvalRow, flag: bool) -> AgentOutput:
        return _with(
            row, [ScreeningProposal(case_id=row.case_id, fast_lane=flag, reasons=[])]
        )

    tp = fast_lane_precision(positive, proposal(positive, True), CTX)
    fp = fast_lane_precision(negative, proposal(negative, True), CTX)
    assert aggregate([tp, fp]) == 0.5
    assert fast_lane_precision(negative, proposal(negative, False), CTX) is None
    assert fast_lane_recall(positive, proposal(positive, False), CTX) == Observation(
        numerator=0, denominator=1, flags=[f"{positive.row_id}: missed"]
    )
    assert fast_lane_recall(negative, proposal(negative, True), CTX) is None
    assert fast_lane_recall(positive, _with(positive, []), CTX).numerator == 0  # type: ignore[union-attr]


def test_clause_recall_and_precision_on_the_demo() -> None:
    three = _with(RESEARCH, CLAUSES[:3])
    recall = clause_recall(RESEARCH, three, CTX)
    assert recall is not None
    assert (recall.numerator, recall.denominator) == (3, 4)
    assert recall.flags == ["missed OFAC-1.2"]
    extra = CLAUSES[0].model_copy(update={"clause_id": "INT-XX-9"})
    precision = clause_precision(RESEARCH, _with(RESEARCH, [*CLAUSES, extra]), CTX)
    assert precision is not None
    assert precision.score == 0.8


def test_precedent_top1_requires_the_labelled_precedent_first() -> None:
    assert precedent_top1(RESEARCH, _echo(RESEARCH), CTX).score == 1.0  # type: ignore[union-attr]
    assert precedent_top1(RESEARCH, _with(RESEARCH, CLAUSES), CTX).score == 0.0  # type: ignore[union-attr]
    synthetic = load(AgentName.RESEARCH)[1]
    assert precedent_top1(synthetic, _echo(synthetic), CTX) is None


ASSESS = _row(AgentName.ASSESSMENT, "FCRM-2026-0151")
ASSESS_ITEMS = _echo(ASSESS).items


def test_li_within_one_fails_a_two_point_miss() -> None:
    factors = [i for i in ASSESS_ITEMS if isinstance(i, FactorProposal)]
    off = factors[0].model_copy(update={"likelihood": factors[0].likelihood - 2})
    obs = li_within_one(ASSESS, _with(ASSESS, [off, *factors[1:]]), CTX)
    assert obs is not None
    assert (obs.numerator, obs.denominator) == (2, 3)
    missing = li_within_one(ASSESS, _with(ASSESS, factors[1:]), CTX)
    assert missing is not None
    assert "missing" in missing.flags[0]


def test_control_match_precision_penalises_an_unmatched_control() -> None:
    controls = [i for i in ASSESS_ITEMS if isinstance(i, ControlProposal)]
    assert len(controls) == 13
    spare = controls[0].model_copy(
        update={
            "control_id": "CTL-T05",
            "source_ref": ControlSourceRef(control_id="CTL-T05", controls_v=9),
        }
    )
    out = _with(ASSESS, [*controls, spare])
    precision = control_match_precision(ASSESS, out, CTX)
    assert precision is not None
    assert (precision.numerator, precision.denominator) == (13, 14)
    recall = control_match_recall(ASSESS, _with(ASSESS, controls[1:]), CTX)
    assert recall is not None
    assert recall.score == 12 / 13


def test_evidence_date_exact_flags_a_moved_date() -> None:
    controls = [i for i in ASSESS_ITEMS if isinstance(i, ControlProposal)]
    moved = controls[0].model_copy(update={"evidence_date": "2026-01-01"})
    obs = evidence_date_exact(ASSESS, _with(ASSESS, [moved, *controls[1:]]), CTX)
    assert obs is not None
    assert obs.score == 12 / 13
    assert obs.flags == ["CTL-T01: 2026-01-01 vs 2023-11-15"]


CRITIC = _row(AgentName.CRITIC, "FCRM-2026-0151")
FINDINGS = [i for i in _echo(CRITIC).items if isinstance(i, CriticFinding)]


def test_parse_record_ref_accepts_hash_and_bare_numbers() -> None:
    assert parse_record_ref("#30") == 30
    assert parse_record_ref(30) == 30
    assert parse_record_ref("record 30") is None


def test_critic_finding_groundedness_resolves_against_the_real_chain() -> None:
    assert critic_finding_groundedness(CRITIC, _echo(CRITIC), CTX).score == 1.0  # type: ignore[union-attr]
    bogus = FINDINGS[0].model_copy(update={"record_refs": ["#999"]})
    empty = FINDINGS[1].model_copy(update={"record_refs": []})
    obs = critic_finding_groundedness(CRITIC, _with(CRITIC, [bogus, empty]), CTX)
    assert obs is not None
    assert obs.score == 0.0


def test_critic_finding_groundedness_tampered_chain_grounds_nothing() -> None:
    def tampered(case_id: str) -> list[AuditRecord]:
        records = chain_for(case_id)
        records[10] = records[10].model_copy(update={"summary": "edited"})
        return records

    ctx = EvalContext(chain_loader=tampered)
    obs = critic_finding_groundedness(CRITIC, _echo(CRITIC), ctx)
    assert obs is not None
    assert obs.score == 0.0


def test_planted_error_detection_requires_type_and_control() -> None:
    assert planted_error_detection(CRITIC, _echo(CRITIC), CTX).score == 1.0  # type: ignore[union-attr]
    retyped = FINDINGS[0].model_copy(update={"finding_type": "other"})
    obs = planted_error_detection(CRITIC, _with(CRITIC, [retyped, FINDINGS[1]]), CTX)
    assert obs is not None
    assert obs.score == 0.5
    planted = _row(
        AgentName.CRITIC, f"{load(AgentName.CRITIC)[1].case_id}~rule_violation"
    )
    assert planted_error_detection(planted, _echo(planted), CTX).score == 1.0  # type: ignore[union-attr]


NARRATIVE = _row(AgentName.NARRATIVE, "FCRM-2026-0151")
NARRATIVE_ITEMS = _echo(NARRATIVE).items


def test_narrative_restricted_terms_catches_a_band_in_the_po_summary() -> None:
    assert narrative_restricted_terms(NARRATIVE, _echo(NARRATIVE), CTX).score == 1.0  # type: ignore[union-attr]
    leaked = BusinessSummary(text="Your change sits in Band 3 and needs review.")
    obs = narrative_restricted_terms(NARRATIVE, _with(NARRATIVE, [leaked]), CTX)
    assert obs is not None
    assert obs.score == 0.0
    assert obs.flags == ["restricted: band"]


def test_number_grounding_rejects_an_invented_number() -> None:
    assert number_grounding(NARRATIVE, _echo(NARRATIVE), CTX).score == 1.0  # type: ignore[union-attr]
    narrative = next(i for i in NARRATIVE_ITEMS if isinstance(i, Narrative))
    invented = narrative.model_copy(update={"text": narrative.text + " Loss 12.5%."})
    obs = number_grounding(NARRATIVE, _with(NARRATIVE, [invented]), CTX)
    assert obs is not None
    assert obs.score == 0.0
    assert "12.5" in obs.flags[0]


def test_number_pattern_matches_guard_four_tokeniser() -> None:
    assert NUMBER_PATTERN.pattern == _NUMBER.pattern
    assert numbers_in(["$10,000 per 30 days", "Band 3."]) == ["10000", "3", "30"]


def test_conditions_traceable_rejects_an_unknown_source() -> None:
    assert conditions_traceable(NARRATIVE, _echo(NARRATIVE), CTX).score == 1.0  # type: ignore[union-attr]
    synthetic = load(AgentName.NARRATIVE)[1]
    assert conditions_traceable(synthetic, _echo(synthetic), CTX) is None


EXAMINER = load(AgentName.EXAMINER)


def test_examiner_grounding_counts_sentences_with_resolving_refs() -> None:
    real = EXAMINER[0]
    assert examiner_grounding(real, _echo(real), CTX).score == 1.0  # type: ignore[union-attr]
    answer = ExaminerAnswer(
        question="q",
        sentences=[
            ExaminerSentence(text="ok", record_refs=[32]),
            ExaminerSentence(text="unknown", record_refs=[999]),
            ExaminerSentence(text="uncited", record_refs=[]),
        ],
    )
    obs = examiner_grounding(real, _with(real, [answer]), CTX)
    assert obs is not None
    assert obs.score == 1 / 3


def test_must_cite_coverage_checks_record_types() -> None:
    real = EXAMINER[0]
    answer = ExaminerAnswer(
        question="q", sentences=[ExaminerSentence(text="s", record_refs=[32, 49])]
    )
    obs = must_cite_coverage(real, _with(real, [answer]), CTX)
    assert obs is not None
    assert obs.score == 0.5


def test_refusal_rate_only_scores_unanswerable_rows() -> None:
    unanswerable = next(r for r in EXAMINER if not r.label("answerable"))
    assert refusal_rate(unanswerable, _echo(unanswerable), CTX).score == 1.0  # type: ignore[union-attr]
    hallucinated = ExaminerAnswer(
        question="q", sentences=[ExaminerSentence(text="made up", record_refs=[1])]
    )
    assert (
        refusal_rate(unanswerable, _with(unanswerable, [hallucinated]), CTX).score
        == 0.0
    )  # type: ignore[union-attr]
    assert refusal_rate(EXAMINER[0], _echo(EXAMINER[0]), CTX) is None


INTAKE = load(AgentName.INTAKE)[0]
INTAKE_ITEMS = _echo(INTAKE).items


def test_normalise_value_ignores_currency_and_spacing() -> None:
    assert normalise_value("$48M  year 1") == normalise_value("48M year 1")


def test_field_extraction_accuracy_counts_wrong_and_missing_values() -> None:
    extraction = [i for i in INTAKE_ITEMS if isinstance(i, ExtractionProposal)]
    assert field_extraction_accuracy(INTAKE, _echo(INTAKE), CTX).score == 1.0  # type: ignore[union-attr]
    first = extraction[0]
    wrong = first.model_copy(
        update={"fields": [first.fields[0].model_copy(update={"value": "nobody"})]}
    )
    obs = field_extraction_accuracy(INTAKE, _with(INTAKE, [wrong]), CTX)
    assert obs is not None
    assert (obs.numerator, obs.denominator) == (0, 9)


def test_source_span_accuracy_requires_overlapping_lines() -> None:
    extraction = [i for i in INTAKE_ITEMS if isinstance(i, ExtractionProposal)]
    field: ExtractedField = extraction[0].fields[0]
    shifted_ref = field.source_ref.model_copy(update={"line_start": 40, "line_end": 41})
    shifted = extraction[0].model_copy(
        update={"fields": [field.model_copy(update={"source_ref": shifted_ref})]}
    )
    obs = source_span_accuracy(INTAKE, _with(INTAKE, [shifted]), CTX)
    assert obs is not None
    assert obs.score == 0.0


def test_category_f1_is_two_tp_over_two_tp_fp_fn() -> None:
    categories = [i for i in INTAKE_ITEMS if isinstance(i, CategoryProposal)]
    extra = categories[0].model_copy(update={"change_type": ChangeType.NEW_SEGMENT})
    obs = category_f1(INTAKE, _with(INTAKE, [*categories[:2], extra]), CTX)
    assert obs is not None
    assert (obs.numerator, obs.denominator) == (4, 6)


def test_gap_question_precision_rejects_a_question_about_a_filled_field() -> None:
    bad = GapQuestion(question_id="Q9", field_id="F01", text="owner?", blocking=False)
    good = GapQuestion(question_id="Q1", field_id="F10", text="licence?", blocking=True)
    obs = gap_question_precision(INTAKE, _with(INTAKE, [good, bad]), CTX)
    assert obs is not None
    assert obs.score == 0.5
    brief_row = load(AgentName.INTAKE)[1]
    assert gap_question_precision(brief_row, _with(brief_row, [good]), CTX) is None


def test_reflag_recall_and_precision() -> None:
    row = next(r for r in load(AgentName.WATCHLIST) if len(r.label("reflags")) == 2)
    proposals = [i for i in _echo(row).items if isinstance(i, ReflagProposal)]
    recall = reflag_recall(row, _with(row, proposals[:1]), CTX)
    assert recall is not None
    assert recall.score == 0.5
    invented = proposals[0].model_copy(update={"case_id": "FCRM-2026-0136"})
    precision = reflag_precision(row, _with(row, [*proposals, invented]), CTX)
    assert precision is not None
    assert precision.score == 2 / 3


def test_policy_gap_metrics_on_target_and_novel() -> None:
    row = load(AgentName.POLICY_GAP)[0]
    suggestion = next(i for i in _echo(row).items if isinstance(i, GapSuggestion))
    assert gap_targets_recorded_gap(row, _echo(row), CTX).score == 1.0  # type: ignore[union-attr]
    assert suggestion_novelty(row, _echo(row), CTX).score == 1.0  # type: ignore[union-attr]
    clause_text = next(
        s.text for s in row.sources() if isinstance(s.source_ref, ClauseSourceRef)
    )
    copy = suggestion.model_copy(
        update={"scope_suggestion": clause_text, "domain": Domain.GEOGRAPHY.value}
    )
    assert suggestion_novelty(row, _with(row, [copy]), CTX).score == 0.0  # type: ignore[union-attr]
    assert gap_targets_recorded_gap(row, _with(row, [copy]), CTX).score == 0.0  # type: ignore[union-attr]


def test_suite_for_every_agent_names_a_primary_it_runs() -> None:
    for agent in AgentName:
        suite = suite_for(agent)
        assert suite.primary in {m.name for m in suite.metrics}


def test_demo_chain_has_sixty_seven_verified_records() -> None:
    assert len(demo_chain()) == 67
    assert CTX.record_seqs("FCRM-2026-0151") == frozenset(range(1, 68))
    assert CTX.record_seqs("FCRM-9999-0000") == frozenset()
