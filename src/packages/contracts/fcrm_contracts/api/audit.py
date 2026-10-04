"""S4 audit-service: the evidence ledger (proposals) and the hash-chained,
append-only audit_records. Insert-only by construction: there is no update
or delete model here at all (design A §2.4).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, model_validator

from fcrm_contracts.jsonsafe import assert_json_safe


class ProposalAppend(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    run_id: str
    agent: str
    kind: str
    payload: dict[str, Any]
    model: str
    prompt_v: str
    tier: str


class Proposal(ProposalAppend):
    model_config = ConfigDict(extra="forbid")

    proposal_id: str
    seq: int
    created_at: datetime
    superseded_by: Optional[str] = None


class RecordAppend(BaseModel):
    """What the caller submits; ``prev_hash``/``hash``/``seq`` are computed
    by the service, never supplied by the caller.
    """

    model_config = ConfigDict(extra="forbid")

    case_id: str
    type: str
    actor: str  # "system" | "ai" | "<user id>"
    summary: str
    data: dict[str, Any] = {}

    @model_validator(mode="after")
    def _content_is_hashable_json(self) -> RecordAppend:
        """A record is hash-chained and re-served verbatim, so every string
        must be UTF-8 encodable (no lone UTF-16 surrogate) and every float
        finite (no ``NaN``/``Infinity``, which JSON cannot carry). Checked
        here so the failure is a 422, not a 500 inside the hash step.
        """
        for name in ("case_id", "type", "actor", "summary", "data"):
            assert_json_safe(getattr(self, name), name)
        return self


class AuditRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    seq: int
    type: str
    actor: str
    summary: str
    data: dict[str, Any]
    prev_hash: str
    hash: str
    created_at: datetime


class VerifyResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    record_count: int
    ok: bool
    first_break_seq: Optional[int] = None
