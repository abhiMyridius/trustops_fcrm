"""Runner alignment, aggregation and the promotion gate."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fcrm_contracts.enums import AgentName
from fcrm_contracts.proposals.screening import ScreeningProposal
from fcrm_evals.datasets import dataset_path, load
from fcrm_evals.metrics import suite_for
from fcrm_evals.outputs import AgentOutput, decode_items
from fcrm_evals.runner import (
    EvalResult,
    GateStatus,
    OutputMismatchError,
    arun,
    gate,
    gate_detail,
    run,
)

STAMP = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
SCREENING = load(AgentName.SCREENING)
SUITE = suite_for(AgentName.SCREENING)


def _outputs(flip_every: int = 0) -> list[AgentOutput]:
    outputs = []
    for n, row in enumerate(SCREENING):
        items = decode_items(row.agent, row.expected.reference_outputs)
        proposal = items[0]
        assert isinstance(proposal, ScreeningProposal)
        if flip_every and n % flip_every == 0:
            proposal = proposal.model_copy(update={"fast_lane": not proposal.fast_lane})
        outputs.append(AgentOutput(row_id=row.row_id, producer="t", items=[proposal]))
    return outputs


def _run(outputs: list[AgentOutput]) -> EvalResult:
    return run(
        AgentName.SCREENING,
        dataset_path(AgentName.SCREENING),
        SUITE.metrics,
        outputs,
        primary_metric=SUITE.primary,
        timestamp=STAMP,
    )


BASELINE = _run(_outputs())
WORSE = _run(_outputs(flip_every=5))


def test_run_reference_outputs_score_one_on_every_metric() -> None:
    assert BASELINE.metrics == {
        "fast_lane_precision": 1.0,
        "fast_lane_recall": 1.0,
        "citation_validity": 1.0,
    }
    assert BASELINE.n == 100
    assert BASELINE.dataset == "screening/fast-lane-100"
    assert BASELINE.timestamp == STAMP
    assert len(BASELINE.per_case) == 100


def test_run_flipped_outputs_have_lower_precision() -> None:
    summary = WORSE.summary("fast_lane_precision")
    assert summary is not None
    assert summary.primary
    assert summary.score is not None
    assert summary.score < 0.9
    assert summary.passed is False
    assert summary.numerator < summary.denominator


def test_run_wraps_bare_proposals_as_single_items() -> None:
    bare = [o.items[0] for o in _outputs()]
    result = _run(bare)  # type: ignore[arg-type]
    assert result.metrics == BASELINE.metrics
    assert result.producer == "direct"


def test_run_rejects_a_missing_output() -> None:
    with pytest.raises(OutputMismatchError, match="99 outputs for 100 rows"):
        _run(_outputs()[:-1])


def test_run_rejects_misaligned_row_ids() -> None:
    outputs = _outputs()
    outputs[0], outputs[1] = outputs[1], outputs[0]
    with pytest.raises(OutputMismatchError, match="where row"):
        _run(outputs)


async def test_arun_rejects_another_agents_dataset() -> None:
    with pytest.raises(ValueError, match="not screening rows"):
        await arun(
            AgentName.SCREENING,
            dataset_path(AgentName.CRITIC),
            SUITE.metrics,
            [],
        )


def test_gate_passes_an_equal_candidate() -> None:
    assert gate(BASELINE, BASELINE, "fast_lane_precision") is True


def test_gate_fails_a_worse_candidate_with_reasons_and_deltas() -> None:
    detail = gate_detail(WORSE, BASELINE, "fast_lane_precision")
    assert detail.status is GateStatus.FAIL
    assert detail.baseline == 1.0
    assert detail.candidate is not None
    assert detail.candidate < 1.0
    assert any("below the baseline" in r for r in detail.reasons)
    assert detail.deltas["fast_lane_precision"] < 0
    assert gate(WORSE, BASELINE, "fast_lane_precision") is False


def test_gate_passes_an_improvement_over_a_worse_baseline() -> None:
    assert gate(BASELINE, WORSE, "fast_lane_precision") is True


def test_gate_tolerance_absorbs_a_small_drop_but_not_the_threshold() -> None:
    lowered = BASELINE.model_copy(
        update={"metrics": {**BASELINE.metrics, "fast_lane_precision": 1.005}}
    )
    assert gate(BASELINE, lowered, "fast_lane_precision", tolerance=0.01) is True
    assert gate(BASELINE, lowered, "fast_lane_precision", tolerance=0.0) is False


def test_gate_without_baseline_applies_the_threshold_only() -> None:
    assert gate(BASELINE, None, "fast_lane_precision") is True
    assert gate(WORSE, None, "fast_lane_precision") is False


def test_gate_fails_an_unmeasured_primary_metric() -> None:
    detail = gate_detail(BASELINE, BASELINE, "no_such_metric")
    assert detail.status is GateStatus.FAIL
    assert detail.reasons == ["no_such_metric was not measured on the candidate"]


def test_result_round_trips_through_json(tmp_path: Path) -> None:
    path = tmp_path / "r.json"
    path.write_text(WORSE.model_dump_json(), encoding="utf-8")
    assert EvalResult.model_validate_json(path.read_text()) == WORSE
