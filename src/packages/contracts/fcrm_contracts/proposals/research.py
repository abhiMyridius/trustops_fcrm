"""Research agent output: policy clauses that apply and precedent matches.
Clauses are quoted verbatim (rule 8); the agent never paraphrases policy
text. Design B §2.3.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from fcrm_contracts.enums import Domain
from fcrm_contracts.proposals.provenance import Claim


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
