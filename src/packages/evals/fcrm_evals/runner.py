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


async def arun(
    agent: AgentName,
    dataset_path: Path,
    metric_fns: Sequence[MetricSpec],
    actual_outputs: Sequence[BaseModel],
    *,
    primary_metric: Optional[str] = None,
    context: Optional[EvalContext] = None,
    timestamp: Optional[datetime] = None,
) -> EvalResult:
    """Score ``actual_outputs`` (one per dataset row, in row order; a bare
    proposal model is wrapped as that row's single item) with
    ``metric_fns``.

    Raises:
        OutputMismatchError: outputs and rows do not align by ``row_id``.
        ValueError: the dataset holds rows of another agent.
    """
    rows = load_rows(dataset_path)
    if any(row.agent is not agent for row in rows):
        msg = f"{dataset_path} holds rows that are not {agent.value} rows"
        raise ValueError(msg)
    outputs = _align(rows, actual_outputs)
    ctx = context or EvalContext()
    table: dict[str, list[Optional[Observation]]] = {s.name: [] for s in metric_fns}
    per_case: list[CaseResult] = []
    for row, output in zip(rows, outputs, strict=True):
        scores: dict[str, Optional[float]] = {}
        flags: list[str] = []
        for spec in metric_fns:
            observation = await _observe(spec, row, output, ctx)
            table[spec.name].append(observation)
            scores[spec.name] = None if observation is None else observation.score
            if observation is not None:
                flags.extend(f"{spec.name}: {f}" for f in observation.flags)
        per_case.append(
            CaseResult(
                row_id=row.row_id, case_id=row.case_id, metrics=scores, flags=flags
            )
        )
    summaries = [
        _summary(s, table[s.name], s.name == primary_metric) for s in metric_fns
    ]
    producers = sorted({o.producer for o in outputs})
    result = EvalResult(
        agent=agent,
        dataset=dataset_name(agent, len(rows)),
        dataset_sha256=file_sha256(dataset_path),
        producer=",".join(producers) or "none",
        n=len(rows),
        primary_metric=primary_metric,
        metrics={s.name: s.score for s in summaries if s.score is not None},
        summaries=summaries,
        per_case=per_case,
        timestamp=timestamp or datetime.now(UTC),
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
