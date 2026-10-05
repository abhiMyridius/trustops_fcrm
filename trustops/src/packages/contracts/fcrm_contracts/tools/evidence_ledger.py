"""The ``evidence_ledger`` tool: read prior proposals for this case from
S4's ledger (not the audit chain itself — the ledger is the pre-decision
evidence trail; see design A §2.4). The critic agent uses this to compare
a new proposal against what earlier agents already said about the same
case. Backed by S4."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


class EvidenceLedgerInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    run_id: str
    # None = every agent's proposals for this run; otherwise filter to one.
    agent: Optional[str] = None


class LedgerEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_id: str
    agent: str
    kind: str
    payload: dict[str, Any]
    seq: int


class EvidenceLedgerOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entries: list[LedgerEntry]
