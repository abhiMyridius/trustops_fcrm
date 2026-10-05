"""S3 policy-service request/response shapes: search, corpus, controls,
jurisdictions, field sets, precedents.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


class SearchFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    corpus_version: Optional[int] = None
    trigger_type: Optional[str] = None
    risk_area: Optional[str] = None
    jurisdiction: Optional[str] = None
    closed_date_before: Optional[str] = None


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: str  # "policy_clauses" | "precedent_cases" | "controls"
    query: str
    top_k: int = 10
    filters: SearchFilters = SearchFilters()


class SearchHit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    score: float
    text: str
    metadata: dict[str, Any]


class SearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hits: list[SearchHit]


class ClauseOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clause_id: str
    source_id: str
    title: str
    text: str
    kind: str  # "public" | "synthetic"


class ControlOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    control_id: str
    domain: str
    name: str
    catalogue_weight: float
    evidence_artifact: str
    last_tested: str


class JurisdictionListOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    published: str
    source: str
    call_for_action: list[str]
    increased_monitoring: list[str]
    active: bool


class FieldDef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_id: str
    label: str
    always_required: bool
    change_type: Optional[str] = None


class PrecedentOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    title: str
    outcome: str
    closed_date: str


class JurisdictionVersionOut(BaseModel):
    """``GET /jurisdictions/versions``: one stored jurisdiction list version.
    ``fixture`` marks a synthetic test list (never an FATF publication);
    ``active`` is the one list new work is checked against.
    """

    model_config = ConfigDict(extra="forbid")

    version: str
    published: str
    fixture: bool
    active: bool


class FieldSetOut(BaseModel):
    """``GET /field-sets/{version}``: one stored field-set version."""

    model_config = ConfigDict(extra="forbid")

    version: str
    active: bool
    max_required: int
    completion_threshold: float
    always_required: list[FieldDef]
    per_type: dict[str, list[FieldDef]]


class ResolvedFieldSetOut(BaseModel):
    """``GET /field-sets/{version}/resolve?change_types=a,b``: the fields a
    case with those change types must fill (the always-required set, then each
    type's own fields in the order the types were given, de-duplicated by
    field id). Each per-type field carries the change type that adds it.
    """

    model_config = ConfigDict(extra="forbid")

    version: str
    change_types: list[str]
    fields: list[FieldDef]
    max_required: int
    completion_threshold: float


class ControlVersionOut(BaseModel):
    """``GET /controls/versions``: one stored control-inventory version."""

    model_config = ConfigDict(extra="forbid")

    version: str
    active: bool
    control_count: int


class ControlInventoryOut(BaseModel):
    """``GET /controls?version=&domain=``: a control inventory version,
    optionally narrowed to one risk domain.
    """

    model_config = ConfigDict(extra="forbid")

    version: str
    active: bool
    controls: list[ControlOut]
