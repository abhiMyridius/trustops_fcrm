"""Intake agent output: extracted field candidates, category proposals, and
gap questions. Sharded per document window (design B §2.2) because the cheap
6k-token budget cannot hold a whole multi-page PDF; one job per window, the
proposals are merged by field id before the PO gate opens.
"""

from __future__ import annotations

from typing import Literal, Optional

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


class FactFlag(Claim):
    """A fact intake noticed that the PO did not explicitly state and
    that may contradict or extend an extracted value — the demo's "cash
    pickup not in the business case" (document says bank deposit only;
    the owner's answer adds cash pickup). Surfaced to the analyst's
    completeness report, never silently merged into a field value.
    """

    model_config = ConfigDict(extra="forbid")

    field_id: Optional[str] = None
    text: str
    kind: Literal["new_fact", "contradiction"] = "new_fact"
