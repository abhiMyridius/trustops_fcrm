"""The ``audit_log`` tool: the examiner agent's only source. It reads the
hash-chained ``audit_records`` for a case — never the ledger, never a
live case document — so every sentence it writes can cite a real,
immutable ``seq``. Backed by S4."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict

from fcrm_contracts.api.audit import AuditRecord


class AuditLogInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    seq_from: int = 1
    seq_to: Optional[int] = None


class AuditLogOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    records: list[AuditRecord]
