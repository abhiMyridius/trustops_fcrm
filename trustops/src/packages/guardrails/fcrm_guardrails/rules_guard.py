"""Guard 5: RulesGuard (state; design B §4.6).

Runs the eight rules (:mod:`fcrm_guardrails.rules`) and blocks on any
failure; the caller refuses the state change with the failing rule ids in
the error. Two callers, one guard:

- **H2**, right after Guard 4, on agent outputs: rule 1 over every output,
  rule 8 over every ``ClauseProposal`` against the clause sources in the
  context (design B §5.1 "RulesGuard(rules 1,7,8)"). Rule 7 reads a scoring
  input, which only exists in H1, so in H2 it reports "not applicable".
- **H1**, before every state change, with ``ctx.rules`` filled for the
  transition at hand (decisions, the PO response, the override, the
  ``ScoreRequest`` about to be posted, and so on).

Inputs from ``ctx.rules`` take precedence; H2-derived inputs fill only what
``ctx.rules`` left empty. The audit record (``guard.rules``) carries the
eight booleans; the summary reads like the walkthrough's "8 of 8 rules
kept".
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Optional

from fcrm_contracts.proposals.provenance import ClauseSourceRef
from fcrm_contracts.proposals.research import ClauseProposal

from fcrm_guardrails.base import Guard, GuardName, GuardResult, GuardStatus
from fcrm_guardrails.rules import RulesContext, check_all

if TYPE_CHECKING:
    from fcrm_guardrails.context import GuardContext


def rules_context_for(ctx: GuardContext) -> RulesContext:
    """Merge ``ctx.rules`` with the inputs H2's outputs provide."""
    base = ctx.rules or RulesContext()
    updates: dict[str, Any] = {}
    if base.proposals is None and ctx.outputs:
        updates["proposals"] = list(ctx.outputs)
    clauses = [o for o in ctx.outputs if isinstance(o, ClauseProposal)]
    if base.clauses is None and clauses:
        updates["clauses"] = clauses
    if base.corpus is None and clauses:
        pairs: list[tuple[ClauseSourceRef, str]] = [
            (s.source_ref, s.text)
            for s in ctx.sources
            if isinstance(s.source_ref, ClauseSourceRef)
        ]
        updates["corpus"] = {ref.clause_id: text for ref, text in pairs}
        versions = {ref.corpus_v for ref, _ in pairs}
        if base.corpus_v is None and len(versions) == 1:
            updates["corpus_v"] = versions.pop()
    return base.model_copy(update=updates) if updates else base


class RulesGuard(Guard):
    """Guard 5: the eight rules before any state change."""

    name = GuardName.RULES
    stage = "state"

    async def check(self, ctx: GuardContext) -> GuardResult:
        """Run :func:`check_all` and block on any broken rule."""
        started = time.perf_counter()
        results = check_all(rules_context_for(ctx))
        kept_count = sum(1 for r in results if r.ok)
        failures = {r.rule_id: r.detail for r in results if not r.ok}
        details: dict[str, Any] = {
            "rules": {r.rule_id: r.ok for r in results},
            "not_applicable": [r.rule_id for r in results if not r.applicable],
            "failures": failures,
        }
        summary = f"{kept_count} of {len(results)} rules kept"
        status = GuardStatus.PASSED
        broken_ids: Optional[str] = ", ".join(failures) if failures else None
        if broken_ids is not None:
            status = GuardStatus.BLOCKED
            summary = f"{summary} · broken: {broken_ids}"
        return self._result(status, summary, started=started, details=details)
