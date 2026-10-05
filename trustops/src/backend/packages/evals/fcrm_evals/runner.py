"""The runner and the promotion gate.

``run(agent, dataset_path, metric_fns, actual_outputs) -> EvalResult``
scores one agent's outputs against its dataset: every metric is applied to
every ``(row, output)`` pair and aggregated micro (see
:mod:`fcrm_evals.metrics.base`). Outputs align with rows by ``row_id``; a
missing or extra output is an error, never silently skipped.

``gate(result, baseline, primary_metric, tolerance)`` is design B §6.4's
promotion rule: a candidate passes when its primary metric is defined, is
``>= baseline - tolerance`` (deterministic metrics use tolerance 0; judge
metrics 0.01 to absorb judge noise), and meets the metric's own threshold.
With no baseline yet (the very first run) only the threshold applies.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Optional

from fcrm_contracts.enums import AgentName
from pydantic import BaseModel, ConfigDict, Field

from fcrm_evals.context import EvalContext
from fcrm_evals.datasets import dataset_name, file_sha256, load_rows
from fcrm_evals.metrics.base import (
    MetricKind,
    MetricSpec,
    Observation,
    ThresholdSource,
    aggregate,
)
from fcrm_evals.outputs import AgentOutput
from fcrm_evals.rows import EvalRow
from fcrm_evals.sampling import SampleInfo
from fcrm_evals.usage import RowRun, RunUsage

logger = logging.getLogger(__name__)

MAX_FLAGS: int = 20
"""Flags kept per metric in a result (the per-case table has the rest)."""


class OutputMismatchError(ValueError):
    """The outputs do not line up one-to-one with the dataset rows."""


class MetricSummary(BaseModel):
    """One metric's aggregate over a dataset."""

    model_config = ConfigDict(extra="forbid")

    name: str
    kind: MetricKind
    score: Optional[float]
    numerator: float
    denominator: float
    rows_scored: int
    threshold: float
    threshold_source: ThresholdSource
    passed: Optional[bool]
    primary: bool
    description: str
    flags: list[str] = Field(default_factory=list)


class CaseResult(BaseModel):
    """Per-row scores (``None``: metric not applicable to the row)."""

    model_config = ConfigDict(extra="forbid")

    row_id: str
    case_id: str
    metrics: dict[str, Optional[float]]
    flags: list[str] = Field(default_factory=list)
    run: Optional[RowRun] = None


class EvalResult(BaseModel):
    """The result of one agent run over one dataset."""

    model_config = ConfigDict(extra="forbid")

    agent: AgentName
    dataset: str
    dataset_sha256: str
    producer: str
    n: int
    primary_metric: Optional[str]
    metrics: dict[str, float]
    summaries: list[MetricSummary]
    per_case: list[CaseResult]
    timestamp: datetime
    usage: Optional[RunUsage] = None
    judge_usage: Optional[RunUsage] = None
    sampled: Optional[SampleInfo] = None
    judge: Optional[str] = None

    def summary(self, name: str) -> Optional[MetricSummary]:
        """The summary of metric ``name``, if it was run."""
        return next((s for s in self.summaries if s.name == name), None)


async def _observe(
    spec: MetricSpec, row: EvalRow, output: AgentOutput, context: EvalContext
) -> Optional[Observation]:
    value = spec.fn(row, output, context)
    if inspect.isawaitable(value):
        return await value
    return value


def _align(rows: Sequence[EvalRow], outputs: Sequence[BaseModel]) -> list[AgentOutput]:
    if len(outputs) != len(rows):
        msg = f"{len(outputs)} outputs for {len(rows)} rows"
        raise OutputMismatchError(msg)
    aligned: list[AgentOutput] = []
    for row, output in zip(rows, outputs, strict=True):
        wrapped = (
            output
            if isinstance(output, AgentOutput)
            else AgentOutput(row_id=row.row_id, producer="direct", items=[output])
        )
        if wrapped.row_id != row.row_id:
            msg = f"output for {wrapped.row_id} where row {row.row_id} was expected"
            raise OutputMismatchError(msg)
        aligned.append(wrapped)
    return aligned


def _summary(
    spec: MetricSpec, observations: Sequence[Optional[Observation]], primary: bool
) -> MetricSummary:
    present = [o for o in observations if o is not None]
    score = aggregate(observations)
    flags = [flag for o in present for flag in o.flags][:MAX_FLAGS]
    return MetricSummary(
        name=spec.name,
        kind=spec.kind,
        score=score,
        numerator=sum(o.numerator for o in present),
        denominator=sum(o.denominator for o in present),
        rows_scored=len(present),
        threshold=spec.threshold,
        threshold_source=spec.threshold_source,
        passed=None if score is None else score >= spec.threshold,
        primary=primary,
        description=spec.description,
        flags=flags,
    )


async def _score_row(
    row: EvalRow,
    output: AgentOutput,
    metric_fns: Sequence[MetricSpec],
    context: EvalContext,
) -> tuple[CaseResult, list[Optional[Observation]]]:
    """Every metric on one row, in suite order."""
    observations = list(
        await asyncio.gather(*(_observe(s, row, output, context) for s in metric_fns))
    )
    flags = [
        f"{spec.name}: {flag}"
        for spec, observation in zip(metric_fns, observations, strict=True)
        if observation is not None
        for flag in observation.flags
    ]
    scores = {
        spec.name: None if observation is None else observation.score
        for spec, observation in zip(metric_fns, observations, strict=True)
    }
    case = CaseResult(
        row_id=row.row_id,
        case_id=row.case_id,
        metrics=scores,
        flags=flags,
        run=output.run,
    )
    return case, observations


async def arun(
    agent: AgentName,
    dataset_path: Path,
    metric_fns: Sequence[MetricSpec],
    actual_outputs: Sequence[BaseModel],
    *,
    primary_metric: Optional[str] = None,
    context: Optional[EvalContext] = None,
    timestamp: Optional[datetime] = None,
    rows: Optional[Sequence[EvalRow]] = None,
    concurrency: int = 1,
    semaphore: Optional[asyncio.Semaphore] = None,
    usage: Optional[RunUsage] = None,
    judge_usage: Optional[RunUsage] = None,
    sampled: Optional[SampleInfo] = None,
    judge: Optional[str] = None,
) -> EvalResult:
    """Score ``actual_outputs`` (one per row, in row order; a bare proposal
    model is wrapped as that row's single item) with ``metric_fns``.

    ``rows`` scores a slice of the dataset (a sampled run) instead of every
    row; ``dataset_path`` still names the file the rows came from, and its
    hash is recorded. ``concurrency`` bounds how many rows are scored at
    once (the judge metrics make network calls); ``semaphore`` is one
    shared with other callers and replaces it. Rows come back in row order
    whatever the bound.

    Raises:
        OutputMismatchError: outputs and rows do not align by ``row_id``.
        ValueError: the dataset holds rows of another agent.
    """
    scored_rows = list(rows) if rows is not None else load_rows(dataset_path)
    if any(row.agent is not agent for row in scored_rows):
        msg = f"{dataset_path} holds rows that are not {agent.value} rows"
        raise ValueError(msg)
    outputs = _align(scored_rows, actual_outputs)
    ctx = context or EvalContext()
    bound = semaphore or asyncio.Semaphore(max(1, concurrency))

    async def bounded(
        row: EvalRow, output: AgentOutput
    ) -> tuple[CaseResult, list[Optional[Observation]]]:
        async with bound:
            return await _score_row(row, output, metric_fns, ctx)

    scored = await asyncio.gather(
        *(bounded(r, o) for r, o in zip(scored_rows, outputs, strict=True))
    )
    per_case = [case for case, _ in scored]
    table: dict[str, list[Optional[Observation]]] = {
        spec.name: [observations[i] for _, observations in scored]
        for i, spec in enumerate(metric_fns)
    }
    summaries = [
        _summary(s, table[s.name], s.name == primary_metric) for s in metric_fns
    ]
    producers = sorted({o.producer for o in outputs})
    if rows is None:
        dataset_rows = len(scored_rows)
    elif sampled is not None:
        dataset_rows = sampled.of
    else:
        dataset_rows = len(load_rows(dataset_path))
    result = EvalResult(
        agent=agent,
        dataset=dataset_name(agent, dataset_rows),
        dataset_sha256=file_sha256(dataset_path),
        producer=",".join(producers) or "none",
        n=len(scored_rows),
        primary_metric=primary_metric,
        metrics={s.name: s.score for s in summaries if s.score is not None},
        summaries=summaries,
        per_case=per_case,
        timestamp=timestamp or datetime.now(UTC),
        usage=usage,
        judge_usage=judge_usage,
        sampled=sampled,
        judge=judge,
    )
    logger.info(
        "%s on %s: %s",
        agent.value,
        result.dataset,
        ", ".join(f"{k}={v:.3f}" for k, v in result.metrics.items()),
    )
    return result


def run(
    agent: AgentName,
    dataset_path: Path,
    metric_fns: Sequence[MetricSpec],
    actual_outputs: Sequence[BaseModel],
    *,
    primary_metric: Optional[str] = None,
    context: Optional[EvalContext] = None,
    timestamp: Optional[datetime] = None,
) -> EvalResult:
    """Synchronous :func:`arun` (for callers outside an event loop)."""
    return asyncio.run(
        arun(
            agent,
            dataset_path,
            metric_fns,
            actual_outputs,
            primary_metric=primary_metric,
            context=context,
            timestamp=timestamp,
        )
    )


class GateStatus(StrEnum):
    """Promotion gate verdict."""

    PASS = "PASS"
    FAIL = "FAIL"


class GateResult(BaseModel):
    """Why the gate passed or failed, with per-metric deltas."""

    model_config = ConfigDict(extra="forbid")

    status: GateStatus
    agent: AgentName
    primary: str
    candidate: Optional[float]
    baseline: Optional[float]
    threshold: Optional[float]
    tolerance: float
    deltas: dict[str, float] = Field(default_factory=dict)
    reasons: list[str] = Field(default_factory=list)


def gate_detail(
    result: EvalResult,
    baseline: Optional[EvalResult],
    primary_metric: str,
    tolerance: float = 0.0,
) -> GateResult:
    """The promotion decision with its reasons (see the module docstring)."""
    reasons: list[str] = []
    summary = result.summary(primary_metric)
    candidate = None if summary is None else summary.score
    base = None if baseline is None else baseline.metrics.get(primary_metric)
    if summary is None or candidate is None:
        reasons.append(f"{primary_metric} was not measured on the candidate")
    else:
        if not summary.passed:
            reasons.append(
                f"{primary_metric} {candidate:.3f} is below its threshold "
                f"{summary.threshold:.2f}"
            )
        if base is not None and candidate < base - tolerance:
            reasons.append(
                f"{primary_metric} {candidate:.3f} is below the baseline "
                f"{base:.3f} (tolerance {tolerance})"
            )
    deltas = {}
    if baseline is not None:
        deltas = {
            name: value - baseline.metrics[name]
            for name, value in result.metrics.items()
            if name in baseline.metrics
        }
    return GateResult(
        status=GateStatus.FAIL if reasons else GateStatus.PASS,
        agent=result.agent,
        primary=primary_metric,
        candidate=candidate,
        baseline=base,
        threshold=None if summary is None else summary.threshold,
        tolerance=tolerance,
        deltas=deltas,
        reasons=reasons,
    )


def gate(
    result: EvalResult,
    baseline: Optional[EvalResult],
    primary_metric: str,
    tolerance: float = 0.0,
) -> bool:
    """True when ``result`` may be promoted over ``baseline``."""
    return (
        gate_detail(result, baseline, primary_metric, tolerance).status
        is GateStatus.PASS
    )
