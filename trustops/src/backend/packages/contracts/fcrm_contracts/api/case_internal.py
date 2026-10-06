"""S1 case-service: internal routes called only by H1 (service-account
bearer, role fcrm_service), never by the web client.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from fcrm_contracts.api.case_analyst import CaseDetailAnalyst
from fcrm_contracts.enums import CaseState, ChangeType, ItemKind, ItemStatus
from fcrm_contracts.proposals.provenance import DocSourceRef, SourceRef
from fcrm_contracts.versions import PinnedVersions


class CaseDetailInternal(CaseDetailAnalyst):
    """What H1 (and other services, via a service-account bearer token)
    read: everything the analyst shape has, plus the pinned versions a
    case runs against -- never sent to the web client.
    """

    model_config = ConfigDict(extra="forbid")

    fieldset_v: int
    pinned_versions: PinnedVersions


class AssignAnalystRequest(BaseModel):
    """``POST /internal/cases/{id}/assign``: H1's R5 auto-triage result."""

    model_config = ConfigDict(extra="forbid")

    analyst_id: str
    reason: Optional[str] = None
    open_counts: dict[str, int] = Field(default_factory=dict)


class AnalystLoad(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analyst_id: str
    open_case_count: int


# ── V1: what H1's intake core and the document pipeline push into S1 ────────
# Everything below is called only by the service account (``fcrm_service``),
# idempotently: a PUT or PATCH repeated with the same body leaves S1 unchanged,
# because a harness step may run again after a failure (ADR-28).

DocumentStatus = Literal["stored", "extracted", "quarantined", "failed"]
UiVisibility = Literal["po", "analyst", "all"]


class DocumentInternal(BaseModel):
    """``GET /internal/documents/{doc_id}``: one uploaded document as H1,
    H2's ``document_reader`` tool and the status polling see it.
    """

    model_config = ConfigDict(extra="forbid")

    doc_id: str
    case_id: str
    name: str
    status: DocumentStatus
    bucket: str
    key: str
    size_bytes: int
    content_type: str
    uploaded_at: datetime
    raised_in_round: Optional[int] = None
    canonical_key: Optional[str] = None
    anchored_key: Optional[str] = None
    pages: Optional[int] = None
    guard1: Optional[dict[str, Any]] = None


class DocumentPatch(BaseModel):
    """``PATCH /internal/documents/{doc_id}``: H1 records what H3 produced."""

    model_config = ConfigDict(extra="forbid")

    status: DocumentStatus
    canonical_key: Optional[str] = None
    anchored_key: Optional[str] = None
    pages: Optional[int] = None
    page_map: Optional[dict[str, tuple[int, int]]] = None
    guard1: Optional[dict[str, Any]] = None


class OwnerRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str


class FieldValueInternal(BaseModel):
    """One field of a case as agents and H1 read it (never the PO shape)."""

    model_config = ConfigDict(extra="forbid")

    field_id: str
    label: str
    value: Optional[str] = None
    origin: Literal["extracted", "reused", "answered", "owner_declared", "open"]
    source: Optional[DocSourceRef] = None
    original_value: Optional[str] = None


class CaseRecordInternal(BaseModel):
    """``GET /internal/cases/{case_id}/record``: the case as H1's steps and
    H2's ``case_record`` tool read it. Carries the pins and fields; carries no
    score (scores live in S2's response and S1's score snapshot, read through
    their own routes).
    """

    model_config = ConfigDict(extra="forbid")

    case_id: str
    title: str
    unit: Optional[str] = None
    owner: OwnerRef
    analyst: Optional[OwnerRef] = None
    state: CaseState
    run_id: Optional[str] = None
    change_types: list[ChangeType] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    pins: PinnedVersions
    fieldset_v: int
    required_field_ids: list[str] = Field(default_factory=list)
    fields: list[FieldValueInternal] = Field(default_factory=list)
    documents: list[DocumentInternal] = Field(default_factory=list)
    submission_version: Optional[int] = None


class ValueProposal(BaseModel):
    """One field value the intake agent extracted (from an ``ExtractedField``)."""

    model_config = ConfigDict(extra="forbid")

    field_id: str
    label: str
    value: str
    source: DocSourceRef
    quote: str
    confidence: float = Field(ge=0.0, le=1.0)
    candidates: int = 1


class CategoryProposalItem(BaseModel):
    """A change type the PO must confirm or remove (gate ``po_confirm_category``)."""

    model_config = ConfigDict(extra="forbid")

    change_type: ChangeType
    label: str
    source: DocSourceRef
    quote: str
    confidence: float = Field(ge=0.0, le=1.0)
    gate_id: str


class GapQuestionItem(BaseModel):
    """A question for the PO (gate ``po_answer_gaps``); the PO's answer fills
    ``field_id``.
    """

    model_config = ConfigDict(extra="forbid")

    question_id: str
    field_id: Optional[str] = None
    label: str
    text: str
    blocking: bool
    gate_id: Optional[str] = None


class ReusableField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_id: str
    label: str
    value: str
    reusable: bool
    reason: Optional[str] = None


class PrecedentMatch(BaseModel):
    """The closest closed case and which of its values may be reused (scene 5)."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    title: str
    unit: str
    closed_month: str
    similarity: float = Field(ge=0.0, le=1.0)
    fields: list[ReusableField] = Field(default_factory=list)


class FactFlagItem(BaseModel):
    """A fact the PO did not state that may contradict or extend a value; shown
    to the analyst, never silently merged (``FactFlag``).
    """

    model_config = ConfigDict(extra="forbid")

    field_id: Optional[str] = None
    text: str
    kind: Literal["new_fact", "contradiction"]


class IntakePush(BaseModel):
    """``PUT /internal/cases/{case_id}/intake``: everything the intake agents
    proposed, merged by H1, in one idempotent write. S1 materialises it: values
    become ``field_values`` (origin ``extracted``, never overwriting a value the
    PO already declared), categories and gaps become gated review items, and
    the PO's intake screen reads the result.
    """

    model_config = ConfigDict(extra="forbid")

    run_id: str
    candidates_found: int
    required_fields: Optional[int] = None
    values: list[ValueProposal] = Field(default_factory=list)
    categories: list[CategoryProposalItem] = Field(default_factory=list)
    gaps: list[GapQuestionItem] = Field(default_factory=list)
    precedent: Optional[PrecedentMatch] = None
    flags: list[FactFlagItem] = Field(default_factory=list)


class ReviewItemUpsert(BaseModel):
    """One ``review_item``. ``item_id`` is chosen by the caller and stable
    across retries (a deterministic id), so an upsert never duplicates.
    """

    model_config = ConfigDict(extra="forbid")

    item_id: str
    kind: ItemKind
    proposal_id: Optional[str] = None
    gate_id: Optional[str] = None
    subject: dict[str, Any] = Field(default_factory=dict)
    proposed: dict[str, Any] = Field(default_factory=dict)
    source_refs: list[SourceRef] = Field(default_factory=list)
    input_fields: list[str] = Field(default_factory=list)
    supersedes: Optional[str] = None
    status: ItemStatus = ItemStatus.PROPOSED


class ItemsUpsert(BaseModel):
    """``PUT /internal/cases/{case_id}/items``: bulk upsert, one round trip."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    items: list[ReviewItemUpsert]


class ItemsUpsertResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    upserted: int
    item_ids: list[str]


class StatePatch(BaseModel):
    """``PATCH /internal/cases/{case_id}/state``: H1 is the only caller; S1 never
    decides a transition itself. Only the fields that are set change.
    """

    model_config = ConfigDict(extra="forbid")

    stage: CaseState
    run_id: Optional[str] = None
    fast_lane_proposed: Optional[bool] = None
    round_open: Optional[bool] = None
    locked: Optional[bool] = None
    pack_version: Optional[int] = None
    vote_round: Optional[int] = None


class UiActor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    role: str
    component: str


class UiEventIn(BaseModel):
    """``POST /internal/cases/{case_id}/events``: H1 asks S1 to show something
    on the case stream and in the activity feed. ``visibility`` decides who sees
    it: ``po`` reaches the PO and everyone else, ``analyst`` never reaches the PO.
    """

    model_config = ConfigDict(extra="forbid")

    type: str
    visibility: UiVisibility
    summary: str
    detail: dict[str, Any] = Field(default_factory=dict)
    actor: UiActor
    record_seq: Optional[int] = None


class AnalystLoadResponse(BaseModel):
    """``GET /internal/analysts/load``: open cases per analyst, for R5."""

    model_config = ConfigDict(extra="forbid")

    loads: list[AnalystLoad]


# ── V1 responses (added by lane I2-A1: the request models above were fixed first,
# these are what S1 answers with; additive, nothing above changed) ────────────


class ItemDecisionInternal(BaseModel):
    """Who decided a review item, and what they decided."""

    model_config = ConfigDict(extra="forbid")

    value: Optional[dict[str, Any]] = None
    reason: Optional[str] = None
    by: str
    at: datetime


class ReviewItemInternal(BaseModel):
    """``GET /internal/cases/{case_id}/items``: one stored review item. Carries
    the proposal's provenance and the decision, if any; never the PO shape.
    """

    model_config = ConfigDict(extra="forbid")

    item_id: str
    kind: ItemKind
    status: ItemStatus
    proposal_id: Optional[str] = None
    gate_id: Optional[str] = None
    subject: dict[str, Any] = Field(default_factory=dict)
    proposed: dict[str, Any] = Field(default_factory=dict)
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    input_fields: list[str] = Field(default_factory=list)
    supersedes: Optional[str] = None
    run_id: Optional[str] = None
    decided: Optional[ItemDecisionInternal] = None


class IntakePushResult(BaseModel):
    """``PUT /internal/cases/{case_id}/intake`` answers with what it did.
    ``values_kept`` counts values left alone because a person already set that
    field.
    """

    model_config = ConfigDict(extra="forbid")

    case_id: str
    run_id: str
    values_written: int
    values_kept: int
    categories: int
    gaps: int


class StateResult(BaseModel):
    """``PATCH /internal/cases/{case_id}/state``: the stage now held, and
    whether this call changed it (a replay of the same patch does not).
    """

    model_config = ConfigDict(extra="forbid")

    case_id: str
    stage: CaseState
    run_id: Optional[str] = None
    stage_changed: bool


class AssignResult(BaseModel):
    """``POST /internal/cases/{case_id}/assign``: who is assigned, and whether
    this call changed it.
    """

    model_config = ConfigDict(extra="forbid")

    case_id: str
    analyst_id: str
    analyst_name: str
    changed: bool


class UiEventResult(BaseModel):
    """``POST /internal/cases/{case_id}/events``: the sequence number the event
    was stored at (the ``id`` of its stream frame).
    """

    model_config = ConfigDict(extra="forbid")

    case_id: str
    seq: int
