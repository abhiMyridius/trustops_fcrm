"""S2 scoring-service request/response and versioned-config shapes.

The engine itself (fcrm_scoring.engine) is pure and has no dependency on
these; these are the HTTP contract so H1, the web client and tests all
agree on one shape. See BUILD-PLAN.md §3.6 and design A §5.

Structural validity is enforced here, at the contract, so the engine can
stay a pure arithmetic function and a malformed request is a 422 from
Pydantic rather than a wrong score from the engine: a request must carry at
least one domain, no domain twice, no control id twice (a repeated control
would otherwise be double-counted and the last ``catalogue_weight`` would
silently win for every occurrence), and every control must sit inside the
domain it names. Likelihood and impact are strict integers (``true`` and
``"3"`` are rejected, not coerced).
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from fcrm_contracts.enums import Band, Domain, Route


class ControlInput(BaseModel):
    """One control's rating as confirmed (or AI-proposed) for scoring.
    A planned control must never appear here (rule 7); the engine does not
    special-case it — it is the caller's job to exclude it.
    """

    model_config = ConfigDict(extra="forbid")

    control_id: str
    domain: Domain
    rating: float = Field(ge=0.0, le=10.0)
    catalogue_weight: float = Field(gt=0.0, le=1.0)


class DomainInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: Domain
    likelihood: int = Field(ge=1, le=5, strict=True)
    impact: int = Field(ge=1, le=5, strict=True)
    controls: list[ControlInput]

    @model_validator(mode="after")
    def _controls_belong_to_this_domain(self) -> DomainInput:
        """Every control's ``domain`` must equal the enclosing domain."""
        strays = [c.control_id for c in self.controls if c.domain is not self.domain]
        if strays:
            msg = (
                f"controls {strays} name a different domain than the "
                f"{self.domain.value!r} domain that contains them"
            )
            raise ValueError(msg)
        return self


class ScoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    scoring_v: int
    domains: list[DomainInput] = Field(min_length=1)

    @model_validator(mode="after")
    def _domains_and_controls_are_unique(self) -> ScoreRequest:
        """No domain may appear twice and no control id may appear twice
        anywhere in the request."""
        seen_domains: set[Domain] = set()
        duplicate_domains: list[str] = []
        for d in self.domains:
            if d.domain in seen_domains:
                duplicate_domains.append(d.domain.value)
            seen_domains.add(d.domain)
        if duplicate_domains:
            msg = f"domains {duplicate_domains} appear more than once"
            raise ValueError(msg)

        seen_controls: set[str] = set()
        duplicate_controls: list[str] = []
        for d in self.domains:
            for c in d.controls:
                if c.control_id in seen_controls:
                    duplicate_controls.append(c.control_id)
                seen_controls.add(c.control_id)
        if duplicate_controls:
            msg = f"control ids {duplicate_controls} appear more than once"
            raise ValueError(msg)
        return self


class DomainResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: Domain
    inherent: int
    control_effectiveness: float
    retention: float
    residual: float
    domain_weight_normalised: float
    contribution: float


class ScoreResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    scoring_v: int
    domains: list[DomainResult]
    composite: float
    band: Band
    route: Route
    boundary_flag: bool
    boundary_distance: Optional[float] = None


class BandDef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    band: Band
    label: str
    lower: float
    upper: float
    route: Route


class ScoringModelVersion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int
    likelihood_scale: dict[int, str]
    impact_scale: dict[int, str]
    domain_weights: dict[Domain, float]
    retention_floor_pct: float  # 40.0
    retention_factor_pct: float  # 60.0
    bands: list[BandDef]
    boundary_flag_distance: float  # 0.5
    planned_controls_scored: bool  # False
    owner: str
    approved: str  # ISO date
    active: bool = False


class RoutingRule(BaseModel):
    """One of R1..R6 as data (ADR-19 as D-19 origin; routing v4)."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str  # "R1".."R6"
    label: str
    description: str
    params: dict[str, Any] = {}


class RoutingRulesVersion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int
    rules: list[RoutingRule]
    committee_quorum: int = 3
    #: The routing parameter table published with the rules (key to value text,
    #: e.g. ``{"quorum": "3"}``); empty for a version seeded before the
    #: configuration screens existed.
    parameters: dict[str, str] = Field(default_factory=dict)
    active: bool = False
