"""S1 case-service: the PO-visible shape of a case.

This is the enforcement point for "the PO never sees a score, band, control
rating or harness data" (rule 3 / BB-02): CaseDetailPO simply has no field
that could carry one, rather than relying on a view that strips them. See
docs/governance/visibility-matrix.md.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict

from fcrm_contracts.enums import CaseState, ChangeType, ClarificationStatus


class FieldValuePO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_id: str
    label: str
    value: Optional[str] = None
    source: Optional[str] = None  # human-readable provenance, e.g. "case p.1 l.2"
    how: Optional[str] = None  # "extracted" | "reused" | "answered" | "owner_corrected"


class DocumentRefPO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doc_id: str
    name: str
    pages: int
    stored_at: datetime


class ClarificationQuestionPO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str
    text: str
    answer: Optional[str] = None
    answered_at: Optional[datetime] = None


class ClarificationThreadPO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str
    status: ClarificationStatus
    round: int
    questions: list[ClarificationQuestionPO]


class ConditionPO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    condition_id: str
    text: str
    owner: str
    due: str
    status: str


class CaseListItemPO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    title: str
    state: CaseState
    change_types: list[ChangeType]
    created_at: datetime
    submitted_at: Optional[datetime] = None
    open_clarification_count: int = 0


class CaseDetailPO(BaseModel):
    """No field here may ever carry a score, band, rating or harness
    identifier — that is the contract, not a runtime check.
    """

    model_config = ConfigDict(extra="forbid")

    case_id: str
    title: str
    state: CaseState
    unit: str
    owner: str
    change_types: list[ChangeType]
    completion_pct: float
    threshold_pct: float
    required_field_count: int
    required_fields_filled: int
    fields: list[FieldValuePO]
    documents: list[DocumentRefPO]
    clarifications: list[ClarificationThreadPO]
    conditions: list[ConditionPO]
    # Plain-language only; never the outcome enum or the band.
    outcome_summary: Optional[str] = None
    created_at: datetime
    submitted_at: Optional[datetime] = None
    decided_at: Optional[datetime] = None


class CreateDraftCaseResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str


class UpdateCaseTitleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str


class ConfirmCategoriesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    change_types: list[ChangeType]


class RemoveCategoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    change_type: ChangeType
    reason: str  # required: removal always needs a reason


class SubmitCaseChecks(BaseModel):
    """The four submit checks (design A §2.1 "proposed enumeration")."""

    model_config = ConfigDict(extra="forbid")

    completion_at_least_threshold: bool
    all_always_required_filled: bool
    no_open_blocking_gaps: bool
    owner_reviewed_corrections: bool

    @property
    def all_passed(self) -> bool:
        return (
            self.completion_at_least_threshold
            and self.all_always_required_filled
            and self.no_open_blocking_gaps
            and self.owner_reviewed_corrections
        )


class SubmitCaseResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    checks: SubmitCaseChecks
    submission_version: int
