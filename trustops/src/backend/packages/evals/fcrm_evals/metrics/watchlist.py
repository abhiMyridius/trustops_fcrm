"""Watchlist metrics. The candidate set is code (``fcrm_evals.watchlist``);
the agent explains each candidate.

- ``reflag_recall`` (design B, = 1.00, watchlist gate): code candidates
  ``(case_id, jurisdiction)`` with a ``ReflagProposal`` / candidates.
- ``reflag_precision`` (= 1.00, proposed): proposals that are code
  candidates / proposals. An explanation for a case the list diff does not
  touch is an invented re-flag.
"""

from __future__ import annotations

from typing import Optional

from fcrm_contracts.proposals.watchlist import ReflagProposal

from fcrm_evals.context import EvalContext
from fcrm_evals.metrics.base import Observation, ratio
from fcrm_evals.outputs import AgentOutput
from fcrm_evals.rows import EvalRow


def _expected(row: EvalRow) -> set[tuple[str, str]]:
    return {(case_id, j) for case_id, j in row.label("reflags", [])}


def _proposed(output: AgentOutput) -> set[tuple[str, str]]:
    return {(p.case_id, p.jurisdiction) for p in output.of_type(ReflagProposal)}


def reflag_recall(
    row: EvalRow, output: AgentOutput, context: EvalContext
) -> Optional[Observation]:
    """|candidates ∩ proposed| / |candidates|."""
    del context
    expected = _expected(row)
    missed = sorted(expected - _proposed(output))
    return ratio(
        len(expected) - len(missed), len(expected), [f"missed {m}" for m in missed]
    )


def reflag_precision(
    row: EvalRow, output: AgentOutput, context: EvalContext
) -> Optional[Observation]:
    """|proposed ∩ candidates| / |proposed|."""
    del context
    proposed = _proposed(output)
    extra = sorted(proposed - _expected(row))
    return ratio(
        len(proposed) - len(extra), len(proposed), [f"invented {e}" for e in extra]
    )
