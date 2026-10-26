"""S3's configuration screens: ``policy/config/{section}`` and its draft,
preview and publish routes.

Every model mirrors an interface of the web's ``src/frontend/src/api/types/
config.ts`` by name and by key (ADR-30: the web's shapes are the contract), and
``tests/unit/packages/contracts/test_config_contract_matches_web_types.py``
parses that file to keep it so. Two names are exceptions: the web's
``BandDef`` is :class:`ConfigBandDef` here (``fcrm_contracts.api.scoring``
already has a different ``BandDef``), and the web's generic
``ConfigSectionBase<K, D>`` is one subclass per section key.

A section is read as ``data`` (what the page renders) next to its version
history and its server-side draft; the draft is a list of single-value changes
(``path``, ``before``, ``after``) the analyst accumulates, which publishing
turns into a new immutable snapshot version.
"""

from __future__ import annotations

from typing import Annotated, Literal, Optional, Union

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from fcrm_contracts.enums import ChangeType, Domain

ConfigSectionKey = Literal[
    "field-sets",
    "categories",
    "scoring-model",
    "control-inventory",
    "policy-corpus",
    "routing-rules",
    "jurisdiction-risk",
    "ai-agents",
]

#: A version note (the publish reason) shorter than this is refused.
NOTE_MIN_LENGTH = 20


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConfigVersion(_Strict):
    """One row of a section's append-only history."""

    version: str
    published_at: str
    by: str
    note: str
    active: bool


class ConfigDraftChange(_Strict):
    """One change in the working set: ``path`` names a value, ``before`` is the
    value in force when the change was made and ``after`` the one it becomes.
    """

    path: str
    before: str
    after: str


class ConfigDraft(_Strict):
    """The section's server-side working set. ``etag`` changes on every write
    and names the published version the draft started from.
    """

    etag: str
    changes: list[ConfigDraftChange]


# ── section data (what each page renders) ───────────────────────────────────


class FieldRef(_Strict):
    id: str
    label: str


class AlwaysRequiredField(_Strict):
    id: str
    label: str
    note: Optional[str]


class PerTypeFields(_Strict):
    type: ChangeType
    label: str
    fields: list[FieldRef]


class AmlCategoryRef(_Strict):
    """One of the four FFIEC BSA/AML risk categories (a scoring domain)."""

    domain: Domain
    label: str


class TypeAmlCategories(_Strict):
    """The AML risk categories a change type brings a case into: the risk areas
    the case is assessed in (Risk & Controls)."""

    type: ChangeType
    label: str
    categories: list[AmlCategoryRef]


class FieldSetData(_Strict):
    max_required: int
    completion_threshold: float
    always_required: list[AlwaysRequiredField]
    per_type: list[PerTypeFields]
    aml_mapping: list[TypeAmlCategories]


class CategoryType(_Strict):
    type: ChangeType
    label: str
    domains: list[Domain]
    signals: list[str]


class FastLane(_Strict):
    volume_under_usd: int
    conditions: list[str]


class CategoriesData(_Strict):
    types: list[CategoryType]
    fast_lane: FastLane


class DomainWeight(_Strict):
    domain: Domain
    label: str
    weight: float


class ScaleAnchor(_Strict):
    value: int
    label: str


class ConfigBandDef(_Strict):
    """The web's ``BandDef`` (``band``, ``min``, ``max``, ``route``, ``label``)."""

    band: Literal[1, 2, 3, 4]
    min: float
    max: float
    route: Literal["analyst", "committee"]
    label: str


class ScoringModelData(_Strict):
    domain_weights: list[DomainWeight]
    likelihood_scale: list[ScaleAnchor]
    impact_scale: list[ScaleAnchor]
    bands: list[ConfigBandDef]
    retention_factor: float
    retention_floor: float
    boundary_flag: float
    rounding: str
    approved: str
    owner: str


class ControlRow(_Strict):
    id: str
    domain: Domain
    name: str
    weight: float
    evidence_artefact: str
    last_tested: str
    stale: bool


class DomainWeightSum(_Strict):
    domain: Domain
    sum: float


class ControlInventoryData(_Strict):
    controls: list[ControlRow]
    domain_weight_sums: list[DomainWeightSum]


class CorpusSource(_Strict):
    id: str
    title: str
    kind: str
    sections: list[str]
    used_for: str
    clauses: int


class PolicyCorpusData(_Strict):
    clause_count: int
    ingested: str
    sources: list[CorpusSource]


class RoutingRuleRow(_Strict):
    id: str
    rule: str


class RoutingParameter(_Strict):
    key: str
    label: str
    value: str
    editable: bool
    """Whether a draft may change it here (the boundary is the scoring model's,
    the case SLA is fixed in code)."""


class RoutingRulesData(_Strict):
    rules: list[RoutingRuleRow]
    parameters: list[RoutingParameter]


class JurisdictionCurrent(_Strict):
    version: str
    published: str
    source: str
    call_for_action: list[str]
    increased_monitoring: list[str]
    added: list[str]
    removed: list[str]


class JurisdictionFixture(_Strict):
    version: str
    note: str
    added: list[str]


class JurisdictionAnchor(_Strict):
    """One level of the 1-5 jurisdiction risk scale and what it means."""

    level: int
    label: str
    definition: str


class JurisdictionEntry(_Strict):
    """One country of the jurisdiction risk list: its rating and where it comes
    from. An external row (an FATF listing) is read-only; an internal one (the
    bank's own methodology) is edited through a draft at ``path``.
    """

    id: str
    jurisdiction: str
    rating: int
    rating_label: str
    source: str
    publisher: str
    source_type: Literal["external", "internal"]
    editable: bool
    path: Optional[str]
    note: Optional[str]


class JurisdictionData(_Strict):
    current: JurisdictionCurrent
    fixture: JurisdictionFixture
    unlisted_default: str
    anchors: list[JurisdictionAnchor]
    entries: list[JurisdictionEntry]


class AgentRow(_Strict):
    name: str
    tier: str
    model: str
    fallback: str
    tools: list[str]
    skills: list[str]
    output: str
    gate: str
    eval_set: str
    budget: str
    eval_score: float
    eval_metric: str


class AgentsData(_Strict):
    agents: list[AgentRow]
    per_case_budget: int


# ── the section as served ───────────────────────────────────────────────────


class _SectionBase(_Strict):
    title: str
    owner: str
    active_version: str
    versions: list[ConfigVersion]
    draft: ConfigDraft


class FieldSetsSection(_SectionBase):
    section: Literal["field-sets"]
    data: FieldSetData


class CategoriesSection(_SectionBase):
    section: Literal["categories"]
    data: CategoriesData


class ScoringModelSection(_SectionBase):
    section: Literal["scoring-model"]
    data: ScoringModelData


class ControlInventorySection(_SectionBase):
    section: Literal["control-inventory"]
    data: ControlInventoryData


class PolicyCorpusSection(_SectionBase):
    section: Literal["policy-corpus"]
    data: PolicyCorpusData


class RoutingRulesSection(_SectionBase):
    section: Literal["routing-rules"]
    data: RoutingRulesData


class JurisdictionRiskSection(_SectionBase):
    section: Literal["jurisdiction-risk"]
    data: JurisdictionData


class AiAgentsSection(_SectionBase):
    section: Literal["ai-agents"]
    data: AgentsData


ConfigSection = Annotated[
    Union[
        FieldSetsSection,
        CategoriesSection,
        ScoringModelSection,
        ControlInventorySection,
        PolicyCorpusSection,
        RoutingRulesSection,
        JurisdictionRiskSection,
        AiAgentsSection,
    ],
    Field(discriminator="section"),
]


class ImpactPreview(_Strict):
    """What publishing the draft would change. ``summary`` is computed from
    stored data (never canned) and says, for every figure, how many cases it
    covers and what could not be computed.
    """

    section: ConfigSectionKey
    open_cases_pinned: int
    summary: str
    non_retroactive: bool


# ── request bodies ──────────────────────────────────────────────────────────


class ConfigDraftIn(_Strict):
    """``PUT policy/config/{section}/draft``: one change to add to (or replace
    in) the working set. ``before`` is what the editor showed; the server keeps
    the value it finds in the published version, not this one.
    """

    path: str = Field(min_length=1, max_length=120)
    before: Optional[str] = Field(default=None, max_length=4000)
    after: str = Field(max_length=4000)


class ConfigPublishIn(_Strict):
    """``POST policy/config/{section}/draft/publish``: the version note
    (``reason`` is accepted as a synonym).
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    note: str = Field(
        validation_alias=AliasChoices("note", "reason"),
        min_length=NOTE_MIN_LENGTH,
        max_length=2000,
    )
