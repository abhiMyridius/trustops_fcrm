"""The event envelope every message on RabbitMQ carries (design A §4.2).

Every publish goes through fcrm_common.events.publish, which fills
``event_id``, ``occurred_at`` and propagates ``trace_id``/``span_id`` from
the current OTel context — callers only build the ``type`` and ``payload``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


class Envelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str  # "evt_" + ULID
    type: str  # e.g. "case.submitted", "agent.run", "agent.done"
    version: int = 1
    case_id: Optional[str] = None
    run_id: Optional[str] = None
    correlation_id: Optional[str] = None  # run-scoped; shared across a run's events
    trace_id: Optional[str] = None
    span_id: Optional[str] = None
    occurred_at: datetime
    payload: dict[str, Any]
