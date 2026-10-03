"""Screening metrics (design B §7.3: ``fast_lane_precision`` / ``recall``,
both >= 0.90; precision is the screening gate).

Per row the agent's proposed ``fast_lane`` is compared with the label the
real R4 predicate produced. A row with no ``ScreeningProposal`` counts as
"not proposed" (a missing proposal is never a fast lane). Precision is
TP / (TP + FP) and recall TP / (TP + FN) over the whole dataset (micro).
"""

from __future__ import annotations

from typing import Optional

from fcrm_contracts.proposals.screening import ScreeningProposal

from fcrm_evals.context import EvalContext
from fcrm_evals.metrics.base import Observation, ratio
from fcrm_evals.outputs import AgentOutput
from fcrm_evals.rows import EvalRow


def proposed_fast_lane(output: AgentOutput) -> bool:
    """The output's fast-lane proposal (``False`` when absent)."""
    proposals = output.of_type(ScreeningProposal)
    return bool(proposals) and proposals[0].fast_lane


def fast_lane_precision(
    row: EvalRow, output: AgentOutput, context: EvalContext
) -> Optional[Observation]:
    """Contributes 1 to the denominator per proposed fast lane, 1 to the
    numerator when the label agrees."""
    del context
    if not proposed_fast_lane(output):
        return None
    expected = bool(row.label("fast_lane"))
    flags = [] if expected else [f"{row.row_id}: false positive"]
    return ratio(1.0 if expected else 0.0, 1.0, flags)


def fast_lane_recall(
    row: EvalRow, output: AgentOutput, context: EvalContext
) -> Optional[Observation]:
    """Contributes 1 to the denominator per labelled fast lane, 1 to the
    numerator when the agent proposed it."""
    del context
    if not row.label("fast_lane"):
        return None
    hit = proposed_fast_lane(output)
    return ratio(1.0 if hit else 0.0, 1.0, [] if hit else [f"{row.row_id}: missed"])
