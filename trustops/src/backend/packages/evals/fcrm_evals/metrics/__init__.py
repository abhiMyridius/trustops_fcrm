"""Metrics, and which ones apply to which agent.

:func:`suite_for` returns an agent's metric list and its **primary**
metric (the one the promotion gate compares against the baseline, design
B §6.4). Primaries follow design B §7.3's ``gate`` column, with one
deviation: narrative's design gate is the judge metric
``narrative_faithfulness``; until the Bedrock judge is wired the judge
here is the offline :class:`~fcrm_evals.metrics.judge.FakeJudge`, so the
narrative gate uses the deterministic ``number_grounding`` instead and the
judge metric is reported, not gated.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Optional

from fcrm_contracts.enums import AgentName

from fcrm_evals.metrics import (
    assessment,
    citation,
    critic,
    examiner,
    intake,
    narrative,
    policy_gap,
    research,
    screening,
    watchlist,
)
from fcrm_evals.metrics.base import MetricFn, MetricSpec
from fcrm_evals.metrics.judge import (
    NARRATIVE_FAITHFULNESS,
    FakeJudge,
    JudgeFn,
    RubricJudgeMetric,
    judge_metric_spec,
)

logger = logging.getLogger(__name__)


def _spec(
    name: str,
    fn: MetricFn,
    threshold: float,
    *,
    design: bool,
    description: str,
) -> MetricSpec:
    return MetricSpec(
        name=name,
        fn=fn,
        threshold=threshold,
        threshold_source="design_b" if design else "proposed",
        description=description,
    )


CITATION = _spec(
    "citation_validity",
    citation.citation_validity,
    0.95,
    design=True,
    description="claims whose source resolves and quote is EXACT (Guard 4)",
)


@dataclass(frozen=True)
class AgentSuite:
    """An agent's metrics and the primary metric its gate compares."""

    agent: AgentName
    metrics: tuple[MetricSpec, ...]
    primary: str


def _deterministic(agent: AgentName) -> tuple[tuple[MetricSpec, ...], str]:
    table: dict[AgentName, tuple[tuple[MetricSpec, ...], str]] = {
        AgentName.SCREENING: (
            (
                _spec(
                    "fast_lane_precision",
                    screening.fast_lane_precision,
                    0.90,
                    design=True,
                    description="TP/(TP+FP) vs the R4 predicate",
                ),
                _spec(
                    "fast_lane_recall",
                    screening.fast_lane_recall,
                    0.90,
                    design=True,
                    description="TP/(TP+FN) vs the R4 predicate",
                ),
                CITATION,
            ),
            "fast_lane_precision",
        ),
        AgentName.INTAKE: (
            (
                _spec(
                    "field_extraction_accuracy",
                    intake.field_extraction_accuracy,
                    0.90,
                    design=True,
                    description="normalised exact value match per expected field",
                ),
                _spec(
                    "source_span_accuracy",
                    intake.source_span_accuracy,
                    0.90,
                    design=True,
                    description="same doc/page, overlapping lines",
                ),
                _spec(
                    "category_f1",
                    intake.category_f1,
                    0.90,
                    design=True,
                    description="micro-F1 over change types",
                ),
                _spec(
                    "gap_question_precision",
                    intake.gap_question_precision,
                    0.90,
                    design=True,
                    description="gap questions about really-absent fields",
                ),
                CITATION,
            ),
            "field_extraction_accuracy",
        ),
        AgentName.RESEARCH: (
            (
                _spec(
                    "clause_recall",
                    research.clause_recall,
                    0.85,
                    design=True,
                    description="expected clause ids proposed / expected",
                ),
                _spec(
                    "clause_precision",
                    research.clause_precision,
                    0.70,
                    design=True,
                    description="proposed clause ids expected / proposed",
                ),
                _spec(
                    "precedent_top1",
                    research.precedent_top1,
                    0.90,
                    design=False,
                    description="top precedent is the labelled one",
                ),
                CITATION,
            ),
            "clause_recall",
        ),
        AgentName.ASSESSMENT: (
            (
                _spec(
                    "li_within_one",
                    assessment.li_within_one,
                    0.85,
                    design=True,
                    description="|L-L*|<=1 and |I-I*|<=1 per domain",
                ),
                _spec(
                    "control_match_precision",
                    assessment.control_match_precision,
                    0.90,
                    design=False,
                    description="proposed controls the analyst matched",
                ),
                _spec(
                    "control_match_recall",
                    assessment.control_match_recall,
                    0.90,
                    design=True,
                    description="matched controls the agent proposed",
                ),
                _spec(
                    "evidence_date_exact",
                    assessment.evidence_date_exact,
                    0.95,
                    design=False,
                    description="evidence date equals the seed's",
                ),
                CITATION,
            ),
            "li_within_one",
        ),
        AgentName.CRITIC: (
            (
                _spec(
                    "planted_error_detection",
                    critic.planted_error_detection,
                    0.85,
                    design=True,
                    description="expected findings matched by type and control",
                ),
                _spec(
                    "critic_finding_groundedness",
                    critic.critic_finding_groundedness,
                    1.00,
                    design=False,
                    description="findings whose record refs resolve in the chain",
                ),
                CITATION,
            ),
            "planted_error_detection",
        ),
        AgentName.NARRATIVE: (
            (
                _spec(
                    "number_grounding",
                    narrative.number_grounding,
                    1.00,
                    design=True,
                    description="numbers in text appear in the inputs (Guard 4 check)",
                ),
                _spec(
                    "narrative_restricted_terms",
                    narrative.narrative_restricted_terms,
                    1.00,
                    design=True,
                    description="business summaries with zero restricted terms",
                ),
                _spec(
                    "conditions_traceable",
                    narrative.conditions_traceable,
                    1.00,
                    design=True,
                    description="condition sources are weak/planned/gap refs",
                ),
                CITATION,
            ),
            "number_grounding",
        ),
        AgentName.EXAMINER: (
            (
                _spec(
                    "examiner_grounding",
                    examiner.examiner_grounding,
                    1.00,
                    design=True,
                    description="sentences whose record refs all resolve",
                ),
                _spec(
                    "must_cite_coverage",
                    examiner.must_cite_coverage,
                    0.90,
                    design=False,
                    description="required record types cited",
                ),
                _spec(
                    "refusal_rate",
                    examiner.refusal_rate,
                    0.95,
                    design=True,
                    description="unanswerable questions refused",
                ),
            ),
            "examiner_grounding",
        ),
        AgentName.WATCHLIST: (
            (
                _spec(
                    "reflag_recall",
                    watchlist.reflag_recall,
                    1.00,
                    design=True,
                    description="code candidates with a proposal",
                ),
                _spec(
                    "reflag_precision",
                    watchlist.reflag_precision,
                    1.00,
                    design=False,
                    description="proposals that are code candidates",
                ),
                CITATION,
            ),
            "reflag_recall",
        ),
        AgentName.POLICY_GAP: (
            (
                _spec(
                    "gap_targets_recorded_gap",
                    policy_gap.gap_targets_recorded_gap,
                    1.00,
                    design=False,
                    description="suggestion names a recorded gap and its domain",
                ),
                _spec(
                    "suggestion_novelty",
                    policy_gap.suggestion_novelty,
                    1.00,
                    design=False,
                    description="suggestion copies no existing clause",
                ),
                CITATION,
            ),
            "gap_targets_recorded_gap",
        ),
    }
    return table[agent]


def suite_for(
    agent: AgentName,
    judge: Optional[JudgeFn] = None,
    *,
    votes: int = 3,
    extra: Sequence[MetricSpec] = (),
) -> AgentSuite:
    """The metric suite of ``agent``. Narrative also gets the
    ``narrative_faithfulness`` judge metric over ``judge`` (default: the
    offline :class:`FakeJudge`, logged as such) with ``votes`` judge calls per
    item; ``extra`` metrics (the DeepEval judge metrics of a live run) are
    appended after the agent's own, and never change its primary."""
    metrics, primary = _deterministic(agent)
    if agent is AgentName.NARRATIVE:
        if judge is None:
            logger.info("narrative_faithfulness uses the offline FakeJudge stand-in")
        faithfulness = RubricJudgeMetric(
            "narrative_faithfulness",
            NARRATIVE_FAITHFULNESS,
            judge or FakeJudge(),
            threshold=0.90,
            k=votes,
        )
        metrics = (*metrics, judge_metric_spec(faithfulness))
    return AgentSuite(agent=agent, metrics=(*metrics, *extra), primary=primary)


__all__ = ["AgentSuite", "MetricSpec", "suite_for"]
