"""Research agent output: policy clauses that apply and precedent matches.
Clauses are quoted verbatim (rule 8); the agent never paraphrases policy
text. Design B §2.3.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from fcrm_contracts.enums import Domain
from fcrm_contracts.proposals.provenance import Claim, Searched


class ClauseProposal(Claim):
    model_config = ConfigDict(extra="forbid")

    clause_id: str
    domain: Domain
    applicability: str  # short agent-written note; the clause text itself is the quote


class PrecedentMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    similarity: float
    outcome: str
    note: str


class PolicyGap(BaseModel):
    """Research's own "I looked and found nothing" output (the
    ``declare_gap`` skill) — distinct from the fuller policy_gap agent's
    :class:`~fcrm_contracts.proposals.policy_gap.GapSuggestion`, which
    proposes what should fill the gap. This is only the honest report
    that a domain has no applicable clause, backed by proof of the
    search (rule 8 applies to absences too: a declared gap must show its
    work, never be asserted bare).
    """

    model_config = ConfigDict(extra="forbid")

    domain: Domain
    description: str
    searched: Searched


class JurisdictionNote(Claim):
    """A jurisdiction-specific observation research surfaces while
    mapping clauses (e.g. a corridor country's screening requirement) —
    distinct from the watchlist agent's :class:`~fcrm_contracts.proposals.
    watchlist.ReflagProposal`, which only fires on a list *change* to an
    open case.
    """

    model_config = ConfigDict(extra="forbid")

    jurisdiction: str
    note: str
