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


class FieldSetOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int
    max_required: int
    completion_threshold: float
    fields: list[FieldDef]


class PrecedentOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    title: str
    outcome: str
    closed_date: str
