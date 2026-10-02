"""S4 audit-service: the evidence ledger (proposals) and the hash-chained,
append-only audit_records. Insert-only by construction: there is no update
or delete model here at all (design A §2.4).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


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
