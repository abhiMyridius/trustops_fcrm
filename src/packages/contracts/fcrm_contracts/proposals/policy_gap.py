"""Policy-gap agent output. Non-blocking oversight job dispatched after
``case.gap_recorded``; reviewed by fcrm_analyst under Assurance (ADR-18:
there is no separate policy_owner role).
"""

from __future__ import annotations

from pydantic import ConfigDict

from fcrm_contracts.proposals.provenance import Claim


class GapSuggestion(Claim):
    model_config = ConfigDict(extra="forbid")

    gap_id: str
    domain: str
    suggested_clause_text: str
    rationale: str
