"""S5's stored rows.

These are this service's own storage detail, not part of ``fcrm_contracts``:
they carry fields (the frozen ``content``, ``open_for_voting``,
``current_round``) the public ``fcrm_contracts.api.decision`` models
deliberately do not expose. Routes only ever return the public models, built
from these rows by :mod:`fcrm_decision.views`.

Every field added after the first version has a default, so rows seeded by an
earlier build still read back.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final, Literal, Optional

from fcrm_common.ids import new_id
from fcrm_contracts.api.decision import (
    OutcomeCondition,
    OutcomeSet,
    PackStatus,
    ReleaseChecks,
)
from fcrm_contracts.enums import ChangeType, Outcome, VoteOption
from pydantic import BaseModel, ConfigDict, Field

#: A delegated close has no pack; its outcome row carries this sentinel (plus
#: the case id) where a pack id would be, so ``<pack_id>:<round>`` stays a key.
DELEGATED_PACK_PREFIX: Final = "delegated:"

#: The author of the thread S5 opens itself when a round ends with no majority.
SYSTEM_ASKER: Final = "svc:decision"
SYSTEM_ASKER_NAME: Final = "S5 decision"

Mode = Literal["committee", "delegated"]


class _Row(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PackRow(_Row):
    """A pack: the frozen snapshot, its hash, and the voting-session state
    around it. ``content`` never changes after the pack is built; everything
    else is bookkeeping.
    """

    pack_id: str
    case_id: str
    version: int
    content: dict[str, Any]
    content_hash: str = ""
    checks: ReleaseChecks
    built_at: Optional[datetime] = None
    released_at: Optional[datetime] = None
    #: Committee member user ids the pack went to (the quorum, three).
    released_to: list[str] = Field(default_factory=list)
    superseded_by: Optional[str] = None
    superseded_at: Optional[datetime] = None
    superseded_reason: Optional[str] = None
    open_for_voting: bool = False
    #: Set when an answer admitted a number changed: the pack stops taking
    #: votes at once and waits to be superseded (R6).
    unlocked_at: Optional[datetime] = None
    current_round: int = 1
    #: A round of this pack reached a decisive (not ``no_majority``) outcome.
    #: Set in the same transaction as that outcome; once true the pack never
    #: reopens and cannot be superseded.
    decided: bool = False
    #: Bumped by every vote and every state change under the pack's lock. Its
    #: only job is to make each of those transactions *write* the pack document,
    #: so two concurrent ones conflict and are serialised.
    ballot_version: int = 0
    #: How many answers have been attached as addenda (the next is this + 1).
    addendum_count: int = 0

    @property
    def superseded(self) -> bool:
        """Whether the pack was replaced or unlocked for replacement."""
        return self.superseded_at is not None or self.superseded_by is not None

    @property
    def status(self) -> PackStatus:
        """The pack's place in its life, as the web labels it."""
        if self.superseded:
            return "superseded"
        if self.decided:
            return "decided"
        if self.released_at is not None:
            return "released"
        return "built"


class VoteRow(_Row):
    """One member's vote in one round. Never updated except ``voided``."""

    vote_id: str = Field(default_factory=lambda: new_id("vote"))
    pack_id: str
    round: int
    member_id: str
    option: VoteOption
    rationale: str
    cast_at: datetime
    voided: bool = False


class RecordedOutcome(_Row):
    """What H1 (committee) or the analyst's close (delegated) recorded about an
    outcome, beyond the bare result.
    """

    decided_by: Optional[str] = None
    reason: Optional[str] = None
    plain_summary: Optional[str] = None
    conditions: list[OutcomeCondition] = Field(default_factory=list)
    recorded_at: datetime


class OutcomeRow(_Row):
    """One round's result (``<pack_id>:<round>``), or a delegated close.

    A round with no majority is kept as a row with ``no_majority`` set: the
    round happened, nothing was decided, and the next round is on the pack.
    """

    outcome: OutcomeSet
    mode: Mode = "committee"
    decided_at: Optional[datetime] = None
    recorded: Optional[RecordedOutcome] = None

    @property
    def row_id(self) -> str:
        """The ``_id`` this row is stored under."""
        return f"{self.outcome.pack_id}:{self.outcome.round}"


class AnswerRow(_Row):
    """The analyst's answer to a thread, which is also the pack's addendum."""

    by: str
    by_name: str
    text: str
    at: datetime
    addendum_id: str
    addendum_no: int
    numbers_changed: bool
    unlock_reason: Optional[str] = None


class ThreadRow(_Row):
    """A committee question thread (R6), with its answer once there is one."""

    thread_id: str
    pack_id: str
    case_id: str
    asked_by: str
    asked_by_name: str
    text: str
    created_at: datetime
    system: bool = False
    answer: Optional[AnswerRow] = None


class KeyRow(_Row):
    """An ``Idempotency-Key`` and what it produced, so a replay answers the
    same. ``fingerprint`` binds the key to one request.
    """

    key: str
    kind: str
    fingerprint: str
    result: dict[str, Any]
    at: datetime
    #: The case the request was about, so a demo reset can sweep its keys too.
    case_id: Optional[str] = None


class QueueEntry(_Row):
    """One row of the committee queue, joined and projected by the store in a
    single query: the pack's headline, who has voted in its current round and,
    for a decided pack, the outcome.
    """

    pack_id: str
    case_id: str
    version: int
    released_at: datetime
    decided: bool = False
    title: str = ""
    analyst_name: str = ""
    change_types: list[ChangeType] = Field(default_factory=list)
    band: int
    band_label: str = ""
    voters: list[str] = Field(default_factory=list)
    outcome: Optional[Outcome] = None

    @classmethod
    def from_pack(
        cls, pack: PackRow, voters: list[str], outcome: Optional[Outcome]
    ) -> QueueEntry:
        """The queue row of a released pack, read from its frozen content. The
        in-memory store builds it this way; the Mongo store's aggregation
        projects the same fields.

        Raises:
            ValueError: The pack was never released.
        """
        if pack.released_at is None:
            raise ValueError(f"pack {pack.pack_id} was never released")
        content = pack.content
        score = content.get("score") or {}
        return cls(
            pack_id=pack.pack_id,
            case_id=pack.case_id,
            version=pack.version,
            released_at=pack.released_at,
            decided=pack.decided,
            title=str(content.get("title", "")),
            analyst_name=str(content.get("analyst_name", "")),
            change_types=list(content.get("change_types", [])),
            band=score["band"],
            band_label=str(score.get("band_label", "")),
            voters=voters,
            outcome=outcome,
        )
