"""Cross-cutting shapes used by more than one service: pagination, the
RFC 9457 problem body, a decoded principal, and a gate decision payload.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, Optional, TypeVar

from pydantic import BaseModel, ConfigDict

from fcrm_contracts.enums import GateType, Role

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """Cursor-paginated list response. ``next_cursor`` is base64 of the last
    ``_id``; omit ``total`` when counting is expensive.
    """

    model_config = ConfigDict(extra="forbid")

    items: list[T]
    next_cursor: Optional[str] = None
    total: Optional[int] = None


class Problem(BaseModel):
    """RFC 9457 problem+json body. Every error response uses this shape;
    see fcrm_common.errors for the exception -> Problem mapping.
    """

    model_config = ConfigDict(extra="forbid")

    type: str
    title: str
    status: int
    detail: Optional[str] = None
    instance: Optional[str] = None
    trace_id: Optional[str] = None
    errors: Optional[list[dict[str, str]]] = None


class Principal(BaseModel):
    """The decoded, verified caller. Never carries the whole user document —
    only what authorization needs (fixes the cloned identity's over-broad
    JWT claims; see docs/architecture/design/A-backend.md §8.1).
    """

    model_config = ConfigDict(extra="forbid")

    sub: str
    roles: list[Role]
    name: str
    typ: str  # "access" | "refresh" | "service"


class GateDecision(BaseModel):
    """What a service posts back to Caseflow when a human decides a gate.
    H1 matches on ``gate_id`` (or ``subject`` when the gate predates an id
    on the caller's side), refuses a decision from the wrong role, and
    advances the state machine.
    """

    model_config = ConfigDict(extra="forbid")

    gate_id: str
    gate_type: GateType
    case_id: str
    run_id: str
    decision: str
    reason: Optional[str] = None
    decided_by: str
    decided_at: datetime
    before: Optional[dict[str, Any]] = None
    after: Optional[dict[str, Any]] = None
