"""S5 decision-service: frozen pack snapshots, hidden votes, tally, outcomes,
and the R6 committee-question path. See ADR-08 for the tally rule.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict

from fcrm_contracts.enums import Outcome, VoteOption


class ReleaseChecks(BaseModel):
    """The four release checks before a pack may go to committee (design A
    §3.1 "proposed enumeration", mirrors SubmitCaseChecks in spirit).
    """

    model_config = ConfigDict(extra="forbid")

    numbers_locked: bool
    conditions_drafted: bool
    all_review_items_decided: bool
    boundary_and_band_acknowledged: bool

    @property
    def all_passed(self) -> bool:
        return (
            self.numbers_locked
            and self.conditions_drafted
            and self.all_review_items_decided
            and self.boundary_and_band_acknowledged
        )


class PackBuild(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    snapshot: dict[str, Any]  # frozen view from S1 + S4 head at build time


class Pack(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pack_id: str
    case_id: str
    version: int
    content_hash: str
    checks: ReleaseChecks
    released_at: Optional[datetime] = None
    superseded_by: Optional[str] = None


class VoteCast(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pack_id: str
    member_id: str
    round: int
    option: VoteOption
    rationale: str  # required on every vote


class VotesHidden(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pack_id: str
    round: int
    votes_cast: int
    quorum: int


class RevealedVote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    member_id: str
    option: VoteOption
    rationale: str


class VotesRevealed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pack_id: str
    round: int
    votes: list[RevealedVote]
    tally: dict[str, int]


class OutcomeSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    pack_id: str
    round: int
    outcome: Optional[Outcome] = None  # None when no_majority
    no_majority: bool = False


class CommitteeQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pack_id: str
    asked_by: str
    text: str


class CommitteeAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pack_id: str
    question_id: str
    answered_by: str
    text: str
    numbers_changed: bool
