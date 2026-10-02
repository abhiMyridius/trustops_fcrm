"""Intake agent output: extracted field candidates, category proposals, and
gap questions. Sharded per document window (design B §2.2) because the cheap
6k-token budget cannot hold a whole multi-page PDF; one job per window, the
proposals are merged by field id before the PO gate opens.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict

from fcrm_contracts.enums import ChangeType
from fcrm_contracts.proposals.provenance import Claim


class ExtractedField(Claim):
    model_config = ConfigDict(extra="forbid")

    field_id: str
    label: str
    value: str


class ExtractionProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    window_id: str
    fields: list[ExtractedField]


class CategoryProposal(Claim):
    model_config = ConfigDict(extra="forbid")

    change_type: ChangeType


class GapQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str
    field_id: Optional[str] = None
    text: str
    blocking: bool
