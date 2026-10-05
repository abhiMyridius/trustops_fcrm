"""S4 audit-service: the evidence ledger (proposals) and the hash-chained,
append-only audit_records. Insert-only by construction: there is no update
or delete model here at all (design A §2.4).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from fcrm_contracts.jsonsafe import assert_json_safe


class ProposalTokens(BaseModel):
    """Token usage of the model call that produced a proposal (the keys of
    ``AgentDone.usage``); all zero for a proposal no model call produced.
    """

    model_config = ConfigDict(extra="forbid")

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cache_read_tokens: int = Field(default=0, ge=0)
    cache_write_tokens: int = Field(default=0, ge=0)


class ProposalAppend(BaseModel):
    """``POST /proposals``: one validated agent output for the evidence
    ledger. ``kind`` is open (``extraction``, ``category``, ``gaps``,
    ``screening``, ``research``, ``assessment``, ``critic``, ``narrative``,
    ``completeness``, ``examiner``, ``watchlist``, ``reflag`` ...) so a new
    agent needs no contract change; ``supersedes`` names the earlier proposal
    this one replaces (a later version is a new document: the ledger never
    updates one).
    """

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    agent: str = Field(min_length=1)
    kind: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_.]*$")
    payload: dict[str, Any]
    model: str = Field(min_length=1)
    prompt_v: str = Field(min_length=1)
    tier: str = Field(min_length=1)
    tokens: ProposalTokens = Field(default_factory=ProposalTokens)
    supersedes: Optional[str] = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _content_is_storable_json(self) -> ProposalAppend:
        """A proposal is stored as BSON and re-served as JSON: a lone UTF-16
        surrogate cannot be encoded and ``NaN``/``Infinity`` has no JSON form,
        so both are a 422 here rather than a 500 in the store.
        """
        for name in ("case_id", "run_id", "agent", "model", "prompt_v", "payload"):
            assert_json_safe(getattr(self, name), name)
        return self


class Proposal(ProposalAppend):
    """A stored proposal. ``seq`` is the case's own monotonic ledger number
    (one sequence per case, so it is also monotonic within a run);
    ``superseded_by`` is derived on read from the proposal that names this
    one in its ``supersedes``.
    """

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


class ChainHead(BaseModel):
    """``GET /head/{case_id}``: the case chain's head, which a pack records so
    it can later prove which records it was built from. A case with no records
    has ``seq=0`` and the genesis hash.
    """

    model_config = ConfigDict(extra="forbid")

    seq: int
    hash: str


class AuditRecordRow(BaseModel):
    """``GET /records?case_id=``: one record in the web's row shape
    (``AuditRecord`` in ``src/frontend/src/api/types/analyst.ts``): ``by`` is
    the actor, ``what`` the summary, ``at`` the append time.
    """

    model_config = ConfigDict(extra="forbid")

    seq: int
    type: str
    by: str
    what: str
    at: datetime
    hash: str
    prev_hash: str
