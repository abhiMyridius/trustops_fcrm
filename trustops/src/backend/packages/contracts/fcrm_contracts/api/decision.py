"""S5 decision-service contracts: the frozen pack, hidden votes, the tally,
committee threads (R6) and outcomes.

The web's shapes are the contract (ADR-30). The models that mirror a web type
carry its exact keys; ``tests/unit/packages/contracts/test_decision_web_shape.py``
parses ``src/frontend/src/api/types/committee.ts`` and ``analyst.ts`` and fails
if either side drifts. ``from`` is a Python keyword, so those two fields are
``from_`` with the alias ``from`` and always serialise by alias.

What H1 posts is :class:`PackContent` (the ``PackBuild.snapshot``): the body of
a :class:`PackView` plus the four release checks, built from S1's locked view
and S4's chain head. S5 stores it verbatim, hashes it verbatim and serves it
back as a :class:`PackView` with its own fields added (``pack_id``, ``version``,
``content_hash``, ``released_at``, ``status``, ``addenda``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    field_validator,
    model_validator,
)

from fcrm_contracts.enums import ChangeType, Outcome, VoteOption

#: A written reason or rationale must say something (the web's ``REASON_MIN``).
REASON_MIN_CHARS = 20
#: A service-supplied reason is never stricter than the analyst's own gate
#: (ADR-22: 10 characters).
SERVICE_REASON_MIN_CHARS = 10

BandNo = Literal[1, 2, 3, 4]
PackStatus = Literal["built", "released", "superseded", "decided"]
#: What a round came to: one of the four outcomes, or ``no_majority`` (ADR-31).
Verdict = Literal[
    "approve", "approve_with_conditions", "defer", "reject", "no_majority"
]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ReleaseChecks(_Strict):
    """The four checks before a pack may go to committee, under the keys the
    web uses for its handoff checklist (``ReleaseCheck.id`` in ``analyst.ts``).

    Strict booleans: only a JSON ``true`` counts, so ``"false"``, ``1`` or a
    missing key is refused at the door rather than read as an acknowledgement.
    """

    locked: StrictBool
    all_items_reviewed: StrictBool
    boundary_and_band_acknowledged: StrictBool
    business_summary_present: StrictBool

    @property
    def all_passed(self) -> bool:
        """Whether every check passed (a pack releases only then)."""
        return (
            self.locked
            and self.all_items_reviewed
            and self.boundary_and_band_acknowledged
            and self.business_summary_present
        )

    def failing(self) -> list[str]:
        """The keys of the checks that did not pass, in checklist order."""
        return [name for name, ok in self.model_dump().items() if not ok]


# --- the pack (PackView, committee.ts) ------------------------------------------


class PackArea(_Strict):
    """One scored domain: the L x I, control effectiveness and residual."""

    domain: str
    label: str
    L: float
    I: float  # noqa: E741 - the web's key (likelihood L, impact I)
    inherent: float
    ce: float
    retention: float
    residual: float
    weight: float
    contribution: float


class PackScore(_Strict):
    """The locked score as the committee sees it (computed by S2, never here)."""

    composite: float
    band: BandNo
    band_label: str
    route_label: str
    areas: list[PackArea]


class PackBoundary(_Strict):
    flag: bool
    distance: Optional[float] = None
    acknowledged_by: Optional[str] = None
    text: str


class PackRecommendation(_Strict):
    outcome: Outcome
    text: str
    edits_by_analyst: int


class PackCondition(_Strict):
    """A drafted condition (``ConditionAnalyst`` in ``analyst.ts``)."""

    id: str
    text: str
    owner: str
    due: str
    status: Literal["open", "scheduled", "evidence_submitted", "closed"]
    from_: str = Field(alias="from")


class PackOverride(_Strict):
    item: str
    from_: float = Field(alias="from")
    to: float
    by: str
    reason: str
    record_seq: Optional[int] = None


class PackRemoval(_Strict):
    item: str
    reason: str
    by: str


class PackClause(_Strict):
    id: str
    title: str
    quote: str
    ref: str
    # ADR-74: set only on a clause the analyst added from a corpus search; an
    # agent-proposed clause leaves both out of the snapshot (its hash is unchanged).
    added_by: Optional[str] = None
    added_reason: Optional[str] = None


class PackControl(_Strict):
    id: str
    name: str
    domain: str
    rating: float
    weight_norm: float
    evidence: str
    stale: bool


class PackPlanned(_Strict):
    description: str
    owner: str
    due: str


class PackCriticFinding(_Strict):
    """A critic finding and where it points (``CriticFinding`` in ``analyst.ts``)."""

    id: str
    type: str
    severity: Literal["high", "medium", "low"]
    text: str
    records: list[int]
    target: Optional[str] = None


class PackClarificationRound(_Strict):
    round: int
    questions: int
    answered: int


class PackPins(_Strict):
    """The versions the case was assessed under (``PinnedVersions`` in
    ``common.ts``: display strings, not the run's integer pins).
    """

    scoring: str
    corpus: str
    controls: str
    field_set: str
    jurisdictions: str
    routing: str
    prompts: str


class PackAuditHead(_Strict):
    seq: int
    hash: str


class PackAddendum(_Strict):
    """An answered committee question, attached to the pack (R6); the pack's
    content and hash are untouched by it.
    """

    no: int
    text: str
    by: str
    at: datetime
    numbers_changed: bool


class PackBody(_Strict):
    """The part of a pack that is content: what H1 freezes and S5 serves."""

    case_id: str
    title: str
    unit: str
    owner_name: str
    analyst_name: str
    score: PackScore
    boundary: PackBoundary
    recommendation: PackRecommendation
    business_summary: str
    conditions: list[PackCondition]
    overrides: list[PackOverride]
    removals: list[PackRemoval]
    clauses: list[PackClause]
    gaps: list[str]
    controls: list[PackControl]
    planned: list[PackPlanned]
    critic_findings: list[PackCriticFinding]
    clarification_rounds: list[PackClarificationRound]
    pins: PackPins
    audit_head: PackAuditHead


class PackView(PackBody):
    """``PackView`` in ``committee.ts``: a pack as the committee and the analyst
    read it. ``status`` is derived by S5; ``addenda`` live beside the pack, not
    in it.
    """

    pack_id: str
    version: int
    content_hash: str
    released_at: Optional[datetime] = None
    status: PackStatus
    addenda: list[PackAddendum] = Field(default_factory=list)


class PackContent(PackBody):
    """``PackBuild.snapshot``: a :class:`PackBody` plus what S5 needs to decide
    whether it may be released and what to show in the queue. Unknown keys are
    ignored here and kept in the stored snapshot (and its hash) as sent.
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    checks: ReleaseChecks
    change_types: list[ChangeType] = Field(default_factory=list)


class PackBuild(_Strict):
    """``POST /packs``. H1 (service) sends the ``snapshot``; an analyst sends
    only the ``case_id`` and is handed the latest pack.
    """

    case_id: str
    snapshot: Optional[dict[str, Any]] = None


class PackSummaryCounts(_Strict):
    composite: float
    band: BandNo
    areas: int
    controls: int
    overrides: int
    planned: int
    clauses: int
    gaps: int
    rounds: int


class PackSummary(_Strict):
    """One pack version in a case's list (``PackSummary`` in ``analyst.ts``)."""

    pack_id: str
    version: int
    content_hash: str
    status: PackStatus
    built_at: datetime
    counts: PackSummaryCounts


# --- the committee queue ----------------------------------------------------------


class CommitteeQueueItem(_Strict):
    """A released pack in the member's queue with their own vote status."""

    case_id: str
    title: str
    change_types: list[ChangeType]
    band: BandNo
    band_label: str
    analyst_name: str
    received: datetime
    pack_id: str
    pack_version: int
    your_vote: Literal["not_voted", "voted"]
    cast_count: int
    quorum: int
    status: Literal["open", "decided"]
    outcome_label: Optional[str] = None


# --- votes (VotesView, committee.ts) ----------------------------------------------


def _stripped_at_least(value: str, minimum: int, what: str) -> str:
    """``value`` stripped, refused when it is shorter than ``minimum``: a
    string of spaces must not pass for a rationale, a question or an answer.
    """
    text = value.strip()
    if len(text) < minimum:
        raise ValueError(f"{what} needs at least {minimum} characters")
    return text


class VoteIn(_Strict):
    """``POST /packs/{pack_id}/votes``. The member is the caller and the round
    is the pack's current one; neither is in the body.
    """

    vote: VoteOption
    rationale: str

    @field_validator("rationale")
    @classmethod
    def _rationale_says_something(cls, value: str) -> str:
        return _stripped_at_least(value, REASON_MIN_CHARS, "a rationale")


class VoteReceipt(_Strict):
    """What a vote returns: that it counted, never what it was."""

    vote_id: str
    round: int
    cast_n_of_3: int


class VoteMember(_Strict):
    name: str
    status: Literal["voted", "not_voted"]
    is_you: bool
    choice: Optional[Outcome] = None
    rationale: Optional[str] = None
    cast_at: Optional[datetime] = None


class MyVote(_Strict):
    choice: Outcome
    rationale: str
    cast_at: datetime


class VotesView(_Strict):
    """The votes of one round. Until all three are cast ``revealed`` is false
    and every ``choice`` but the caller's own is null; the reveal and the tally
    happen together on the third vote (R2).
    """

    pack_id: str
    round: int
    quorum: int
    cast_count: int
    revealed: bool
    members: list[VoteMember]
    mine: Optional[MyVote] = None
    tally: Optional[dict[str, int]] = None
    outcome: Optional[Verdict] = None
    outcome_label: Optional[str] = None


# --- committee threads (R6) -------------------------------------------------------


class ThreadAsk(_Strict):
    """``POST /packs/{pack_id}/threads``: a member's question."""

    text: str = Field(max_length=2000)

    @field_validator("text")
    @classmethod
    def _question_is_not_blank(cls, value: str) -> str:
        return _stripped_at_least(value, 1, "a question")


class ThreadAnswerIn(_Strict):
    """``POST /threads/{thread_id}/answer``. ``numbers_changed`` needs a reason
    of at least :data:`REASON_MIN_CHARS`; without it the answer is an addendum.
    """

    text: str = Field(max_length=4000)
    numbers_changed: bool
    unlock_reason: Optional[str] = None

    @field_validator("text")
    @classmethod
    def _answer_is_not_blank(cls, value: str) -> str:
        return _stripped_at_least(value, 1, "an answer")

    @model_validator(mode="after")
    def _a_changed_number_needs_a_reason(self) -> ThreadAnswerIn:
        reason = (self.unlock_reason or "").strip()
        if self.numbers_changed and len(reason) < REASON_MIN_CHARS:
            raise ValueError(
                "unlock_reason of at least "
                f"{REASON_MIN_CHARS} characters is required when numbers change"
            )
        self.unlock_reason = reason or None
        return self


class ThreadAnswer(_Strict):
    by: str
    text: str
    at: datetime
    addendum_no: int
    numbers_changed: bool


class CommitteeThread(_Strict):
    """A question thread, visible to every member and the analyst."""

    id: str
    from_: str = Field(alias="from")
    question: str
    at: datetime
    answer: Optional[ThreadAnswer] = None


# --- service routes: supersede, re-vote, outcomes ---------------------------------


class SupersedeRequest(_Strict):
    """``POST /packs/{pack_id}/supersede`` (H1, after the analyst unlocks)."""

    reason: str = Field(min_length=SERVICE_REASON_MIN_CHARS)


class SupersedeResult(_Strict):
    pack_id: str
    version: int
    status: Literal["superseded"] = "superseded"
    voided_votes: int
    rerequested: list[str]


class RevoteRequest(_Strict):
    """``POST /packs/{pack_id}/revote`` (H1, after ``vote.no_majority``)."""

    reason: Literal["no_majority"] = "no_majority"


class RevoteResult(_Strict):
    pack_id: str
    round: int
    member_ids: list[str]


class OutcomeCondition(_Strict):
    id: str
    text: str
    owner_id: Optional[str] = None
    due_label: Optional[str] = None


class OutcomeRecord(_Strict):
    """``POST /outcomes`` (service). ``committee`` records what a tallied pack
    decided (``pack_id`` required, the outcome must be the tallied one);
    ``delegated`` records an analyst's close under R1 (no pack, a reason).
    """

    case_id: str
    mode: Literal["committee", "delegated"]
    outcome: Outcome
    pack_id: Optional[str] = None
    decided_by: Optional[str] = None
    reason: Optional[str] = None
    plain_summary: Optional[str] = None
    conditions: list[OutcomeCondition] = Field(default_factory=list)

    @model_validator(mode="after")
    def _mode_rules(self) -> OutcomeRecord:
        if self.mode == "committee" and not self.pack_id:
            raise ValueError("a committee outcome names the pack that was tallied")
        if self.mode == "delegated":
            if self.pack_id:
                raise ValueError("a delegated outcome has no pack")
            if not self.decided_by:
                raise ValueError("a delegated outcome names who decided it")
            if len((self.reason or "").strip()) < SERVICE_REASON_MIN_CHARS:
                raise ValueError(
                    "a delegated outcome needs a reason of at least "
                    f"{SERVICE_REASON_MIN_CHARS} characters"
                )
        return self


class OutcomeSet(_Strict):
    """One round's tally result, as S5 stores it. ``outcome`` is ``None`` when
    the round ended with no majority (ADR-31); nothing is decided then.
    """

    case_id: str
    pack_id: str
    round: int
    outcome: Optional[Outcome] = None  # None when no_majority
    no_majority: bool = False


class CaseOutcome(_Strict):
    """``GET /cases/{case_id}/outcome``: what was decided and how."""

    case_id: str
    outcome: Outcome
    label: str
    mode: Literal["committee", "delegated"]
    pack_id: Optional[str] = None
    round: int
    decided_at: Optional[datetime] = None
    decided_by: Optional[str] = None
    reason: Optional[str] = None
    plain_summary: Optional[str] = None
    conditions: list[OutcomeCondition] = Field(default_factory=list)
    recorded: bool
