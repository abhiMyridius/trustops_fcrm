"""Assessment agent output: likelihood/impact per in-scope domain and
proposed control ratings. No scoring tool is available to this agent
(ADR-07) — Caseflow calls fcrm_scoring after these land. Design B §2.4.
"""

from __future__ import annotations

from typing import Optional, Self

from pydantic import ConfigDict, Field, model_validator

from fcrm_contracts.enums import Domain
from fcrm_contracts.proposals.provenance import Claim


class FactorProposal(Claim):
    model_config = ConfigDict(extra="forbid")

    domain: Domain
    likelihood: int = Field(ge=1, le=5)
    impact: int = Field(ge=1, le=5)
    justification: str


class ControlProposal(Claim):
    """A control's proposed rating. ``rating`` is ``None`` exactly when
    ``planned=True`` — a planned control has no rating by construction
    (rule 7), not by convention the caller must remember to honour.
    ``rationale`` is always present: for a scored control it explains the
    rating; for a planned one it explains why none is given yet (e.g. "a
    review is scheduled for Nov 2026").
    """

    model_config = ConfigDict(extra="forbid")

    control_id: str
    domain: Domain
    rating: Optional[float] = Field(default=None, ge=0.0, le=10.0)
    rationale: str
    evidence_artifact: str
    evidence_date: str  # ISO date; staleness is computed by code, not the model
    planned: bool = False

    @model_validator(mode="after")
    def _rating_matches_planned(self) -> Self:
        if self.planned and self.rating is not None:
            msg = "a planned control must not carry a rating (rule 7)"
            raise ValueError(msg)
        if not self.planned and self.rating is None:
            msg = "a non-planned control must carry a rating"
            raise ValueError(msg)
        return self
