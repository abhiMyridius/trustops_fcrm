"""Assessment agent output: likelihood/impact per in-scope domain and
proposed control ratings. No scoring tool is available to this agent
(ADR-07) — Caseflow calls fcrm_scoring after these land. Design B §2.4.
"""

from __future__ import annotations

from pydantic import ConfigDict, Field

from fcrm_contracts.enums import Domain
from fcrm_contracts.proposals.provenance import Claim


class FactorProposal(Claim):
    model_config = ConfigDict(extra="forbid")

    domain: Domain
    likelihood: int = Field(ge=1, le=5)
    impact: int = Field(ge=1, le=5)
    justification: str


class ControlProposal(Claim):
    model_config = ConfigDict(extra="forbid")

    control_id: str
    domain: Domain
    rating: float = Field(ge=0.0, le=10.0)
    evidence_artifact: str
    evidence_date: str  # ISO date; staleness is computed by code, not the model
    planned: bool = False
