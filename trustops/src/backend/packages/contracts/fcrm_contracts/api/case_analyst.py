"""S1 case-service: the analyst-visible shape. Carries scores, bands,
ratings and provenance that the PO shape (case_po.py) never does.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict

from fcrm_contracts.api.case_shared import (
    ClarificationThreadPO,
    CompletionState,
    ConditionPO,
    DocumentRefPO,
    FieldValuePO,
)
from fcrm_contracts.api.scoring import ScoreResponse
from fcrm_contracts.enums import CaseState, ChangeType, ItemKind, ItemStatus


class ReviewItem(BaseModel):
    """One row behind Gate A (review_item). The analyst confirms, overrides
    or removes each item individually; there is no bulk-accept endpoint
    (rule 2).
    """

    model_config = ConfigDict(extra="forbid")

    item_id: str
    kind: ItemKind
    status: ItemStatus
    proposed_value: dict[str, Any]
    confirmed_value: Optional[dict[str, Any]] = None
    override_reason: Optional[str] = None
    source_refs: list[dict[str, Any]] = []
    decided_by: Optional[str] = None
    decided_at: Optional[datetime] = None


class OverrideReviewItemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmed_value: dict[str, Any]
    reason: str  # required whenever confirmed_value != proposed_value


class SubmissionVersionDiff(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_id: str
    before: Optional[str]
    after: Optional[str]


class SubmissionVersion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int
    created_at: datetime
    created_by: str
    diff: list[SubmissionVersionDiff]


class CaseWorkbenchSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    state: str
    score: Optional[ScoreResponse] = None
    review_items: list[ReviewItem]
    submission_versions: list[SubmissionVersion]


class RerunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: str  # "changed_fields" | "full"
    reason: Optional[str] = None  # required when scope == "full"


class UnlockRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str


class AcknowledgeRequest(BaseModel):
    """R3: boundary flag or band-change acknowledgement."""

    model_config = ConfigDict(extra="forbid")

    ack_type: str  # "boundary" | "band_change"


class CaseListItemAnalyst(BaseModel):
    """The analyst/committee queue row: carries the assignment, which
    ``CaseListItemPO`` (case_po.py) never does.
    """

    model_config = ConfigDict(extra="forbid")

    case_id: str
    title: str
    state: CaseState
    change_types: list[ChangeType]
    analyst_id: Optional[str] = None
    created_at: datetime
    submitted_at: Optional[datetime] = None
    open_clarification_count: int = 0


class CaseDetailAnalyst(BaseModel):
    """The analyst-visible case detail: every field ``CaseDetailPO`` has,
    plus the assignment, score and harness-facing data the PO shape must
    never carry (rule 3). Built by ``fcrm_case.shaping``, never by widening
    ``CaseDetailPO`` itself.
    """

    model_config = ConfigDict(extra="forbid")

    case_id: str
    title: str
    state: CaseState
    unit: str
    owner: str
    analyst_id: Optional[str] = None
    change_types: list[ChangeType]
    completion: CompletionState
    fields: list[FieldValuePO]
    documents: list[DocumentRefPO]
    clarifications: list[ClarificationThreadPO]
    conditions: list[ConditionPO]
    score: Optional[ScoreResponse] = None
    review_items: list[ReviewItem] = []
    submission_versions: list[SubmissionVersion] = []
    outcome_summary: Optional[str] = None
    created_at: datetime
    submitted_at: Optional[datetime] = None
    decided_at: Optional[datetime] = None
