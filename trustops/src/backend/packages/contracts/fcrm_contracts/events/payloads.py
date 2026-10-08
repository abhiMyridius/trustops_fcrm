"""Typed payloads for the event types other packages construct or read
directly (design A §4.3).

Two generations live here. The first four (``JobPayload``, ``AgentDone``,
``AgentFailed``, ``GateOpened``) and ``CaseEvent`` are strict
(``extra="forbid"``). Every model added for the live wiring derives from
:class:`EventPayload` and tolerates unknown fields (``extra="ignore"``,
design A §4.4: a consumer survives an additive change); only the
:class:`~fcrm_contracts.events.envelope.Envelope` stays strict.

**Guard keys sit at the top level.** Caseflow's
``resolve.context_from_payload`` reads its guard facts (``four_submit_
checks_passed``, ``release_checks_passed``, ``vote_outcome``, ``band``,
``rerun_reason``, ``unlock_reason``, ``committee_numbers_changed``,
``fast_lane_proposed``, ``rating_or_control_changed``, ``band_changed``,
``boundary_flagged``) from top-level payload keys and silently defaults a
missing one. A payload that must drive a guard therefore declares that key
as a top-level field, and ``tests/unit/workers/caseflow/
test_payload_context_keys.py`` fails if one is not declared anywhere.

An event type with no model here is still valid (``Envelope.payload`` is a
dict).
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from fcrm_contracts.common import GateDecision
from fcrm_contracts.enums import (
    AgentName,
    ChangeType,
    Domain,
    GateType,
    ItemKind,
    Outcome,
    Role,
    StepKind,
    Tier,
    VoteOption,
)
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
    # The H1-supplied references H2's context-pack builder reads (design B
    # §3.1): in-scope domains, the submission version, the narrative mode,
    # the read-only engine summary, the examiner question, a document id...
    # The keys are ``fcrm_agents.context_pack.BuildRequest`` fields; H2
    # hands them to ``BuildRequest.from_job`` unchanged. References only,
    # never a copy of case data.
    refs: dict[str, Any] = Field(default_factory=dict)


class AgentDone(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    case_id: str
    run_id: str
    agent: AgentName
    # Keys: ``accepted`` (the proposals Guard 4/5 kept), ``dropped``,
    # ``notes_for_analyst``, ``guard_summary`` {claims_total,
    # claims_with_source, dropped} and ``attempts`` (how many model calls).
    output: dict[str, Any]
    # Keys: input_tokens, output_tokens, cache_read_tokens, cache_write_tokens.
    usage: dict[str, int]
    model: str
    attempt: int
    # A copy of the screening agent's own R4 proposal (``ScreeningProposal.
    # fast_lane``) so H1's AGENT_DONE_SCREENING guard can read it without
    # opening ``output``. Not a score, band or rating (agents never write
    # those); ``None`` for every other agent.
    fast_lane_proposed: Optional[bool] = None


class AgentFailed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    case_id: str
    run_id: str
    agent: AgentName
    attempt: int
    reason: str
    raw_inputs_ref: Optional[str] = None
    # What the job spent before it failed: input_tokens, output_tokens, and the
    # cache keys, as in ``AgentDone.usage`` (every attempt included). Empty when
    # no model call was made (a context pack that could not be built, a budget
    # refusal). H1 settles the case token ledger from it, so a job that fails
    # after two attempts is charged for both.
    usage: dict[str, int] = Field(default_factory=dict)


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


class EventPayload(BaseModel):
    """Base of the live-wiring payloads: unknown fields are ignored (design A
    §4.4), so a producer adding an optional field never breaks a consumer.
    """

    model_config = ConfigDict(extra="ignore")


# --- S1 -> H1: case lifecycle -----------------------------------------------


class CaseCreatedData(EventPayload):
    """``case.created``'s ``data``: what H1 needs to open a run. ``pins`` are
    the versions active when the case was created; H1 falls back to its own
    placeholder pins when a producer omits them (``store.DEFAULT_PINS``).
    ``change_types`` are the types the owner chose herself (the manual path,
    ADR-72): H1 records them and the field set they resolve when it opens the
    run. Empty on the AI-assisted path, where the assistant proposes them.
    """

    title: Optional[str] = None
    unit: Optional[str] = None
    change_types: list[ChangeType] = Field(default_factory=list)
    pins: Optional[PinnedVersions] = None
    intake_mode: Optional[Literal["ai_assisted", "manual"]] = None


class CaseCreated(EventPayload):
    case_id: str
    actor: str
    data: CaseCreatedData = Field(default_factory=CaseCreatedData)


class CaseDocumentStored(EventPayload):
    case_id: str
    actor: str
    doc_id: str
    name: str
    bucket: str
    key: str
    sha256: str
    size_bytes: int
    content_type: str
    raised_in_round: Optional[int] = None  # set when a clarification round asked for it
    keep_terms: list[str] = Field(default_factory=list)  # survive Guard 1 PII masking


class SubmitCheck(EventPayload):
    """One of the four submit checks (design A §2.1.3) and its verdict."""

    id: str
    ok: bool


class CaseSubmitted(EventPayload):
    case_id: str
    actor: str
    submission_version: int
    change_types: list[ChangeType] = Field(default_factory=list)
    domains: list[Domain] = Field(default_factory=list)
    filled: int
    required: int
    checks: list[SubmitCheck] = Field(default_factory=list)
    # The guard on CASE_SUBMITTED. Deliberately required: H1's context
    # defaults a missing key to True, so a producer that forgot it would
    # silently submit a case whose checks failed.
    four_submit_checks_passed: bool


class CaseAnswered(EventPayload):
    case_id: str
    actor: str
    round: int
    submission_version: int
    changed_field_ids: list[str] = Field(default_factory=list)
    added_document_ids: list[str] = Field(default_factory=list)
    thread_ids: list[str] = Field(default_factory=list)


class CaseClarificationSent(EventPayload):
    case_id: str
    actor: str
    round: int
    thread_ids: list[str] = Field(default_factory=list)
    question_count: int = 0


class CaseQuestionAsked(EventPayload):
    """``case.question_asked`` (ADR-73): the analyst asked the owner questions
    outside the clarification round, one thread per question. It decides no gate
    and pauses no clock; Caseflow only records it.
    """

    case_id: str
    actor: str
    thread_ids: list[str] = Field(default_factory=list)
    question_count: int = 0


class CaseQuestionAnswered(EventPayload):
    """``case.question_answered`` (ADR-73): the owner answered one ad-hoc
    question. ``changed_field_ids`` are the fields whose value the answer changed
    (``submission_version`` is the version it froze, ``None`` when it changed
    none); Caseflow re-runs research and assessment on them only when the run is
    in review and no agent is running, and otherwise leaves the analyst a flag.
    """

    case_id: str
    actor: str
    thread_id: str
    question_id: str
    changed_field_ids: list[str] = Field(default_factory=list)
    added_document_ids: list[str] = Field(default_factory=list)
    submission_version: Optional[int] = None


class CaseRetagged(EventPayload):
    case_id: str
    actor: str
    change_type: ChangeType
    reason: str


class CaseTypesChanged(EventPayload):
    """S1 -> H1 (ADR-72): the owner added or removed a change type outside a
    ``po_confirm_category`` gate (a type the assistant never proposed, or a
    change to a proposal whose gate was decided before). ``change_types`` is
    the case's list after the change; a removal carries its reason.
    """

    case_id: str
    actor: str
    change_type: ChangeType
    action: Literal["added", "removed"]
    reason: Optional[str] = None
    change_types: list[ChangeType] = Field(default_factory=list)


class CaseRerunRequested(EventPayload):
    case_id: str
    actor: str
    rerun_reason: str  # the guard: a re-run without a reason is refused
    scope: Optional[str] = None  # "changed_fields" | "full"


class CaseGapRecorded(EventPayload):
    case_id: str
    actor: str
    gap_id: str
    text: str
    scope: Optional[str] = None


class CaseControlPlanned(EventPayload):
    """A planned control: recorded for the analyst, never scored."""

    case_id: str
    actor: str
    planned_control: dict[str, Any] = Field(default_factory=dict)


class CaseClauseAdded(EventPayload):
    """The analyst added a clause from a corpus search (ADR-74). It is stored in
    S1 as an accepted ``clause`` item with no gate, its text read from S3 at the
    case's pinned corpus version; clauses never enter the score.
    """

    case_id: str
    actor: str
    item_id: str
    clause_id: str
    corpus_v: int
    reason: str
    title: Optional[str] = None
    ref: Optional[str] = None


class CaseClauseRemoved(EventPayload):
    """The analyst removed a clause they had added (ADR-74), with a reason."""

    case_id: str
    actor: str
    item_id: str
    clause_id: str
    reason: str


class CaseLocked(EventPayload):
    case_id: str
    actor: str


class CaseUnlocked(EventPayload):
    case_id: str
    actor: str
    unlock_reason: str  # the guard: an unlock without a reason is refused


class CaseCloseRequested(EventPayload):
    case_id: str
    actor: str
    outcome: Outcome
    reason: Optional[str] = None
    plain_summary: Optional[str] = None
    # R1 routes Band 1-2 to a delegated close. Optional: H1 falls back to the
    # band it recorded from the last score.
    band: Optional[int] = None


class GateDecided(GateDecision):
    """``gate.decided`` as S1 and S5 publish it: :class:`GateDecision` plus
    the context H1's guards and the audit trail need. Unknown fields are
    ignored (the base class is strict, this one is not).
    """

    model_config = ConfigDict(extra="ignore")

    role: Optional[Role] = None
    subject: dict[str, str] = Field(default_factory=dict)
    item_kind: Optional[ItemKind] = None
    value: Any = None
    rating_or_control_changed: bool = False
    # Read by the REVIEW_ITEM transition's context to decide whether an ack
    # gate opens. The intended source is the rescoring step's result; these
    # stay declared because H1 still reads them off this payload.
    band_changed: bool = False
    boundary_flagged: bool = False


# --- H1 -> H3 / H4: ingestion and indexing ------------------------------------


class ObjectRef(EventPayload):
    bucket: str
    key: str


class ObjectPrefix(EventPayload):
    bucket: str
    key_prefix: str


class IngestRun(EventPayload):
    """H1 (a case document) or S3 (a corpus source) -> H3, over ``ingest.run``."""

    job_id: str
    source: ObjectRef
    target: ObjectPrefix
    purpose: Literal["case_document", "corpus_source"]
    doc_id: Optional[str] = None
    source_id: Optional[str] = None
    guard1: bool = True
    keep_terms: list[str] = Field(default_factory=list)


class IngestDone(EventPayload):
    job_id: str
    doc_id: Optional[str] = None
    canonical_key: str  # the masked canonical Markdown
    anchored_key: str  # the page:line anchored copy agents cite
    pages: int
    # page number (as a string, JSON keys) -> (first line, last line), 1-based
    page_map: dict[str, tuple[int, int]] = Field(default_factory=dict)
    guard1: dict[str, Any] = Field(default_factory=dict)  # counts and pattern ids only
    warnings: list[str] = Field(default_factory=list)


class IngestFailed(EventPayload):
    job_id: str
    doc_id: Optional[str] = None
    code: str
    detail: str = ""
    quarantined: bool = False  # Guard 1 blocked the text; no agent may read it
    guard1: dict[str, Any] = Field(default_factory=dict)


class IndexBuild(EventPayload):
    job_id: str
    index: Literal["policy_clauses", "precedent_cases", "controls"]
    version: int
    source_keys: list[str] = Field(default_factory=list)
    records_key: Optional[str] = None
    mode: Literal["full", "incremental"] = "full"


class IndexBuilt(EventPayload):
    job_id: str
    index: Literal["policy_clauses", "precedent_cases", "controls"]
    version: int
    vector_count: int
    fixture_key: Optional[str] = None


class IndexFailed(EventPayload):
    job_id: str
    index: Literal["policy_clauses", "precedent_cases", "controls"]
    code: str
    detail: str = ""


# --- H1 -> H1: the step mechanism (ADR-28) ------------------------------------


class HarnessStep(EventPayload):
    """``harness.step``: one unit of H1's HTTP-side work.

    Enqueued through the outbox in the same transaction as the transition
    that needs it, run by ``caseflow.steps`` outside any transaction, and
    closed by ``CaseflowEngine.complete_step``. ``step_id`` is
    deterministic (``deterministic_id("step", run_id, step, <triggering
    message id>)``), so a redelivered trigger re-derives the same id and a
    step that already finished is a no-op.
    """

    step_id: str
    step: StepKind
    case_id: str
    run_id: str
    # What the handler needs from the trigger: ``trigger`` (the Caseflow
    # trigger's wire value), ``message_id`` and ``payload`` (the event's own
    # payload; ``agent.done`` carries the proposals H1 has no other copy of).
    args: dict[str, Any] = Field(default_factory=dict)


# --- S5 -> H1 / S1: pack, votes, committee, outcome ---------------------------

#: Keys a ``vote.cast`` payload must never carry: the option stays hidden
#: until every vote is in (ADR-08, R2), and the rationale goes with it.
_VOTE_SECRET_KEYS = frozenset({"vote", "option", "choice", "vote_value", "rationale"})


class PackReleased(EventPayload):
    """``pack.released``: a pack went to the committee. ``released_to`` holds
    member user ids (like ``vote.rerequested.member_ids``); ``released_to_names``
    is the same people by display name, for S1's handoff checklist.
    """

    pack_id: str
    version: int
    released_to: list[str] = Field(default_factory=list)
    released_to_names: list[str] = Field(default_factory=list)
    content_hash: str
    release_checks_passed: bool  # the guard on PACK_RELEASED; required, like above


class PackSuperseded(EventPayload):
    """``pack.superseded`` (S5): the analyst unlocked a pack, so it will never
    take another vote; the corrected numbers arrive as the next version. Its
    cast votes were voided (kept, never deleted) and are re-requested by the
    ``vote.rerequested`` that follows.
    """

    pack_id: str
    version: int
    reason: str
    voided_votes: int = 0


class VoteCastPayload(EventPayload):
    """``vote.cast``: that a member voted, never what they voted."""

    pack_id: str
    round: int
    member_id: str
    cast_count: int
    quorum: int

    @model_validator(mode="before")
    @classmethod
    def _never_the_option(cls, data: Any) -> Any:
        if isinstance(data, dict) and _VOTE_SECRET_KEYS & data.keys():
            leaked = sorted(_VOTE_SECRET_KEYS & data.keys())
            raise ValueError(
                f"vote.cast must never carry the chosen option; found {leaked}"
            )
        return data


class RevealedVote(EventPayload):
    member_id: str
    vote: VoteOption
    rationale: str


class VoteTallied(EventPayload):
    """``vote.tallied``: the round's outcome and every vote, revealed
    together. ``outcome`` (design A's name) and ``vote_outcome`` (the key
    H1's guard context reads) carry the same value; give either one.
    """

    pack_id: str
    round: int
    outcome: Optional[Outcome] = None
    vote_outcome: Optional[Outcome] = None
    counts: dict[str, int] = Field(default_factory=dict)
    votes: list[RevealedVote] = Field(default_factory=list)

    @model_validator(mode="after")
    def _agree_on_the_outcome(self) -> VoteTallied:
        if self.outcome is None and self.vote_outcome is None:
            raise ValueError(
                "vote.tallied needs an outcome (ADR-08: a split is a "
                "vote.no_majority, never a tally)"
            )
        if self.outcome is None:
            self.outcome = self.vote_outcome
        elif self.vote_outcome is None:
            self.vote_outcome = self.outcome
        elif self.outcome != self.vote_outcome:
            raise ValueError("outcome and vote_outcome disagree")
        return self


class VoteNoMajority(EventPayload):
    pack_id: str
    round: int
    counts: dict[str, int] = Field(default_factory=dict)


class VoteRerequested(EventPayload):
    pack_id: str
    version: int
    round: int
    member_ids: list[str] = Field(default_factory=list)
    reason: str


class CommitteeQuestionAsked(EventPayload):
    pack_id: str
    thread_id: str
    member_id: str
    text: str


class CommitteeAnswered(EventPayload):
    """``committee.answered``. ``numbers_changed`` (R6) sends the case back
    to review, which needs ``unlock_reason``; ``committee_numbers_changed``
    mirrors it for H1's guard context and is always overwritten from it.
    """

    pack_id: str
    thread_id: str
    addendum_id: Optional[str] = None
    by: str
    numbers_changed: bool
    committee_numbers_changed: bool = False
    unlock_reason: Optional[str] = None

    @model_validator(mode="after")
    def _mirror_and_require_a_reason(self) -> CommitteeAnswered:
        if self.numbers_changed and not self.unlock_reason:
            raise ValueError("an answer that changes the numbers needs unlock_reason")
        self.committee_numbers_changed = self.numbers_changed
        return self


class OutcomeCondition(EventPayload):
    id: str
    text: str
    owner_id: Optional[str] = None
    due_label: Optional[str] = None
    due_date: Optional[date] = None


class OutcomeSetPayload(EventPayload):
    """``outcome.set``. H1 reacts only to ``mode == "delegated"`` (a
    committee outcome has already moved the run on ``vote.tallied``).
    """

    case_id: str
    mode: Literal["committee", "delegated"]
    outcome: Outcome
    conditions: list[OutcomeCondition] = Field(default_factory=list)
    plain_summary: Optional[str] = None
    decided_by: Optional[str] = None
    pack_id: Optional[str] = None
    reason: Optional[str] = None


# --- S3 / S4 -> H1: oversight --------------------------------------------------


class JurisdictionListPublished(EventPayload):
    """``jurisdiction.list_published`` (S3): a new sanctions/jurisdiction list
    version; the watchlist agent re-flags open cases against it.
    """

    version: str
    added: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    fixture: bool = False  # true for the demo's injected list change


class ExaminerAsked(EventPayload):
    """``examiner.asked``: an examiner question about one case; the examiner
    agent answers from the audit chain only.
    """

    case_id: str
    question_id: str
    question: str
    asked_by: str
