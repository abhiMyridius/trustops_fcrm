"""``citation_validity`` (design B §7.3; threshold >= 0.95).

Of the ``Claim`` items in an output, the fraction whose ``source_ref``
resolves to a source the agent was shown *and* whose ``quote`` is an
``EXACT`` verbatim match in that source. It is computed by running Guard
4's own verification (``fcrm_guardrails.output_shape.verify_outputs``) over
the output with the row's sources, so the normalisation, the minimum quote
length, the document line tolerance and the "fuzzy is not a pass" rule are
the guard's, not a second implementation. Measured on attempt 1 in a live
run (design B: Guard 4 makes the final, post-retry rate 100%).

An output item that fails the schema check counts as one invalid claim
(its claims are never inspected, so it must not shrink the denominator).
"""

from __future__ import annotations

from typing import Optional

from fcrm_contracts.enums import AgentName
from fcrm_guardrails.context import AvailableSource, GuardContext
from fcrm_guardrails.output_shape import QUOTE_CHECK, verify_outputs

from fcrm_evals.context import EvalContext
from fcrm_evals.metrics.base import Observation, ratio
from fcrm_evals.outputs import AgentOutput
from fcrm_evals.rows import EvalRow

SOURCE_CHECK = "source_resolves"
SCHEMA_CHECK = "schema"


def shown_sources(row: EvalRow) -> list[AvailableSource]:
    """The sources the agent was shown for ``row``: the row's own, except that
    the critic's assessment record is widened to its summary plus the compact
    ledger payload, exactly as production's ``cite_records`` registers it and as
    the live pack gives it to the model (:func:`fcrm_evals.live.critic_sources`).
    The metric must check a quote against the text the model could read, or a
    finding that quotes the rating it is about reads as unsourced."""
    sources = row.sources()
    if row.agent is not AgentName.CRITIC:
        return sources
    from fcrm_evals.live import critic_sources  # heavy; only the critic needs it

    return critic_sources(row, sources)


def citation_validity(
    row: EvalRow, output: AgentOutput, context: EvalContext
) -> Optional[Observation]:
    """Verified claims / claims (see the module docstring)."""
    del context  # sources come from the row; no chain lookup needed
    if not output.items:
        return None
    guard_ctx = GuardContext(
        case_id=row.case_id,
        run_id=f"eval:{row.row_id}",
        agent=row.agent,
        outputs=list(output.items),
        sources=shown_sources(row),
    )
    annotations = verify_outputs(guard_ctx)
    claims = [a for a in annotations if a.check == SOURCE_CHECK]
    schema_failures = [a for a in annotations if a.check == SCHEMA_CHECK and not a.ok]
    verified = [a for a in annotations if a.check == QUOTE_CHECK and a.ok]
    failures = [
        f"{a.item_id}: {a.check} ({a.detail})"
        for a in annotations
        if not a.ok and a.check in (SOURCE_CHECK, QUOTE_CHECK, SCHEMA_CHECK)
    ]
    return ratio(len(verified), len(claims) + len(schema_failures), failures)
