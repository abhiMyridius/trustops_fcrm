"""The stored shapes of S1's view collections (design A §2.1.1): the rows that
:mod:`fcrm_case.views_store` reads and writes, as typed models.

Like :class:`~fcrm_case.domain.record.CaseRecord` these are storage shapes, not
wire contracts: ``fcrm_case.shaping`` maps them onto ``fcrm_contracts.api``
models, so a field never reaches a response by being stored. None of them
carries a score; the score lives in ``score_snapshots`` and only the analyst
routes read it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from fcrm_contracts.api.case_internal import DocumentStatus
from fcrm_contracts.enums import ItemKind, ItemStatus
from fcrm_contracts.proposals.provenance import DocSourceRef
from pydantic import BaseModel, ConfigDict, Field

#: How a field value arrived. ``open`` is a state, not a provenance: it marks a
#: required field nobody has filled (only ever synthesised on read).
FieldOrigin = Literal["extracted", "reused", "answered", "owner_declared", "open"]

#: Origins a person set: S1 never overwrites them from an agent's proposal.
PROTECTED_ORIGINS: tuple[FieldOrigin, ...] = ("owner_declared", "reused", "answered")


class FieldSource(BaseModel):
    """Where a field value came from, in the PO's terms: a span in a document,
    another case, or an answer to a question.
    """

    model_config = ConfigDict(extra="forbid")

    doc_id: Optional[str] = None
    page: Optional[int] = None
    line_start: Optional[int] = None
    line_end: Optional[int] = None
    from_case_id: Optional[str] = None
    question_id: Optional[str] = None
    label: Optional[str] = None

    @classmethod
    def from_doc_ref(cls, ref: DocSourceRef) -> FieldSource:
        """The source of a value extracted from ``ref``."""
        return cls(
            doc_id=ref.doc_id,
            page=ref.page,
            line_start=ref.line_start,
            line_end=ref.line_end,
        )

    def as_doc_ref(self) -> Optional[DocSourceRef]:
        """The document span this source points at, or ``None`` when it is not
        a document span (a reused value, an owner's answer).
        """
        if self.doc_id is None or self.page is None or self.line_start is None:
            return None
        return DocSourceRef(
            doc_id=self.doc_id,
            page=self.page,
            line_start=self.line_start,
            line_end=self.line_end if self.line_end is not None else self.line_start,
        )


class FieldHistoryEntry(BaseModel):
    """A value a field held before it was replaced: the PO's correction keeps
    the extracted original here.
    """

    model_config = ConfigDict(extra="forbid")

    value: Optional[str] = None
    origin: FieldOrigin
    source: Optional[FieldSource] = None
    at: datetime
    by: Optional[str] = None


class FieldValueRow(BaseModel):
    """``field_values``: one field of one case (``_id = "<case_id>:<field_id>"``)."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    field_id: str
    label: str
    value: Optional[str] = None
    origin: FieldOrigin
    source: Optional[FieldSource] = None
    confidence: Optional[float] = None
    candidates: int = 1
    history: list[FieldHistoryEntry] = Field(default_factory=list)
    created_at: datetime
    updated_at: Optional[datetime] = None
    updated_by: Optional[str] = None


def field_value_id(case_id: str, field_id: str) -> str:
    """The ``_id`` of a ``field_values`` row."""
    return f"{case_id}:{field_id}"


class DocumentRow(BaseModel):
    """``documents``: one uploaded file (``_id = doc_id``)."""

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
    sha256: Optional[str] = None
    uploaded_by: Optional[str] = None
    raised_in_round: Optional[int] = None
    keep_terms: list[str] = Field(default_factory=list)
    canonical_key: Optional[str] = None
    anchored_key: Optional[str] = None
    pages: Optional[int] = None
    page_map: Optional[dict[str, tuple[int, int]]] = None
    guard1: Optional[dict[str, Any]] = None


class DecisionRow(BaseModel):
    """Who decided a review item, and what they decided."""

    model_config = ConfigDict(extra="forbid")

    value: Optional[dict[str, Any]] = None
    reason: Optional[str] = None
    by: str
    at: datetime


class ReviewItemRow(BaseModel):
    """``review_items``: one thing a gate acts on (``_id = item_id``)."""

    model_config = ConfigDict(extra="forbid")

    item_id: str
    case_id: str
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
    decided: Optional[DecisionRow] = None
    created_at: datetime


class IntakeViewRow(BaseModel):
    """``intake_views`` (``_id = case_id``): what the intake agents found that
    is not itself a field value or a gated item.
    """

    model_config = ConfigDict(extra="forbid")

    case_id: str
    run_id: str
    candidates_found: int
    required_fields: Optional[int] = None
    precedent: Optional[dict[str, Any]] = None
    flags: list[dict[str, Any]] = Field(default_factory=list)


class ScoreSnapshotRow(BaseModel):
    """``score_snapshots``: a score of record as S2 returned it and H1 pushed
    it (``_id = snapshot_id``). Analyst-only; the PO shapes have nowhere to
    carry it.
    """

    model_config = ConfigDict(extra="forbid")

    snapshot_id: str
    case_id: str
    computed_at: datetime
    purpose: str
    composite: float
    band: int
    route: str
    boundary_flag: bool
    boundary_distance: float
    domains: dict[str, dict[str, float]] = Field(default_factory=dict)


class ConditionRow(BaseModel):
    """``conditions``: a condition attached to an approval (``_id =
    condition_id``).
    """

    model_config = ConfigDict(extra="forbid")

    condition_id: str
    case_id: str
    text: str
    owner_id: str
    owner_label: str
    due_label: str
    due_date: Optional[str] = None
    status: str
    source: dict[str, Any] = Field(default_factory=dict)
