"""The metric contract.

A metric is a function ``(row, output, context) -> Optional[Observation]``
(sync or async). An :class:`Observation` is a ``numerator / denominator``
pair rather than a ready-made ratio, so the runner aggregates **micro**
(sum of numerators over sum of denominators): precision over 100 screening
rows is TP / (TP + FP) across the whole dataset, not a mean of per-row
ratios. ``None`` means "not applicable to this row" (e.g. field accuracy on
a categories-only row) and contributes nothing.

Every metric is higher-is-better and passes when its aggregate score is
``>= threshold``. Thresholds are design B §7.3 values where it gives one
(``threshold_source="design_b"``); otherwise they are this lane's reasoned
default (``"proposed"``), stated in the metric's docstring.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

from fcrm_evals.context import EvalContext
from fcrm_evals.outputs import AgentOutput
from fcrm_evals.rows import EvalRow


class Observation(BaseModel):
    """One row's contribution to a metric."""

    model_config = ConfigDict(extra="forbid")

    numerator: float = Field(ge=0.0)
    denominator: float = Field(gt=0.0)
    flags: list[str] = Field(default_factory=list)

    @property
    def score(self) -> float:
        """This row's own ratio (for the per-case table)."""
        return self.numerator / self.denominator


MetricFn = Callable[
    [EvalRow, AgentOutput, EvalContext],
    Union[Optional[Observation], Awaitable[Optional[Observation]]],
]
MetricKind = Literal["deterministic", "llm_judge"]
ThresholdSource = Literal["design_b", "proposed"]


@dataclass(frozen=True)
class MetricSpec:
    """A named metric with its threshold and provenance."""

    name: str
    fn: MetricFn
    threshold: float
    threshold_source: ThresholdSource
    description: str
    kind: MetricKind = "deterministic"


def ratio(
    numerator: float, denominator: float, flags: Sequence[str] = ()
) -> Optional[Observation]:
    """An observation, or ``None`` when there is nothing to measure."""
    if denominator <= 0:
        return None
    return Observation(numerator=numerator, denominator=denominator, flags=list(flags))


def aggregate(observations: Sequence[Optional[Observation]]) -> Optional[float]:
    """Micro-average: ``sum(numerators) / sum(denominators)``; ``None`` when
    no row produced an observation."""
    present = [o for o in observations if o is not None]
    denominator = sum(o.denominator for o in present)
    if denominator <= 0:
        return None
    return sum(o.numerator for o in present) / denominator
