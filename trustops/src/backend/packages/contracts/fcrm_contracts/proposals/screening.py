"""Screening agent output (R4 fast-lane proposal). Design B §2.1."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from fcrm_contracts.proposals.provenance import Claim


class FastLaneReason(Claim):
    model_config = ConfigDict(extra="forbid")

    text: str


class ScreeningProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    fast_lane: bool
    reasons: list[FastLaneReason]
