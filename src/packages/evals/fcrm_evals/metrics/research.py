"""Research metrics.

- ``clause_recall`` (design B ``clause_recall_at_k``, >= 0.85, research
  gate): expected clause ids found among the proposed ``ClauseProposal``
  ids / expected. On the demo row the expected set is the four clauses the
  analyst accepted (FFIEC-TPPP-04, FCRM42-3.1, TP71-2.3, OFAC-1.2).
- ``clause_precision`` (>= 0.70): proposed ids that are expected / proposed.
- ``precedent_top1`` (>= 0.90, proposed): the first ``PrecedentMatch`` is the
  labelled closest precedent (only rows that carry one: the demo's 0118).
  Similarity is a number copied from the index, so only the id is judged.
"""

from __future__ import annotations

from typing import Optional

from fcrm_contracts.proposals.research import ClauseProposal, PrecedentMatch

from fcrm_evals.context import EvalContext
from fcrm_evals.metrics.base import Observation, ratio
from fcrm_evals.outputs import AgentOutput
from fcrm_evals.rows import EvalRow


def _proposed_ids(output: AgentOutput) -> list[str]:
    return list(dict.fromkeys(c.clause_id for c in output.of_type(ClauseProposal)))


def clause_recall(
    row: EvalRow, output: AgentOutput, context: EvalContext
) -> Optional[Observation]:
    """|expected ∩ proposed| / |expected|."""
    del context
    expected = list(row.label("relevant_clause_ids", []))
    proposed = set(_proposed_ids(output))
    missed = [cid for cid in expected if cid not in proposed]
    return ratio(
        len(expected) - len(missed), len(expected), [f"missed {m}" for m in missed]
    )


def clause_precision(
    row: EvalRow, output: AgentOutput, context: EvalContext
) -> Optional[Observation]:
    """|proposed ∩ expected| / |proposed|."""
    del context
    expected = set(row.label("relevant_clause_ids", []))
    proposed = _proposed_ids(output)
    extra = [cid for cid in proposed if cid not in expected]
    return ratio(
        len(proposed) - len(extra), len(proposed), [f"extra {e}" for e in extra]
    )


def precedent_top1(
    row: EvalRow, output: AgentOutput, context: EvalContext
) -> Optional[Observation]:
    """1 when the top-ranked precedent is the labelled one."""
    del context
    expected = row.label("top_precedent")
    if expected is None:
        return None
    matches = output.of_type(PrecedentMatch)
    top = matches[0].case_id if matches else None
    return ratio(
        1.0 if top == expected else 0.0, 1.0, [] if top == expected else [f"top {top}"]
    )
