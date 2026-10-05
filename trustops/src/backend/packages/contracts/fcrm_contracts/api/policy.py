"""S3 policy-service request/response shapes: search, corpus, controls,
jurisdictions, field sets, precedents.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from fcrm_contracts.enums import Outcome
from fcrm_contracts.proposals.provenance import ClauseSourceRef


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


# ── clauses, policy search and precedent search (I3-C) ────────────────────


class ClauseDetailOut(BaseModel):
    """One policy clause as the system of record holds it: ``GET /clauses/{id}``
    and each item of ``GET /internal/clauses`` (H4's input).

    ``text`` is verbatim from ``clauses.json`` (never generated, never
    rewritten); ``source_ref`` is the structured citation an agent claim carries
    and ``ref`` the human-readable one. ``provenance_kind`` says whether the text
    is a seed verbatim quote or a declared synthetic stand-in (ADR-29).
    """

    model_config = ConfigDict(extra="forbid")

    clause_id: str
    source_id: str
    title: str
    text: str
    ref: str
    source_ref: ClauseSourceRef
    corpus_version: int
    page: Optional[int] = None
    line_start: Optional[int] = None
    line_end: Optional[int] = None
    risk_areas: list[str] = Field(default_factory=list)
    trigger_types: list[str] = Field(default_factory=list)
    jurisdictions: list[str] = Field(default_factory=list)
    quotable: bool = True
    provenance_kind: Optional[str] = None


class PolicySearchWebRequest(BaseModel):
    """``POST /policy/search`` as the web sends it (``{q, case_id}``). ``case_id``
    is for the log line only: it never narrows or re-ranks a search.
    """

    model_config = ConfigDict(extra="forbid")

    q: str = Field(min_length=1, max_length=1000)
    case_id: Optional[str] = Field(default=None, max_length=64)
    top_k: int = Field(default=8, ge=1, le=30)


class PolicyClauseHit(BaseModel):
    """One clause hit of a policy search: the verbatim text under the web's
    name (``quote``), both citations, its place in ``corpus/<source>.md`` and
    its scores. ``similarity`` is the fused rank-agreement score in (0, 1];
    ``vector_score`` and ``text_score`` are the two rankers' own numbers.
    """

    model_config = ConfigDict(extra="forbid")

    clause_id: str
    source_id: str
    title: str
    quote: str
    ref: str
    source_ref: ClauseSourceRef
    page: Optional[int] = None
    line_start: Optional[int] = None
    line_end: Optional[int] = None
    similarity: float
    vector_score: Optional[float] = None
    text_score: Optional[float] = None
    quotable: bool = True
    provenance_kind: Optional[str] = None
    risk_areas: list[str] = Field(default_factory=list)


class PolicySearchResultOut(BaseModel):
    """``POST /policy/search`` response (the web's ``PolicySearchResult``)."""

    model_config = ConfigDict(extra="forbid")

    query: str
    results: list[PolicyClauseHit]
    corpus_version: str


class PrecedentSearchFilters(BaseModel):
    """Metadata filters of a precedent search; every field narrows, none ranks.
    The date bounds are ISO dates (``closed_after`` inclusive, ``closed_before``
    exclusive); the two list fields match any of their values.
    """

    model_config = ConfigDict(extra="forbid")

    trigger_types: list[str] = Field(default_factory=list, max_length=12)
    risk_areas: list[str] = Field(default_factory=list, max_length=12)
    closed_after: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    closed_before: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    outcome: Optional[str] = Field(default=None, max_length=40)


class PrecedentSearchRequest(BaseModel):
    """``POST /precedents/search``: closed cases similar to a case summary."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=2000)
    filters: PrecedentSearchFilters = Field(default_factory=PrecedentSearchFilters)
    top_k: int = Field(default=5, ge=1, le=10)


class PrecedentHitOut(BaseModel):
    """One similar closed case."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    title: str
    similarity: float
    closed_date: str
    outcome: Optional[str] = None
    band: Optional[int] = None
    control_ratings: dict[str, float] = Field(default_factory=dict)


class PrecedentSearchResponse(BaseModel):
    """Precedent hits and, per control, the mean rating over the hits that rated
    it (``precedent_avg``: code, not a model, so the critic can re-derive it).
    """

    model_config = ConfigDict(extra="forbid")

    results: list[PrecedentHitOut]
    precedent_avg: dict[str, float] = Field(default_factory=dict)


class PrecedentRecordIn(BaseModel):
    """``POST /internal/precedents``: the closed-case feedback loop (H1 posts one
    when a case closes). Posting the same ``case_id`` again replaces the row.
    ``outcome`` is stored in the seed's vocabulary (``approved``, ``deferred``,
    ``rejected``) so closed cases and seeded ones filter alike.
    """

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=300)
    change_types: list[str] = Field(default_factory=list, max_length=12)
    domains: list[str] = Field(default_factory=list, max_length=8)
    geographies: list[str] = Field(default_factory=list, max_length=50)
    closed_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    outcome: Outcome
    band: Optional[int] = Field(default=None, ge=1, le=4)
    control_ratings: dict[str, float] = Field(default_factory=dict)
    unit: Optional[str] = Field(default=None, max_length=100)


class PrecedentRecordOut(BaseModel):
    """What ``POST /internal/precedents`` did: ``created`` for a new row,
    ``changed`` when an existing one was replaced with different content;
    ``index_requested`` says whether an ``index.build`` was enqueued (not for
    an identical repost). ``index_version`` is the precedent index version.
    """

    model_config = ConfigDict(extra="forbid")

    case_id: str
    created: bool
    changed: bool
    index_requested: bool
    index_version: int


class PrecedentIndexRecord(BaseModel):
    """One stored precedent as H4 embeds it (``GET /internal/precedents``)."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    title: str
    change_types: list[str]
    domains: list[str]
    geographies: list[str]
    closed_date: str
    outcome: Optional[str] = None
    band: Optional[int] = None
    search_text: str


class JurisdictionActivateOut(BaseModel):
    """``POST /jurisdictions/versions/{version}/activate``: the list that is now
    active, the one it replaced and the change it announced. ``already_active``
    is ``True`` for a repeat of an activation that had already happened (no
    event is published again).
    """

    model_config = ConfigDict(extra="forbid")

    version: str
    previous_version: Optional[str] = None
    fixture: bool
    added: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    already_active: bool = False
