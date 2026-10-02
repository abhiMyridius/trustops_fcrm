"""S1 case-service: internal routes called only by H1 (service-account
bearer, role fcrm_service), never by the web client.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class AssignAnalystRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analyst_id: str


class AnalystLoad(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analyst_id: str
    open_case_count: int


class SetOutcomeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: str
    conditions: list[dict[str, Any]] = []
