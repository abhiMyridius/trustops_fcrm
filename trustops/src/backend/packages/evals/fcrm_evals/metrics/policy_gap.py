"""Policy-gap metrics. ``suggestion_acceptance`` (accepted / reviewed) is a
live metric (design B: tracked, no gate until >= 10 reviewed) and cannot be
computed offline; these two are what can be.

- ``gap_targets_recorded_gap`` (= 1.00, proposed; policy_gap gate offline):
  suggestions whose ``gap_id`` is a recorded gap and whose ``domain`` matches
  it / suggestions.
- ``suggestion_novelty`` (= 1.00, proposed): suggestions whose clause text
  does not reproduce an existing clause shown to the agent, checked with
  Guard 4's verbatim matcher in both directions (an existing clause quoted
  inside the suggestion, or the suggestion found inside a clause). A "gap"
  answered by copying existing policy is not a gap suggestion.
"""

from __future__ import annotations

from typing import Optional

from fcrm_contracts.proposals.policy_gap import GapSuggestion
from fcrm_contracts.proposals.provenance import ClauseSourceRef
from fcrm_guardrails.quote_match import MatchKind, match_quote

from fcrm_evals.context import EvalContext
from fcrm_evals.metrics.base import Observation, ratio
from fcrm_evals.outputs import AgentOutput
from fcrm_evals.rows import EvalRow


def gap_targets_recorded_gap(
    row: EvalRow, output: AgentOutput, context: EvalContext
) -> Optional[Observation]:
    """On-target suggestions / suggestions."""
    del context
    gap_ids = set(row.label("gap_ids", []))
    domain = row.label("domain")
    suggestions = output.of_type(GapSuggestion)
    bad = [s for s in suggestions if s.gap_id not in gap_ids or s.domain != domain]
    flags = [f"{s.gap_id}/{s.domain}: not a recorded gap" for s in bad]
    return ratio(len(suggestions) - len(bad), len(suggestions), flags)


def _copies(text: str, clause: str) -> bool:
    """True when either text contains the other (exact or case-folded)."""
    return (
        match_quote(clause, text).kind is not MatchKind.NONE
        or match_quote(text, clause).kind is not MatchKind.NONE
    )


def suggestion_novelty(
    row: EvalRow, output: AgentOutput, context: EvalContext
) -> Optional[Observation]:
    """Suggestions that copy no existing clause / suggestions."""
    del context
    clauses = [
        s.text for s in row.sources() if isinstance(s.source_ref, ClauseSourceRef)
    ]
    suggestions = output.of_type(GapSuggestion)
    copied = [
        s for s in suggestions if any(_copies(s.scope_suggestion, c) for c in clauses)
    ]
    flags = [f"{s.gap_id}: reproduces an existing clause" for s in copied]
    return ratio(len(suggestions) - len(copied), len(suggestions), flags)
