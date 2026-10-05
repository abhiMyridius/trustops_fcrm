"""The judge seam: an injected callable, k votes, and the offline FakeJudge."""

from __future__ import annotations

from collections.abc import Mapping

import pytest
from fcrm_contracts.enums import AgentName
from fcrm_contracts.proposals.narrative import BusinessSummary, Narrative
from fcrm_evals.context import EvalContext
from fcrm_evals.datasets import load
from fcrm_evals.metrics.judge import (
    NARRATIVE_FAITHFULNESS,
    FakeJudge,
    JudgeContext,
    JudgeMetric,
    JudgeRequest,
    RubricJudgeMetric,
    judge_metric_spec,
    text_of,
)
from fcrm_evals.outputs import AgentOutput, decode_items

DEMO = load(AgentName.NARRATIVE)[0]
DEMO_CTX = JudgeContext.from_row(DEMO)
REAL_TEXT = next(
    i
    for i in decode_items(DEMO.agent, DEMO.expected.reference_outputs)
    if isinstance(i, Narrative)
)


class RecordingJudge:
    def __init__(self, scores: Mapping[str, float]) -> None:
        self.scores = scores
        self.requests: list[JudgeRequest] = []

    async def __call__(self, request: JudgeRequest) -> Mapping[str, float]:
        self.requests.append(request)
        return self.scores


async def test_fake_judge_passes_the_seed_recommendation() -> None:
    metric = RubricJudgeMetric("f", NARRATIVE_FAITHFULNESS, FakeJudge(), threshold=0.9)
    assert await metric.score(REAL_TEXT, DEMO_CTX) == 1.0


async def test_fake_judge_penalises_an_invented_number_and_missing_driver() -> None:
    judge = FakeJudge()
    request = JudgeRequest(
        rubric=NARRATIVE_FAITHFULNESS,
        context=DEMO_CTX,
        output_text="Vendor diligence explains a 47.3 point loss.",
        vote=0,
    )
    verdict = await judge(request)
    assert verdict["no_invented_numbers"] == 0.0
    assert verdict["emphasis"] == 0.0


async def test_fake_judge_is_deterministic_across_votes() -> None:
    judge = FakeJudge()
    first = await judge(
        JudgeRequest(
            rubric=NARRATIVE_FAITHFULNESS, context=DEMO_CTX, output_text="x.", vote=0
        )
    )
    second = await judge(
        JudgeRequest(
            rubric=NARRATIVE_FAITHFULNESS, context=DEMO_CTX, output_text="x.", vote=2
        )
    )
    assert first == second


async def test_rubric_judge_metric_calls_the_injected_judge_k_times() -> None:
    judge = RecordingJudge(
        {"traceable": 1.0, "no_invented_numbers": 0.0, "emphasis": 1.0}
    )
    metric = RubricJudgeMetric("f", NARRATIVE_FAITHFULNESS, judge, threshold=0.9, k=3)
    assert await metric.score(REAL_TEXT, DEMO_CTX) == pytest.approx(0.7)
    assert [r.vote for r in judge.requests] == [0, 1, 2]
    assert judge.requests[0].output_text == REAL_TEXT.text


async def test_rubric_judge_metric_clamps_out_of_range_criteria() -> None:
    judge = RecordingJudge({"traceable": 7.0, "no_invented_numbers": -3.0})
    metric = RubricJudgeMetric("f", NARRATIVE_FAITHFULNESS, judge, threshold=0.9, k=1)
    assert await metric.score(REAL_TEXT, DEMO_CTX) == pytest.approx(0.5)


def test_rubric_judge_metric_needs_at_least_one_vote() -> None:
    with pytest.raises(ValueError, match="k must be"):
        RubricJudgeMetric("f", NARRATIVE_FAITHFULNESS, FakeJudge(), threshold=0.9, k=0)


def test_rubric_judge_metric_satisfies_the_protocol() -> None:
    metric = RubricJudgeMetric("f", NARRATIVE_FAITHFULNESS, FakeJudge(), threshold=0.9)
    assert isinstance(metric, JudgeMetric)


def test_text_of_rejects_items_without_text() -> None:
    assert text_of(BusinessSummary(text="hello")) == "hello"
    with pytest.raises(TypeError, match="no text"):
        text_of(DEMO.expected)


async def test_judge_metric_spec_counts_items_at_or_above_the_pass_mark() -> None:
    judge = RecordingJudge(
        {"traceable": 1.0, "no_invented_numbers": 1.0, "emphasis": 1.0}
    )
    metric = RubricJudgeMetric("f", NARRATIVE_FAITHFULNESS, judge, threshold=0.9)
    spec = judge_metric_spec(metric)
    assert spec.kind == "llm_judge"
    output = AgentOutput(row_id=DEMO.row_id, producer="t", items=[REAL_TEXT])
    obs = await spec.fn(DEMO, output, EvalContext())  # type: ignore[misc]
    assert obs is not None
    assert obs.score == 1.0
    empty = AgentOutput(row_id=DEMO.row_id, producer="t", items=[])
    assert await spec.fn(DEMO, empty, EvalContext()) is None  # type: ignore[misc]
