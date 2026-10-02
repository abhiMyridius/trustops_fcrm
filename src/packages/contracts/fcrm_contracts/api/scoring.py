"""S2 scoring-service request/response and versioned-config shapes.

The engine itself (fcrm_scoring.engine) is pure and has no dependency on
these; these are the HTTP contract so H1, the web client and tests all
agree on one shape. See BUILD-PLAN.md §3.6 and design A §5.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

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
    likelihood: int = Field(ge=1, le=5)
    impact: int = Field(ge=1, le=5)
    controls: list[ControlInput]


class ScoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    scoring_v: int
    domains: list[DomainInput]


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
    active: bool = False
