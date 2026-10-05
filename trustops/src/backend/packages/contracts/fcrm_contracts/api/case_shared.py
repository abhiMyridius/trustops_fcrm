"""Sub-models the analyst and internal case shapes
(:mod:`fcrm_contracts.api.case_analyst`, :mod:`fcrm_contracts.api.case_internal`)
embed.

They lived in ``case_po.py`` until that module became an exact mirror of the
web's PO types (``src/frontend/src/api/types/po.ts``, ADR-30): the PO surface
now has its own, differently shaped ``ConditionPO``, so these were moved here
unchanged rather than renamed. Nothing the PO is served uses them; the analyst
lane replaces the shapes that embed them (the workbench) and deletes this
module with them.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict

from fcrm_contracts.enums import ClarificationStatus


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


class CompletionState(BaseModel):
    """The one completion shape the analyst and internal detail responses
    read (``fcrm_case.domain.completion.compute_completion``'s output, mapped
    at the HTTP boundary so the wire shape lives here, not in the service's own
    domain module).
    """

    model_config = ConfigDict(extra="forbid")

    required_count: int
    filled_count: int
    pct: float
    threshold_pct: float
    missing_always_required: list[str]
    all_always_required_filled: bool
