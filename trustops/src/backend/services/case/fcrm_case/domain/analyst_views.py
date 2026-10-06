"""How a stored case reads to its analyst: the queue row, the stage and the order
the queue is worked in, the score box, the workbench header and the dashboard.
Pure, no I/O, no clock (callers pass ``now``).

Nothing here computes a score, a band or a recommendation. A score box is built
from the snapshot S2 answered and H1 pushed (or the seed stored); the module
only decides *whether it may be shown yet* and what words go with it:

* the score is **withheld** until the analyst has opened the risk view
  (``review_started_at``), so the first number the analyst sees comes after they
  have looked at the proposals (walkthrough scene 9; the PO never sees one at all);
* a **seeded** case (a snapshot with purpose ``seed``) predates this system and
  shows its stored score from the start.

The module is the one place the stage names, the lifecycle strip and the queue's
"what needs the analyst first" order are written; the Mongo queue pipeline and its
in-memory twin both read :data:`NEEDS_RANK` so the order cannot diverge.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Final, Optional

from fcrm_common.time import add_business_days, business_days_between
from fcrm_contracts.api.case_analyst_read import (
    AgentEval,
    AuditBadge,
    BandCount,
    Clocks,
    ControlHealth,
    DashboardAnalyst,
    DashboardKpis,
    FastLane,
    MonthCount,
    PinnedVersionsWeb,
    PreviousScore,
    QueueItem,
    ScoreBox,
    ScoreShown,
    ScoreWithheld,
    TypeCount,
    WorkbenchHeader,
)
from fcrm_contracts.api.case_po import StageStep
from fcrm_contracts.enums import CaseState, ChangeType, Domain
from fcrm_contracts.versions import PinnedVersions

from fcrm_case.domain.record import CaseRecord
from fcrm_case.domain.views import ScoreSnapshotRow
from fcrm_case.reference import (
    RECENCY_MONTHS,
    ControlRef,
    agent_evals,
    band_table,
    change_type_labels,
    months_between,
    scoring_model_version,
)

#: A case is decision-ready this many business days after it is submitted.
SLA_BUSINESS_DAYS: Final = 2
#: A case due within this many business days (or already late) is "approaching due".
APPROACHING_DUE_DAYS: Final = 1

#: Snapshot purpose of a row the seed loaded (it predates this system).
SEED_PURPOSE: Final = "seed"

WITHHELD_REASON: Final = (
    "No score is shown yet. Review the proposals in Policy and Risk & Controls first."
)
RUNNING_REASON: Final = "The assessment core is still running."

#: The analyst's lifecycle strip.
LIFECYCLE: Final[tuple[tuple[str, str], ...]] = (
    ("intake", "Intake"),
    ("review_and_submit", "Review & submit"),
    ("triage", "Triage"),
    ("screening", "Screening"),
    ("assess", "Assess"),
    ("critic", "Critic"),
    ("completeness", "Completeness"),
    ("clarification", "Clarification"),
    ("review", "Review"),
    ("decide", "Decide"),
    ("oversight", "Oversight"),
)
_STEP: Final[dict[CaseState, int]] = {
    CaseState.INTAKE: 0,
    CaseState.REVIEW_AND_SUBMIT: 1,
    CaseState.TRIAGE: 2,
    CaseState.SCREENING: 3,
    CaseState.ASSESS: 4,
    CaseState.CRITIC: 5,
    CaseState.COMPLETENESS: 6,
    CaseState.CLARIFICATION: 7,
    CaseState.REVIEW: 8,
    CaseState.RECOMMEND: 8,
    CaseState.HANDOFF: 8,
    CaseState.DECIDE: 9,
    CaseState.CLOSED: 10,
}

#: The states a queue ``stage`` filter selects (the web's options). ``fast_lane``
#: is a flag, not a state, and is filtered on ``stage_flags``.
STAGE_STATES: Final[dict[str, tuple[CaseState, ...]]] = {
    "new": (CaseState.TRIAGE, CaseState.COMPLETENESS),
    "assessing": (CaseState.SCREENING, CaseState.ASSESS, CaseState.CRITIC),
    "review": (CaseState.REVIEW,),
    "awaiting_business": (CaseState.CLARIFICATION,),
    "recommend": (CaseState.RECOMMEND, CaseState.HANDOFF),
    "committee": (CaseState.DECIDE,),
    "closed": (CaseState.CLOSED,),
}
FAST_LANE_STAGE: Final = "fast_lane"
QUEUE_STAGES: Final[tuple[str, ...]] = (*STAGE_STATES, FAST_LANE_STAGE)

#: What needs the analyst first (lower is first): a case waiting for the analyst's
#: triage or completeness decision, then one in review, then one ready to lock or
#: release, then one the agents are still working on, then the cases waiting on
#: someone else (the owner, then the committee), and the closed ones last.
NEEDS_RANK: Final[dict[CaseState, int]] = {
    CaseState.TRIAGE: 0,
    CaseState.COMPLETENESS: 0,
    CaseState.REVIEW: 1,
    CaseState.RECOMMEND: 2,
    CaseState.HANDOFF: 2,
    CaseState.SCREENING: 3,
    CaseState.ASSESS: 3,
    CaseState.CRITIC: 3,
    CaseState.CLARIFICATION: 4,
    CaseState.DECIDE: 5,
    CaseState.CLOSED: 6,
    CaseState.INTAKE: 7,
    CaseState.REVIEW_AND_SUBMIT: 7,
}


@dataclass(frozen=True)
class StageInfo:
    """A case's analyst-facing stage: the filter key, its label and the strip step."""

    stage: str
    label: str
    step: int


def analyst_stage(state: CaseState, *, locked: bool) -> StageInfo:
    """The analyst's name for a case in ``state`` (``locked`` splits the
    recommendation step into drafting and locked-for-handoff).
    """
    step = _STEP[state]
    if state in (CaseState.INTAKE, CaseState.REVIEW_AND_SUBMIT):
        return StageInfo("intake", "Intake (owner)", step)
    if state in STAGE_STATES["new"]:
        return StageInfo("new", "New", step)
    if state in STAGE_STATES["assessing"]:
        return StageInfo("assessing", "Assessing (agents running)", step)
    if state is CaseState.CLARIFICATION:
        return StageInfo("awaiting_business", "Awaiting business", step)
    if state is CaseState.REVIEW:
        return StageInfo("review", "Review", step)
    if state in STAGE_STATES["recommend"]:
        if locked:
            return StageInfo("recommend", "Locked · handoff", 9)
        return StageInfo("recommend", "Recommendation", step)
    if state is CaseState.DECIDE:
        return StageInfo("committee", "Committee", step)
    return StageInfo("closed", "Closed", step)


def lifecycle(info: StageInfo) -> list[StageStep]:
    """The strip: everything before the current step done, the current one
    current, the rest upcoming.
    """
    return [
        StageStep(
            key=key,
            label=label,
            state="done"
            if i < info.step
            else "current"
            if i == info.step
            else "upcoming",
        )
        for i, (key, label) in enumerate(LIFECYCLE)
    ]


# -- the score box -----------------------------------------------------------------


def band_no(value: int) -> int:
    """``value`` as a band number, refusing anything the model has no band for.

    Raises:
        ValueError: ``value`` is not one of the model's bands.
    """
    if value not in band_table():
        raise ValueError(f"no scoring band {value}")
    return value


def route_label(route: str) -> str:
    """The words for a route (R1 delegated close, R2 committee)."""
    return "Risk Committee (R2)" if route == "committee" else "Delegated close (R1)"


def boundary_text(
    composite: float, band: int, distance: Optional[float]
) -> Optional[str]:
    """The sentence under a flagged score: how far it sits from a band edge. The
    distance is the stored one (S2's); this only says which edge it is measured to.
    """
    if distance is None:
        return None
    table = band_table()
    nxt = table.get(band + 1)
    if nxt is not None and abs(nxt.lower - composite - distance) < 0.05:
        return f"Boundary flag · {distance:.1f} from the Band {band + 1} edge"
    current = table[band]
    return (
        f"Boundary flag · {distance:.1f} above the Band {band} edge "
        f"({current.lower:.1f})"
    )


def score_visible(snapshot: Optional[ScoreSnapshotRow], review_started: bool) -> bool:
    """Whether the analyst may be shown ``snapshot``'s numbers: once review has
    started, or at once for a seeded row.
    """
    if snapshot is None:
        return False
    return review_started or snapshot.purpose == SEED_PURPOSE


def score_shown(snapshot: ScoreSnapshotRow) -> ScoreShown:
    """The score box for a snapshot: S2's figures and the words around them."""
    band = band_no(snapshot.band)
    row = band_table()[band]
    previous = (
        PreviousScore(
            composite=snapshot.previous_composite,
            band=band_no(snapshot.previous_band),  # type: ignore[arg-type]
        )
        if snapshot.previous_composite is not None
        and snapshot.previous_band is not None
        else None
    )
    return ScoreShown(
        composite=snapshot.composite,
        band=band,  # type: ignore[arg-type]
        band_label=row.label,
        band_range=row.range_text,
        route=snapshot.route,  # type: ignore[arg-type]
        route_label=route_label(snapshot.route),
        boundary_flag=snapshot.boundary_flag,
        boundary_distance=snapshot.boundary_distance,
        boundary_text=boundary_text(
            snapshot.composite, band, snapshot.boundary_distance
        )
        if snapshot.boundary_flag
        else None,
        previous=previous,
        model_version=(
            f"v{snapshot.scoring_v}"
            if snapshot.scoring_v is not None
            else scoring_model_version()
        ),
    )


def score_box(
    snapshot: Optional[ScoreSnapshotRow],
    *,
    review_started: bool,
    assessment_ready: bool,
) -> ScoreBox:
    """The score box the analyst reads: the numbers when they may be shown, else
    the reason there are none.
    """
    if snapshot is None or not assessment_ready:
        return ScoreWithheld(reason=RUNNING_REASON)
    if not score_visible(snapshot, review_started):
        return ScoreWithheld(reason=WITHHELD_REASON)
    return score_shown(snapshot)


# -- the queue ---------------------------------------------------------------------


@dataclass(frozen=True)
class QueueRow:
    """What the queue aggregation returns for one case: the case's own fields
    and the two things the one pipeline joins in (its latest score snapshot's
    band figures and its critic findings' count).
    """

    case_id: str
    title: str
    unit: str
    owner: str
    owner_name: Optional[str]
    analyst_id: Optional[str]
    analyst_name: Optional[str]
    change_types: tuple[str, ...]
    state: CaseState
    submitted_at: Optional[datetime]
    review_started: bool
    fast_lane_proposed: bool
    locked: bool
    band: Optional[int]
    composite: Optional[float]
    boundary_flag: bool
    snapshot_purpose: Optional[str]
    critic_count: int


def due_date(submitted_at: Optional[datetime], state: CaseState) -> Optional[date]:
    """When a case is due: two business days after it was submitted; ``None``
    once closed or before it is submitted.
    """
    if submitted_at is None or state is CaseState.CLOSED:
        return None
    return add_business_days(submitted_at.astimezone(UTC).date(), SLA_BUSINESS_DAYS)


def is_approaching_due(due: Optional[date], today: date) -> bool:
    """Whether ``due`` is within a business day of ``today``, or already past."""
    if due is None:
        return False
    return business_days_between(today, due) <= APPROACHING_DUE_DAYS


def _change_types(values: Sequence[str]) -> list[ChangeType]:
    return [ChangeType(v) for v in values]


def queue_item(row: QueueRow, viewer_id: str) -> QueueItem:
    """One queue row. The band and composite are shown when the snapshot may be
    shown (a seeded row at once, a live one after review has started), else
    ``None``: the web prints "withheld until review".
    """
    info = analyst_stage(row.state, locked=row.locked)
    visible = row.band is not None and (
        row.review_started or row.snapshot_purpose == SEED_PURPOSE
    )
    due = due_date(row.submitted_at, row.state)
    band = band_no(row.band) if visible and row.band is not None else None
    return QueueItem(
        id=row.case_id,
        title=row.title,
        unit=row.unit,
        owner_name=row.owner_name or row.owner,
        analyst_name=row.analyst_name or "",
        change_types=_change_types(row.change_types),
        arrived=row.submitted_at.astimezone(UTC).date().isoformat()
        if row.submitted_at is not None
        else "",
        due=due.isoformat() if due is not None else None,
        stage=info.stage,
        stage_label=info.label,
        band=band,  # type: ignore[arg-type]
        band_label=band_table()[band].label if band is not None else None,
        composite=row.composite if visible else None,
        fast_lane=FastLane(proposed=True, confirmed=False)
        if row.fast_lane_proposed
        else None,
        boundary_flag=bool(visible and row.boundary_flag),
        critic_count=row.critic_count,
        is_new=info.stage == "new",
        mine=row.analyst_id is not None and row.analyst_id == viewer_id,
    )


# -- the dashboard -----------------------------------------------------------------


@dataclass(frozen=True)
class PortfolioFigures:
    """The counts the dashboard aggregations return (all of them computed from
    stored cases and score snapshots).
    """

    closed_cases: int
    avg_days_to_decision: Optional[float]
    closed_by_month: tuple[tuple[str, int], ...]
    by_type: tuple[tuple[str, int], ...]
    bands: tuple[tuple[int, int], ...]


def dashboard(
    figures: PortfolioFigures,
    mine: list[QueueItem],
    controls: Sequence[ControlRef],
    today: date,
) -> DashboardAnalyst:
    """The analyst dashboard: KPIs and charts from ``figures``, the viewer's open
    cases, the seed's weekly agent evaluation and control-evidence health (a
    control is stale when its last test is older than :data:`RECENCY_MONTHS`).
    """
    counts = dict(figures.bands)
    stale = sorted(
        c.control_id
        for c in controls
        if c.last_tested is not None
        and months_between(c.last_tested, today) > RECENCY_MONTHS
    )
    labels = change_type_labels()
    return DashboardAnalyst(
        kpis=DashboardKpis(
            open_assigned=len([m for m in mine if m.stage != "closed"]),
            approaching_due=sum(
                1
                for m in mine
                if m.due is not None
                and is_approaching_due(date.fromisoformat(m.due), today)
            ),
            avg_days_intake_to_decision=figures.avg_days_to_decision or 0.0,
            closed_cases=figures.closed_cases,
        ),
        band_distribution=[
            BandCount(
                band=band_no(b),  # type: ignore[arg-type]
                label=row.label,
                count=counts.get(b, 0),
            )
            for b, row in sorted(band_table().items())
        ],
        closed_by_month=[
            MonthCount(month=m, count=n) for m, n in figures.closed_by_month
        ],
        cases_by_type=[
            TypeCount(type=t, label=labels.get(t, t), count=n)
            for t, n in sorted(figures.by_type, key=lambda kv: (-kv[1], kv[0]))
        ],
        agent_eval=[
            AgentEval(
                agent=agent,
                pct=pct,
                metric=metric,
                lower_is_better=agent == "assessment",
            )
            for agent, pct, metric in agent_evals()
        ],
        control_health=ControlHealth(
            total=len(controls),
            stale=len(stale),
            healthy=len(controls) - len(stale),
            stale_ids=stale,
            recency_months=RECENCY_MONTHS,
        ),
        mine=mine,
    )


# -- the workbench header ----------------------------------------------------------


def hm(span: timedelta) -> str:
    """``0h 25m`` under a day, ``1d 2h`` from a day up (never negative)."""
    seconds = max(int(span.total_seconds()), 0)
    hours, rest = divmod(seconds, 3600)
    if hours >= 24:
        days, hours = divmod(hours, 24)
        return f"{days}d {hours}h"
    return f"{hours}h {rest // 60:02d}m"


@dataclass(frozen=True)
class StageSpan:
    """A stretch of a case's life in one state, from the ``case.stage_changed``
    log: when it began, and when it ended (``None`` while it is current).
    """

    state: str
    began: datetime
    ended: Optional[datetime]


def clocks(
    submitted_at: Optional[datetime],
    closed_at: Optional[datetime],
    spans: Sequence[StageSpan],
    now: datetime,
) -> Clocks:
    """The two clocks (design A section 2.1.4): the total time since submission,
    and the analyst's own time, which stops while a round of questions is with the
    owner (the case sits in ``clarification``).
    """
    if submitted_at is None:
        return Clocks(
            analyst_active="0h 00m", total_elapsed="0h 00m", analyst_paused=False
        )
    end = closed_at or now
    total = end - submitted_at
    paused = timedelta()
    paused_now = False
    for span in spans:
        if span.state != CaseState.CLARIFICATION.value:
            continue
        stop = span.ended or end
        paused += max(stop - max(span.began, submitted_at), timedelta())
        paused_now = paused_now or span.ended is None
    return Clocks(
        analyst_active=hm(total - paused),
        total_elapsed=hm(total),
        analyst_paused=paused_now and closed_at is None,
    )


def pins_web(pins: PinnedVersions) -> PinnedVersionsWeb:
    """A case's pinned versions as the header prints them."""
    return PinnedVersionsWeb(
        scoring=f"v{pins.scoring_v}",
        corpus=f"v{pins.corpus_v}",
        controls=f"v{pins.controls_v}",
        field_set=f"v{pins.fieldset_v}",
        jurisdictions=pins.jurisdictions_v,
        routing=f"v{pins.routing_v}",
        prompts=pins.prompts_v,
    )


@dataclass(frozen=True)
class Acknowledgement:
    """One stored analyst acknowledgement (R3): its kind and when it was made."""

    kind: str
    at: datetime


def pending_acknowledgements(
    snapshot: Optional[ScoreSnapshotRow], acks: Sequence[Acknowledgement]
) -> list[str]:
    """The acknowledgements still owed on the latest score (R3).

    A band change is owed until an acknowledgement made *after* that score; a
    boundary flag is owed until one acknowledgement of that kind exists (an
    acknowledgement covers the flag until the case is re-scored into a different
    band). Read from stored rows; H1 opens the matching gates and refuses the pack
    release while one is open, so this only tells the analyst what is owed.
    """
    if snapshot is None:
        return []
    owed: list[str] = []
    if snapshot.band_changed and not any(
        a.kind == "band_change" and a.at >= snapshot.computed_at for a in acks
    ):
        owed.append("band_change")
    if snapshot.boundary_flag and not any(a.kind == "boundary" for a in acks):
        owed.append("boundary")
    return owed


def ack_message(
    owed: Sequence[str], snapshot: Optional[ScoreSnapshotRow]
) -> Optional[str]:
    """The banner text for what is owed, or ``None``."""
    if not owed or snapshot is None:
        return None
    distance = (
        f"{snapshot.boundary_distance:.1f}"
        if snapshot.boundary_distance is not None
        else ""
    )
    if "band_change" in owed:
        was = snapshot.previous_band if snapshot.previous_band is not None else "?"
        return (
            f"Band changed ({was} → {snapshot.band}) and the composite sits "
            f"{distance} from a band edge. Acknowledge before this case can be "
            "released: the route changed from delegated close to committee."
            if snapshot.route == "committee"
            else f"Band changed ({was} → {snapshot.band}). Acknowledge before "
            "this case can be released."
        )
    return (
        f"The composite sits {distance} from a band edge. Acknowledge before this "
        "case can be released (R3)."
    )


def completeness_decision(record: CaseRecord) -> Optional[str]:
    """What the analyst decided at the completeness gate, as far as the case shows:
    nothing yet before it; ``ask`` when a round of questions was sent (the case went
    to clarification, or came back from it as a later submission version); else
    ``proceed``.
    """
    state = record.state
    if state in (
        CaseState.INTAKE,
        CaseState.REVIEW_AND_SUBMIT,
        CaseState.TRIAGE,
        CaseState.SCREENING,
        CaseState.ASSESS,
        CaseState.CRITIC,
        CaseState.COMPLETENESS,
    ):
        return None
    if state is CaseState.CLARIFICATION:
        return "ask"
    return "ask" if (record.current_submission_v or 1) > 1 else "proceed"


def assessment_ready(record: CaseRecord, has_completeness_view: bool) -> bool:
    """Whether the assessment core has finished its part: the completeness report
    exists, or the case is already past it.
    """
    if has_completeness_view:
        return True
    return record.state in (
        CaseState.CLARIFICATION,
        CaseState.REVIEW,
        CaseState.RECOMMEND,
        CaseState.HANDOFF,
        CaseState.DECIDE,
        CaseState.CLOSED,
    )


@dataclass(frozen=True)
class HeaderInputs:
    """Everything the workbench header is built from, already read."""

    record: CaseRecord
    snapshot: Optional[ScoreSnapshotRow]
    review_started: bool
    has_completeness_view: bool
    acks: Sequence[Acknowledgement]
    spans: Sequence[StageSpan]
    audit_count: int
    audit_verified: bool
    now: datetime


def workbench_header(inputs: HeaderInputs) -> WorkbenchHeader:
    """The case summary header: stage, strip, clocks, pins, what is owed and what
    to do next. Route and acknowledgements are score-derived, so they follow the
    score box's visibility.
    """
    record = inputs.record
    info = analyst_stage(record.state, locked=record.stage_flags.locked)
    ready = assessment_ready(record, inputs.has_completeness_view)
    box = score_box(
        inputs.snapshot,
        review_started=inputs.review_started,
        assessment_ready=ready,
    )
    shown = isinstance(box, ScoreShown)
    owed = pending_acknowledgements(inputs.snapshot, inputs.acks) if shown else []
    started = inputs.review_started or (
        inputs.snapshot is not None and inputs.snapshot.purpose == SEED_PURPOSE
    )
    return WorkbenchHeader(
        id=record.case_id,
        title=record.title,
        unit=record.unit,
        owner_name=record.owner_name or record.owner,
        analyst_name=record.analyst_name or "",
        change_types=_change_types([c.value for c in record.change_types]),
        domains=[Domain(d) for d in record.domains],
        stage=info.stage,
        stage_label=info.label,
        lifecycle=lifecycle(info),
        clocks=clocks(record.submitted_at, record.closed_at, inputs.spans, inputs.now),
        pins=pins_web(record.pinned_versions),
        pending_acknowledgements=owed,  # type: ignore[arg-type]
        ack_message=ack_message(owed, inputs.snapshot),
        closed=record.state is CaseState.CLOSED,
        submitted=record.submitted_at is not None,
        review_started=started,
        completeness_decision=completeness_decision(record),  # type: ignore[arg-type]
        submission_version=record.current_submission_v or 0,
        locked=record.stage_flags.locked,
        route=box.route if isinstance(box, ScoreShown) else None,
        audit=AuditBadge(count=inputs.audit_count, verified=inputs.audit_verified),
        is_demo=record.run_id is not None,
    )
