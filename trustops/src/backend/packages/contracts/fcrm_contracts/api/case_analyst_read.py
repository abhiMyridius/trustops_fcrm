"""S1's analyst READ surface, in the shapes the web's analyst screens read
(``src/frontend/src/api/types/analyst.ts`` and ``harness.ts``; ADR-30: the web's
type names are the contract).

Every model mirrors a TypeScript interface of the same name: the same wire
keys, the same optionality, the same literal unions
(``tests/unit/packages/contracts/test_analyst_read_matches_web_types.py`` parses
the TypeScript and fails on drift). Where a model carries a key the web does not
declare, it is listed in that test's ``BACKEND_ONLY`` table: the key is additive,
the web ignores it, and it exists so a reader that is not the web (the next
lane's item routes, a test) can name the review item and the gate behind a row.

Nothing here computes anything: a score box, a band, a boundary flag, a rating
and a weight are what S2 answered and H1 or the seed stored; the models only
carry them. These shapes are for the analyst, the committee and the observer
and are never served to the PO (``fcrm_case.po_guard`` and the OPA rules hold
that on the wire).

Models are plain ``BaseModel`` with ``extra="forbid"``, as the PO's are, so a
typo in a builder is an error rather than a silently dropped key.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

from fcrm_contracts.api.case_po import Origin, SourceRef, StageStep
from fcrm_contracts.enums import ChangeType, Domain

BandNo = Literal[1, 2, 3, 4]
RouteName = Literal["analyst", "committee"]
ItemDecision = Literal["pending", "accepted", "removed", "confirmed", "overridden"]


class _Dto(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- shared shapes (common.ts) ---------------------------------------------------


class PinnedVersionsWeb(_Dto):
    """``PinnedVersions`` in ``common.ts``: the versions a case pins, in the
    web's display form (``v3``, ``v14``, ``2026-06``).
    """

    scoring: str
    corpus: str
    controls: str
    field_set: str
    jurisdictions: str
    routing: str
    prompts: str


# --- the score box ---------------------------------------------------------------


class ScoreWithheld(_Dto):
    """``ScoreBox`` before review starts: no number is sent (design C 2.14)."""

    withheld: Literal[True] = True
    reason: str


class PreviousScore(_Dto):
    composite: float
    band: BandNo


class ScoreShown(_Dto):
    """``ScoreBox`` once the analyst has started review: S2's answer as stored."""

    withheld: Literal[False] = False
    composite: float
    band: BandNo
    band_label: str
    band_range: str
    route: RouteName
    route_label: str
    boundary_flag: bool
    boundary_distance: Optional[float]
    boundary_text: Optional[str]
    previous: Optional[PreviousScore]
    model_version: str


ScoreBox = Union[ScoreShown, ScoreWithheld]


# --- queue and dashboard ---------------------------------------------------------


class FastLane(_Dto):
    proposed: bool
    confirmed: bool


class QueueItem(_Dto):
    id: str
    title: str
    unit: str
    owner_name: str
    analyst_name: str
    change_types: list[ChangeType]
    arrived: str
    due: Optional[str]
    stage: str
    stage_label: str
    band: Optional[BandNo]
    band_label: Optional[str]
    composite: Optional[float]
    fast_lane: Optional[FastLane]
    boundary_flag: bool
    critic_count: int
    is_new: bool
    mine: bool


class DashboardKpis(_Dto):
    open_assigned: int
    approaching_due: int
    avg_days_intake_to_decision: float
    closed_cases: int


class BandCount(_Dto):
    band: BandNo
    label: str
    count: int


class MonthCount(_Dto):
    month: str
    count: int


class TypeCount(_Dto):
    type: str
    label: str
    count: int


class AgentEval(_Dto):
    agent: str
    pct: float
    metric: str
    lower_is_better: bool


class ControlHealth(_Dto):
    total: int
    stale: int
    healthy: int
    stale_ids: list[str]
    recency_months: int


class DashboardAnalyst(_Dto):
    kpis: DashboardKpis
    band_distribution: list[BandCount]
    closed_by_month: list[MonthCount]
    cases_by_type: list[TypeCount]
    agent_eval: list[AgentEval]
    control_health: ControlHealth
    mine: list[QueueItem]


# --- the workbench header --------------------------------------------------------


class Clocks(_Dto):
    analyst_active: str
    total_elapsed: str
    analyst_paused: bool


class AuditBadge(_Dto):
    count: int
    verified: bool


class WorkbenchHeader(_Dto):
    id: str
    title: str
    unit: str
    owner_name: str
    analyst_name: str
    change_types: list[ChangeType]
    domains: list[Domain]
    stage: str
    stage_label: str
    lifecycle: list[StageStep]
    clocks: Clocks
    pins: PinnedVersionsWeb
    pending_acknowledgements: list[Literal["boundary", "band_change"]]
    ack_message: Optional[str]
    closed: bool
    submitted: bool
    review_started: bool
    completeness_decision: Optional[Literal["ask", "proceed"]]
    submission_version: int
    locked: bool
    route: Optional[RouteName]
    audit: AuditBadge
    is_demo: bool


# --- the assessment summary ------------------------------------------------------


class Flag(_Dto):
    id: str
    text: str
    source: Optional[SourceRef]


class CriticFinding(_Dto):
    id: str
    type: str
    severity: Literal["high", "medium", "low"]
    text: str
    records: list[int]
    target: Optional[str]
    # Not in the web type: the gate that decides this finding and whether it is
    # still open (the item is ``id``).
    gate_id: Optional[str] = None
    gate_open: bool = False


class DraftQuestion(_Dto):
    id: str
    text: str
    anchor: Optional[str]
    keep: bool
    dropped_reason: Optional[str]


class SubmissionSummary(_Dto):
    version: int
    fields_filled: int
    fields_required: int
    documents: int
    submitted_at: Optional[str]


class CompletenessSummary(_Dto):
    missing: list[str]
    stale: list[str]
    contradictory: list[str]
    drafted: int
    kept: int
    # Not in the web type: the ``completeness`` gate (ask or proceed) and whether
    # it is still open.
    gate_id: Optional[str] = None
    gate_open: bool = False


class FailedStep(_Dto):
    """One job an agent failed on both tries, waiting for the analyst: the agent,
    the stage the run was in (``None`` when the gate did not say) and its gate.
    """

    agent: str
    step: Optional[str]
    gate_id: str


class AgentFailed(_Dto):
    """The Summary's handoff banner (D-117): ``title`` is its heading, ``reason``
    one sentence naming what failed (the web adds what to do), ``step`` the stage
    the run was in, and ``failures`` every open handoff gate, oldest first.
    """

    step: str
    reason: str
    title: str
    failures: list[FailedStep]


class AssessmentSummary(_Dto):
    ready: bool
    submission: SubmissionSummary
    flags: list[Flag]
    completeness: CompletenessSummary
    critic_findings: list[CriticFinding]
    score: ScoreBox
    agent_failed: Optional[AgentFailed]


# --- clarifications ---------------------------------------------------------------


class ThreadAnswerView(_Dto):
    text: str
    documents: list[str]
    at: str
    by: str


class ThreadAnalyst(_Dto):
    id: str
    round: int
    anchor: str
    question: str
    status: Literal["open", "answered", "resolved"]
    answer: Optional[ThreadAnswerView]


class ClarificationRound(_Dto):
    round: int
    sent_at: str
    to: str
    threads: list[ThreadAnalyst]


class ClarificationsView(_Dto):
    drafted: list[DraftQuestion]
    rounds: list[ClarificationRound]
    can_send: bool
    clock_paused: bool


# --- submissions -----------------------------------------------------------------


class WasValue(_Dto):
    value: str
    source: str


class SubmissionField(_Dto):
    field_id: str
    label: str
    value: Optional[str]
    origin: Origin
    source: Optional[SourceRef]
    was: Optional[WasValue]


class SubmissionVersion(_Dto):
    version: int
    label: str
    at: str
    by: str
    fields: list[SubmissionField]
    documents: list[str]


class SubmissionChange(_Dto):
    field_id: str
    label: str
    before: Optional[str]
    after: Optional[str]
    before_origin: Origin
    after_origin: Origin


class SubmissionDiff(_Dto):
    # ``from`` is a Python keyword: the wire key is ``from``.
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: int = Field(alias="from")
    to: int
    changed: list[SubmissionChange]
    added_documents: list[str]


# --- policy ----------------------------------------------------------------------


class ClauseItem(_Dto):
    id: str
    source: str
    title: str
    quote: str
    ref: str
    corpus_version: str
    decision: ItemDecision
    reason: Optional[str]
    # Not in the web type: the review item and the gate behind this row.
    item_id: str
    gate_id: Optional[str] = None
    gate_open: bool = False


class PolicyGap(_Dto):
    text: str
    recorded: bool


class PolicyView(_Dto):
    clauses: list[ClauseItem]
    gap: Optional[PolicyGap]
    guard4_note: Optional[str]
    corpus_version: str


# --- risk ------------------------------------------------------------------------


class Override(_Dto):
    # ``from`` is a Python keyword: the wire key is ``from``.
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: float = Field(alias="from")
    to: float
    by: str
    reason: str


class ControlItem(_Dto):
    id: str
    name: str
    domain: Domain
    weight_norm: float
    rating: float
    proposed_rating: float
    evidence: str
    evidence_date: str
    stale: bool
    decision: ItemDecision
    override: Optional[Override]
    critic: bool
    # Not in the web type: the review item and the gate behind this row.
    item_id: str
    gate_id: Optional[str] = None
    gate_open: bool = False


class PlannedControl(_Dto):
    id: str
    control_id: Optional[str]
    description: str
    owner: str
    due: str
    # Not in the web type: an agent-proposed planned control is a review item with
    # a gate (the web posts ``id`` to ``POST items/{id}``); one the analyst
    # recorded by hand has none.
    gate_open: bool = False


class FactorAnchor(_Dto):
    value: int
    label: str


class RiskArea(_Dto):
    domain: Domain
    label: str
    L: int
    I: int  # noqa: E741 - the wire key of the web type
    inherent: float
    ce: float
    retention: float
    residual: float
    weight: float
    contribution: float
    controls: list[ControlItem]
    matched_caption: str
    justification: str
    lock_blocking: bool
    # Not in the web type: the factor (likelihood and impact) item behind this
    # area and its gate.
    factor_item_id: Optional[str] = None
    factor_gate_id: Optional[str] = None
    factor_gate_open: bool = False


class BandCrossed(_Dto):
    # ``from`` is a Python keyword: the wire key is ``from``.
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: BandNo = Field(alias="from")
    to: BandNo
    record_seq: int
    hash: str
    prev_seq: int


class RiskView(_Dto):
    areas: list[RiskArea]
    score: ScoreBox
    planned: list[PlannedControl]
    critic: Optional[CriticFinding]
    band_crossed: Optional[BandCrossed]
    likelihood_scale: list[FactorAnchor]
    impact_scale: list[FactorAnchor]
    model_version: str


# --- handoff ---------------------------------------------------------------------


class ReleaseCheck(_Dto):
    id: Literal[
        "locked",
        "all_items_reviewed",
        "boundary_and_band_acknowledged",
        "business_summary_present",
    ]
    label: str
    ok: bool
    detail: str


class PackCounts(_Dto):
    composite: float
    band: BandNo
    areas: int
    controls: int
    overrides: int
    planned: int
    clauses: int
    gaps: int
    rounds: int


class PackSummary(_Dto):
    pack_id: str
    version: int
    content_hash: str
    status: Literal["built", "released", "superseded", "decided"]
    built_at: str
    counts: PackCounts


class OutcomeLine(_Dto):
    label: str
    at: str


class HandoffView(_Dto):
    pack: Optional[PackSummary]
    release_checks: list[ReleaseCheck]
    route: Optional[RouteName]
    band: Optional[BandNo]
    can_close_delegated: bool
    released_to: list[str]
    outcome: Optional[OutcomeLine]


# --- activity --------------------------------------------------------------------


class ActivityAnalyst(_Dto):
    id: str
    at: datetime
    actor: str
    type: str
    text: str
    record_seq: Optional[int]
    # Not in the web type: the gate an entry opened or decided, when it names one.
    gate_id: Optional[str] = None


# --- the outcome as staff read it (no web type: the web reads S5 for this) -------


class OutcomeConditionAnalyst(_Dto):
    id: str
    ref: Optional[str]
    text: str
    owner: str
    due: str
    due_date: Optional[str]
    status: str


class OutcomeAnalyst(_Dto):
    """``GET /cases/{id}/outcome`` for the analyst, committee and observer: the
    recorded outcome with who decided it and how, and the conditions it
    activated. The PO reads a different, plainer shape from the same path.
    """

    decided: bool
    outcome: Optional[str]
    outcome_label: Optional[str]
    mode: Optional[Literal["committee", "delegated"]]
    decided_by: Optional[str]
    decided_at: Optional[datetime]
    pack_id: Optional[str]
    reason: Optional[str]
    plain_summary: Optional[str]
    close_requested: bool
    conditions: list[OutcomeConditionAnalyst]


# --- the harness monitor (harness.ts) --------------------------------------------


class HarnessLine(_Dto):
    seq: int
    at: str
    who: str
    text: str
    sub: Optional[str]
    level: Literal["info", "warn", "error"]
    guard: Optional[int]
    progress: Optional[float]


class GuardStatus(_Dto):
    n: int
    name: str
    last: Literal["pass", "block", "idle"]
    detail: str


class HarnessCounters(_Dto):
    agents_run: int
    agents_total: int
    guards_passed: int
    guards_blocked: int
    audit_records: int
    chain_verified: bool
    tokens_used: int
    # The cap H1 enforces on the case (``CASE_TOKEN_CAP``, 160k): a dispatch that
    # would pass it is refused and opens a handoff.
    tokens_budget: int
    # What a clean run is expected to fit in (120k). A case over the target and
    # under the cap is not an error. Defaulted so a snapshot pushed before the
    # field existed still validates.
    tokens_target: int = 120_000


#: The most log lines and guard rows one snapshot carries.
MAX_HARNESS_LINES = 500
MAX_HARNESS_GUARDS = 20


class HarnessSnapshot(_Dto):
    case_id: str
    trace_id: str
    status: Literal["running", "idle", "waiting_on_person"]
    core: Literal["intake core", "assessment core", "decision core", "oversight core"]
    counters: HarnessCounters
    guards: list[GuardStatus] = Field(max_length=MAX_HARNESS_GUARDS)
    log: list[HarnessLine] = Field(max_length=MAX_HARNESS_LINES)
    jaeger_url: str
    tokens_note: str


class HarnessPush(_Dto):
    """``PUT /internal/cases/{id}/harness``: what H1 sends S1 after a step: the
    snapshot it assembled from its own run documents. S1 stores it as it is and
    keeps the log bounded. Analyst, committee and observer read it; the PO never
    does, and nothing in it is derived from a PO request.
    """

    snapshot: HarnessSnapshot
    run_id: str


class HarnessPushResult(_Dto):
    case_id: str
    lines: int
    replayed: bool = False
