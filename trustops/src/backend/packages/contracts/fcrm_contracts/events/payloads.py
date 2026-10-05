"""Typed payloads for the highest-traffic event types (design A §4.3).

Not every type in ``catalogue.EVENT_TYPES`` has a dedicated model yet; an
untyped payload is still valid (``Envelope.payload`` is ``dict``), but these
are the ones other packages construct directly, so they get real types and
a lock test (``tests/unit/packages/contracts/test_payload_coverage.py``)
checks every ``AgentName`` and ``GateType`` combination is reachable.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict

from fcrm_contracts.enums import AgentName, GateType, Tier
from fcrm_contracts.versions import PinnedVersions


class JobPayload(BaseModel):
    """H1 -> H2, over ``agent.run``. Deliberately small: a reference, not a
    copy, of the case's data (design B §3.1 "ContextPack is H2-built,
    snapshotted"). H2 builds and snapshots the full ContextPack itself.
    """

    model_config = ConfigDict(extra="forbid")

    job_id: str
    case_id: str
    run_id: str
    agent: AgentName
    tier: Tier
    step_key: str
    attempt: int
    scope: Optional[str] = None  # "changed_fields" | "full" | None
    changed_fields: list[str] = []
    pins: PinnedVersions


class AgentDone(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    case_id: str
    run_id: str
    agent: AgentName
    output: dict[str, Any]
    # Keys: input_tokens, output_tokens, cache_read_tokens, cache_write_tokens.
    usage: dict[str, int]
    model: str
    attempt: int


class AgentFailed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    case_id: str
    run_id: str
    agent: AgentName
    attempt: int
    reason: str
    raw_inputs_ref: Optional[str] = None


class GateOpened(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gate_id: str
    gate_type: GateType
    case_id: str
    run_id: str
    subject: dict[str, str]
    required_role: str


class CaseEvent(BaseModel):
    """Generic S1 -> H1 case-lifecycle event body (case.created,
    case.submitted, case.answered, etc.).
    """

    model_config = ConfigDict(extra="forbid")

    case_id: str
    actor: str
    data: dict[str, Any] = {}
