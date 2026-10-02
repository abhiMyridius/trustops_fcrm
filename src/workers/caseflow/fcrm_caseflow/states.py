"""Run-level flags that sit alongside ``CaseState``.

Design A §3.1: "Sub-states are not enum values; they are flags in the run
snapshot". These never appear in ``CaseState`` and are never themselves a
transition target — they are read and written by transition *actions* as
side information (e.g. "fast_lane became true"), carried in
:class:`~fcrm_caseflow.transitions.TransitionContext` and persisted on
``case_runs`` by the real runtime.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict


class RunFlags(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fast_lane: bool = False
    round_open: bool = False
    locked: bool = False
    pack_version: int = 0
    vote_round: int = 0


class RunSnapshot(BaseModel):
    """The minimal persisted shape ``eventstore.rebuild`` must reproduce
    from ``run_events`` alone (design A §3.5's replay test)."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    case_id: str
    state: str
    flags: RunFlags
    version: int = 0  # event count; incremented by every applied transition
    closed_at: Optional[str] = None
