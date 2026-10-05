"""The ``case_record`` tool: read a case's own confirmed fields, change
types and submission version — an agent's view of "what the case already
says", never what it has not yet been told. Backed by S1."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


class CaseRecordInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    submission_version: Optional[int] = None  # None = latest


class CaseRecordOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    change_types: list[str]
    fields: dict[str, Any]
    submission_version: int
