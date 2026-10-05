"""Narrative metrics (all deterministic; the judge metric is in
:mod:`fcrm_evals.metrics.judge`).

- ``narrative_restricted_terms`` (design B ``restricted_terms_zero``: zero
  hits, expressed here as the fraction of business summaries with no hit,
  threshold = 1.00): the PO-facing ``BusinessSummary`` text is scanned with
  ``fcrm_guardrails`` rule 3's ``restricted_terms_in`` (the same scanner
  Guard 4 and the S1 response scanner use).
- ``number_grounding`` (design B, = 1.00): every number in a ``Narrative``
  or ``BusinessSummary`` text appears in the row's input facts. Computed by
  Guard 4's own ``numbers_grounded`` check (``verify_outputs`` with
  ``grounding_numbers``), so the tokeniser is the guard's.
- ``conditions_traceable`` (design B ``conditions_map_to_weak_controls``,
  = 1.00): every ``Condition.source`` is one of the weak-control / planned /
  gap sources the row's inputs name.
"""

from __future__ import annotations

from typing import Optional

from fcrm_contracts.proposals.narrative import BusinessSummary, Condition, Narrative
from fcrm_guardrails.context import GuardContext
from fcrm_guardrails.output_shape import verify_outputs
from fcrm_guardrails.rules.r3_po_no_restricted import restricted_terms_in

from fcrm_evals.context import EvalContext
from fcrm_evals.metrics.base import Observation, ratio
from fcrm_evals.outputs import AgentOutput
from fcrm_evals.rows import EvalRow

NUMBERS_CHECK = "numbers_grounded"


def narrative_restricted_terms(
    row: EvalRow, output: AgentOutput, context: EvalContext
) -> Optional[Observation]:
    """Clean business summaries / business summaries."""
    del row, context
    summaries = output.of_type(BusinessSummary)
    hits = [(s, restricted_terms_in(s.text)) for s in summaries]
    flags = [f"restricted: {', '.join(terms)}" for _, terms in hits if terms]
    return ratio(sum(1 for _, terms in hits if not terms), len(summaries), flags)


def number_grounding(
    row: EvalRow, output: AgentOutput, context: EvalContext
) -> Optional[Observation]:
    """Texts whose numbers are all grounded / texts with a numbers check."""
    del context
    texts = [i for i in output.items if type(i) in (Narrative, BusinessSummary)]
    if not texts:
        return None
    guard_ctx = GuardContext(
        case_id=row.case_id,
        run_id=f"eval:{row.row_id}",
        agent=row.agent,
        outputs=texts,
        sources=row.sources(),
        grounding_numbers=frozenset(row.label("allowed_numbers", [])),
    )
    checks = [a for a in verify_outputs(guard_ctx) if a.check == NUMBERS_CHECK]
    flags = [f"{a.item_id}: {a.detail}" for a in checks if not a.ok]
    return ratio(sum(1 for a in checks if a.ok), len(checks), flags)


def conditions_traceable(
    row: EvalRow, output: AgentOutput, context: EvalContext
) -> Optional[Observation]:
    """Conditions with a recognised source / conditions (rows that label
    their allowed condition sources only)."""
    del context
    allowed = row.label("condition_sources")
    if allowed is None:
        return None
    conditions = output.of_type(Condition)
    bad = [c for c in conditions if c.source not in set(allowed)]
    flags = [f"{c.condition_id}: untraceable source {c.source!r}" for c in bad]
    return ratio(len(conditions) - len(bad), len(conditions), flags)
