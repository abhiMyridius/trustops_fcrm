"""Narrative agent output. Two modes (design B §2.6): completeness (runs
before Policy/Controls review, D-16/D-17) and recommendation (runs after
all review items are decided). The PO-facing BusinessSummary is checked
against the restricted-terms list by Guard 4 before it is ever stored.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict

from fcrm_contracts.proposals.provenance import Claim


class CompletenessReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    missing: list[str]
    stale: list[str]
    contradictory: list[str]
    questions_drafted: int
    questions_kept: int


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str
    anchored_to: Optional[str] = None  # field_id or control_id
    text: str
    dropped_reason: Optional[str] = None


class Condition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    condition_id: str
    text: str
    owner: str
    due: str
    status: Literal["open", "scheduled"]
    source: str  # "overridden control CTL-T01" | "planned control" | "policy gap (...)"


class Narrative(Claim):
    model_config = ConfigDict(extra="forbid")

    text: str


class BusinessSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    restricted_term_hits: int = 0  # Guard 4 writes this; must be 0 to pass
