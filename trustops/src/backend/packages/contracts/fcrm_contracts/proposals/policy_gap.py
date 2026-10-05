"""Policy-gap agent output. Non-blocking oversight job dispatched after
``case.gap_recorded``; reviewed by fcrm_analyst under Assurance (ADR-18:
there is no separate policy_owner role).
"""

from __future__ import annotations

from pydantic import ConfigDict

from fcrm_contracts.proposals.provenance import Claim, Searched


class GapSuggestion(Claim):
    """The policy_gap agent's fuller analysis of a gap research already
    declared (:class:`~fcrm_contracts.proposals.research.PolicyGap`):
    which real cases hit it, the closest existing clauses, and which
    internal standard should likely be extended to cover it. Reviewed by
    ``fcrm_analyst`` under Assurance (ADR-18); never auto-applied to the
    corpus.
    """

    model_config = ConfigDict(extra="forbid")

    gap_id: str
    domain: str
    scope_suggestion: str
    rationale: str
    cases: list[str] = []
    nearest_clauses: list[str] = []
    suggested_standard: str
    searched: Searched
