"""The transition table and the pure reducer.

Every row below is transcribed directly from design A §3.1's transition
table (one row per ``|`` line in that table), in the same order. Where a
single ``(from_state, trigger)`` pair in the design table has more than one
possible outcome depending on a guard (e.g. SCREENING's fast-lane proposal,
or DECIDE's committee-answer path), it becomes more than one
:class:`Transition` here, tried in the order written, and the first whose
guard passes wins — this is what "Guard(s)" means operationally.

Nothing here performs I/O. ``action`` is a short label, not executable
code: the real runtime (``fcrm_caseflow.cores.*``, built once
``fcrm_common`` exists) switches on it to do the actual HTTP calls, job
dispatch and audit writes. This separation is deliberate — it is what
makes the *decision* of "what state comes next and why" independently
testable from "how that decision is carried out".
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Optional

from fcrm_contracts.enums import CaseState

from fcrm_caseflow.exceptions import IllegalTransition
from fcrm_caseflow.states import RunFlags
from fcrm_caseflow.triggers import Trigger


@dataclass(frozen=True)
class TransitionContext:
    """Everything a guard might need to decide, and everything an action
    label might need the caller to know was true when it fired. Every
    field defaults to a harmless value so most tests only need to set the
    one or two fields relevant to the row under test.
    """

    flags: RunFlags = field(default_factory=RunFlags)
    all_po_gates_decided: bool = True
    four_submit_checks_passed: bool = True
    fast_lane_proposed: bool = False
    fast_lane_confirmed: bool = False
    rating_or_control_changed: bool = False
    band_changed: bool = False
    boundary_flagged: bool = False
    gaps_open: bool = True
    all_items_decided: bool = False
    acks_done: bool = True
    release_checks_passed: bool = True
    rerun_reason: Optional[str] = None
    unlock_reason: Optional[str] = None
    committee_numbers_changed: bool = False
    vote_outcome: Optional[str] = None  # set when the trigger is VOTE_TALLIED
    band: Optional[int] = None  # 1-4; used to route R1 (delegated) vs R2 (committee)
    resume_state: Optional[CaseState] = None
    """The state a run in HANDOFF resumes to: the one it was suspended from
    (``CaseRun.suspended_from``). Read only by ``GATE_DECIDED_HANDOFF``; recorded
    with the transition so a replay lands where the live run did."""


@dataclass(frozen=True)
class TransitionResult:
    from_state: CaseState
    to_state: CaseState
    action: str
    trigger: Trigger


Guard = Callable[[TransitionContext], bool]


def _always(_: TransitionContext) -> bool:
    return True


@dataclass(frozen=True)
class Transition:
    from_state: CaseState
    trigger: Trigger
    to_state: CaseState
    action: str
    guard: Guard = _always


S = CaseState

#: Design A §3.1's first row ("— / case.created / — / open run / INTAKE")
#: has no "from" state — a run does not exist yet when it fires. It is
#: therefore not a row in :data:`TRANSITIONS` (which always matches an
#: existing state) but the fixed starting point the runtime uses instead
#: of calling :func:`apply` at all when it sees ``case.created``.
INITIAL_STATE = S.INTAKE


def open_run() -> CaseState:
    """What the runtime sets a new run's state to on ``case.created``,
    before opening the root span and pinning versions — see design A
    §3.1's first transition row.
    """
    return INITIAL_STATE


# --- INTAKE -----------------------------------------------------------
_INTAKE = [
    Transition(
        S.INTAKE,
        Trigger.CASE_DOCUMENT_STORED,
        S.INTAKE,
        "ingest.run; on ingest.done + Guard 1, dispatch intake agent "
        "once every stored document is extracted",
    ),
    Transition(
        S.INTAKE,
        Trigger.INGEST_DONE,
        S.INTAKE,
        "H3 extracted one document: record its Guard 1 result; dispatch "
        "the intake agent once every stored document is extracted",
    ),
    Transition(
        S.INTAKE,
        Trigger.INGEST_FAILED,
        S.INTAKE,
        "H3 could not extract a document (or Guard 1 quarantined it): "
        "record why; no agent ever reads a quarantined document",
    ),
    Transition(
        S.INTAKE,
        Trigger.AGENT_DONE_INTAKE,
        S.INTAKE,
        "write proposals to S4; push items to S1; resolve field set "
        "against S3; match precedents; open PO gates",
    ),
    Transition(
        S.INTAKE,
        Trigger.GATE_DECIDED_PO_CONFIRM_CATEGORY_ALL,
        S.INTAKE,
        "fieldset.resolved; if gaps are still open, dispatch intake "
        "again for questions only",
    ),
    # case.submitted passes through REVIEW_AND_SUBMIT immediately — design
    # A §3.1 writes the target as "REVIEW_AND_SUBMIT -> TRIAGE (immediate)";
    # the four submit checks (owner review included) have already passed by
    # the time this trigger fires, so there is no state the machine parks
    # in between.
    Transition(
        S.INTAKE,
        Trigger.CASE_SUBMITTED,
        S.TRIAGE,
        "record case.submitted; request an intake eval grade",
        guard=lambda ctx: ctx.four_submit_checks_passed,
    ),
]

# --- TRIAGE / SCREENING (R5, R4) ---------------------------------------
_TRIAGE_SCREENING = [
    Transition(
        S.TRIAGE,
        Trigger.TRIAGE_DISPATCH,
        S.SCREENING,
        "R5: assign the analyst with the fewest open cases; record triage.assigned",
    ),
    Transition(
        S.SCREENING,
        Trigger.SCREENING_DISPATCH,
        S.SCREENING,
        "reserve budget; dispatch the screening agent",
    ),
    Transition(
        S.SCREENING,
        Trigger.AGENT_DONE_SCREENING,
        S.SCREENING,
        "record screening.proposed; open the fast_lane_confirm gate",
        guard=lambda ctx: ctx.fast_lane_proposed,
    ),
    Transition(
        S.SCREENING,
        Trigger.AGENT_DONE_SCREENING,
        S.ASSESS,
        "record screening.proposed (no fast lane)",
        guard=lambda ctx: not ctx.fast_lane_proposed,
    ),
    Transition(
        S.SCREENING,
        Trigger.GATE_DECIDED_FAST_LANE_CONFIRM,
        S.ASSESS,
        "confirmed: set fast_lane=true, dispatch assessment only; "
        "declined: dispatch the full research+assessment path",
    ),
]

# --- ASSESS / CRITIC / COMPLETENESS -------------------------------------
_ASSESS_CRITIC_COMPLETENESS = [
    Transition(
        S.ASSESS,
        Trigger.ASSESS_DISPATCH,
        S.ASSESS,
        "reserve budget twice; dispatch research and assessment in "
        "parallel (fast lane: assessment only)",
    ),
    Transition(
        S.ASSESS,
        Trigger.AGENT_DONE_ASSESS_BOTH,
        S.CRITIC,
        "proposals to S4; items to S1; build a ScoreRequest with no "
        "planned controls; POST /score; push the snapshot; record "
        "score.computed and boundary.flagged if flagged",
    ),
    Transition(
        S.CRITIC,
        Trigger.CRITIC_DISPATCH,
        S.CRITIC,
        "reserve budget; dispatch the critic agent",
    ),
    Transition(
        S.CRITIC,
        Trigger.AGENT_DONE_CRITIC,
        S.COMPLETENESS,
        "push the critic view; record critic.findings",
    ),
    Transition(
        S.COMPLETENESS,
        Trigger.COMPLETENESS_DISPATCH,
        S.COMPLETENESS,
        "reserve budget; dispatch narrative in completeness mode",
    ),
    Transition(
        S.COMPLETENESS,
        Trigger.AGENT_DONE_NARRATIVE_COMPLETENESS,
        S.COMPLETENESS,
        "push the completeness view and drafted questions; record "
        "completeness.report; open the completeness gate",
    ),
    Transition(
        S.COMPLETENESS,
        Trigger.GATE_DECIDED_COMPLETENESS_ASK,
        S.CLARIFICATION,
        "record clarification.sent; pause the analyst clock (one round only, D-16)",
    ),
    Transition(
        S.COMPLETENESS,
        Trigger.GATE_DECIDED_COMPLETENESS_PROCEED,
        S.REVIEW,
        "no clarification needed; proceed straight to review",
    ),
]

# --- CLARIFICATION -------------------------------------------------------
_CLARIFICATION = [
    Transition(
        S.CLARIFICATION,
        Trigger.CASE_ANSWERED,
        S.CLARIFICATION,
        "diff v(n-1) to v(n); Guard 1 on any new document; dispatch "
        "research+assessment scoped to changed_fields; record "
        "agent.rerun; a planned control is recorded, never scored",
    ),
    Transition(
        S.CLARIFICATION,
        Trigger.CASE_DOCUMENT_STORED,
        S.CLARIFICATION,
        "the owner added a document with an answer: ingest.run for it, the "
        "same path as at intake; the re-run waits for it",
    ),
    Transition(
        S.CLARIFICATION,
        Trigger.INGEST_DONE,
        S.CLARIFICATION,
        "H3 extracted a document the owner added with an answer: record "
        "its Guard 1 result; the re-run waits for it",
    ),
    Transition(
        S.CLARIFICATION,
        Trigger.INGEST_FAILED,
        S.CLARIFICATION,
        "H3 could not extract an added document (or Guard 1 quarantined "
        "it): record why and flag the analyst",
    ),
    Transition(
        S.CLARIFICATION,
        Trigger.AGENT_DONE_RERUN_BOTH,
        S.REVIEW,
        "new proposals supersede the ones whose inputs changed; "
        "POST /score; push the new snapshot",
    ),
]

# --- REVIEW --------------------------------------------------------------
_REVIEW = [
    Transition(
        S.REVIEW,
        Trigger.GATE_DECIDED_REVIEW_ITEM,
        S.REVIEW,
        "update the item; recompute the score if a rating or control "
        "changed, recording human.override + score.recomputed; open "
        "the ack gate (R3) on a band change or boundary flag",
    ),
    Transition(
        S.REVIEW,
        Trigger.AGENT_DONE_RERUN_BOTH,
        S.REVIEW,
        "a re-run the analyst asked for (or a re-tag's new domain) finished: new "
        "proposals supersede the ones whose inputs changed; POST /score; push "
        "the new snapshot",
    ),
    Transition(
        S.REVIEW,
        Trigger.CASE_RETAGGED,
        S.REVIEW,
        "expand the field set; dispatch research+assessment for the "
        "newly added domain only",
    ),
    Transition(
        S.REVIEW,
        Trigger.CASE_RERUN_REQUESTED,
        S.REVIEW,
        "dispatch per the requested scope; prior proposals are kept, "
        "marked superseded_by",
        guard=lambda ctx: bool(ctx.rerun_reason),
    ),
    Transition(
        S.REVIEW,
        Trigger.CASE_GAP_RECORDED,
        S.REVIEW,
        "record gap.recorded; dispatch policy_gap as a non-blocking oversight job",
    ),
    Transition(
        S.REVIEW,
        Trigger.CASE_CONTROL_PLANNED,
        S.REVIEW,
        "record control.planned; a planned control is shown to the "
        "analyst and never scored",
    ),
    Transition(
        S.REVIEW,
        Trigger.ALL_ITEMS_DECIDED,
        S.RECOMMEND,
        "every review item decided and every ack done; dispatch "
        "narrative in recommendation mode",
        guard=lambda ctx: ctx.all_items_decided and ctx.acks_done,
    ),
]

# --- RECOMMEND -------------------------------------------------------------
_RECOMMEND = [
    Transition(
        S.RECOMMEND,
        Trigger.AGENT_DONE_NARRATIVE_RECOMMENDATION,
        S.RECOMMEND,
        "push the recommendation draft and conditions; record "
        "proposal.narrative and guard.output (restricted-term check)",
    ),
    Transition(
        S.RECOMMEND,
        Trigger.CASE_LOCKED,
        S.RECOMMEND,
        "record recommendation.locked; build the pack from S1's view "
        "and S4's head; POST /packs; run the four release checks",
    ),
    Transition(
        S.RECOMMEND,
        Trigger.PACK_RELEASED,
        S.DECIDE,
        "record pack.approved; evaluate R1/R2 from the latest band to pick the route",
        guard=lambda ctx: ctx.release_checks_passed,
    ),
    Transition(
        S.RECOMMEND,
        Trigger.CASE_UNLOCKED,
        S.REVIEW,
        "record case.unlocked; supersede the pack if one exists",
        guard=lambda ctx: bool(ctx.unlock_reason),
    ),
]

# A Band 1-2 case is closed by the analyst under delegated authority (R1) from
# where it is: its recommendation is locked and its pack built, never released
# to the committee (S5 refuses a delegated outcome over an open committee pack),
# so the run is still in RECOMMEND when the close is requested. The DECIDE rows
# below cover a case whose pack was released and has since been closed the same way.
_RECOMMEND_DELEGATED = [
    Transition(
        S.RECOMMEND,
        Trigger.CASE_CLOSE_REQUESTED,
        S.RECOMMEND,
        "R1: Band 1-2 delegated close from a locked recommendation; "
        "POST /outcomes{mode: delegated}, awaiting outcome.set",
        guard=lambda ctx: ctx.band in (1, 2) and ctx.acks_done and ctx.flags.locked,
    ),
    Transition(
        S.RECOMMEND,
        Trigger.OUTCOME_SET_DELEGATED,
        S.CLOSED,
        "delegated close resolves the same way a committee outcome does",
    ),
]

# --- DECIDE ----------------------------------------------------------------
_DECIDE = [
    Transition(
        S.DECIDE,
        Trigger.CASE_CLOSE_REQUESTED,
        S.DECIDE,
        "R1: Band 1-2 delegated close; POST /outcomes{mode: delegated}, "
        "awaiting outcome.set",
        guard=lambda ctx: ctx.band in (1, 2) and ctx.acks_done,
    ),
    Transition(
        S.DECIDE,
        Trigger.VOTE_CAST,
        S.DECIDE,
        "R2: record vote.cast (count only, never the option, until all are in)",
    ),
    Transition(
        S.DECIDE,
        Trigger.COMMITTEE_QUESTION_ASKED,
        S.DECIDE,
        "open a committee thread",
    ),
    Transition(
        S.DECIDE,
        Trigger.COMMITTEE_ANSWERED_NO_CHANGE,
        S.DECIDE,
        "record committee.question and committee.answer as a pack "
        "addendum; numbers unchanged",
        guard=lambda ctx: not ctx.committee_numbers_changed,
    ),
    Transition(
        S.DECIDE,
        Trigger.COMMITTEE_ANSWERED_CHANGED,
        S.REVIEW,
        "R6: unlock with reason, exactly like case.unlocked; pack v2 "
        "will follow, then every cast vote is re-requested",
        guard=lambda ctx: ctx.committee_numbers_changed,
    ),
    Transition(
        S.DECIDE,
        Trigger.VOTE_TALLIED,
        S.CLOSED,
        "record vote.revealed and outcome.set; activate conditions",
    ),
    Transition(
        S.DECIDE,
        Trigger.VOTE_NO_MAJORITY,
        S.DECIDE,
        "ADR-08: no option got a majority; record no_majority; open a "
        "new round on the same pack (POST /packs/{id}/revote), never "
        "auto-resolve to defer or any other outcome",
    ),
    Transition(
        S.DECIDE,
        Trigger.OUTCOME_SET_DELEGATED,
        S.CLOSED,
        "delegated close resolves the same way a committee outcome does",
    ),
]

# --- CLOSED / HANDOFF ------------------------------------------------------
_CLOSED_HANDOFF = [
    Transition(
        S.CLOSED,
        Trigger.CLOSED_DISPATCH,
        S.CLOSED,
        "record outcome.delivered and case.closed; GET /verify and "
        "record chain.verified; POST the case into the precedent "
        "index; end the root span",
    ),
    Transition(
        S.HANDOFF,
        Trigger.GATE_DECIDED_HANDOFF,
        S.REVIEW,  # the default only: the runtime resumes to the state the run was
        # suspended from and passes it in as `ctx.resume_state`, which `apply`
        # prefers (see `_resume_target`). A run that has none (suspended before
        # the runtime remembered it) resumes to REVIEW.
        "treat the manual output like agent.done, actor=human",
    ),
]

# --- ad-hoc questions to the owner (ADR-73) ------------------------------------
# The analyst may ask the owner a question at any stage from triage to an unlocked
# recommendation (S1 refuses the rest), and the owner may answer it later, even
# once the pack is with the committee. Every row is a self-loop: the run never
# moves on a question, the analyst's clock is not paused (``round_open`` is the
# D-16 round's alone), and the work is the step the trigger enqueues. Only in
# REVIEW, with no agent running, does an answer that changed a value re-run
# research and assessment on the changed fields (the step decides, from the run
# it reads; ``AGENT_DONE_RERUN_BOTH`` then returns the run to REVIEW as for the
# analyst's own re-run). Elsewhere the values are recorded and the analyst is
# told they changed since the agents ran, so no answer is lost and no re-run
# races a running agent.
ADHOC_QUESTION_STATES: tuple[CaseState, ...] = (
    S.TRIAGE,
    S.SCREENING,
    S.ASSESS,
    S.CRITIC,
    S.COMPLETENESS,
    S.CLARIFICATION,
    S.REVIEW,
    S.RECOMMEND,
    S.DECIDE,
    S.HANDOFF,
)

_ADHOC_QUESTIONS: list[Transition] = [
    *(
        Transition(
            state,
            Trigger.CASE_QUESTION_ASKED,
            state,
            "record question.asked; the analyst clock keeps running (ADR-73)",
        )
        for state in ADHOC_QUESTION_STATES
    ),
    *(
        Transition(
            state,
            Trigger.CASE_QUESTION_ANSWERED,
            state,
            "record question.answered; re-run research+assessment on the changed "
            "fields when no agent is running (ADR-73)"
            if state is S.REVIEW
            else "record question.answered; flag the values that changed since "
            "the agents ran (ADR-73)",
        )
        for state in ADHOC_QUESTION_STATES
    ),
    # A document the owner adds with an ad-hoc answer is stored, recorded and
    # extracted at whatever stage it arrives (INTAKE and CLARIFICATION have their
    # own rows above); no agent reads it until one runs again.
    *(
        Transition(
            state,
            trigger,
            state,
            "the owner added a document with an answer: record it and hand it to "
            "H3; a re-run in REVIEW waits for it (ADR-73)",
        )
        for state in ADHOC_QUESTION_STATES
        if state is not S.CLARIFICATION
        for trigger in (
            Trigger.CASE_DOCUMENT_STORED,
            Trigger.INGEST_DONE,
            Trigger.INGEST_FAILED,
        )
    ),
]

TRANSITIONS: list[Transition] = [
    *_INTAKE,
    *_TRIAGE_SCREENING,
    *_ASSESS_CRITIC_COMPLETENESS,
    *_CLARIFICATION,
    *_REVIEW,
    *_RECOMMEND,
    *_RECOMMEND_DELEGATED,
    *_DECIDE,
    *_CLOSED_HANDOFF,
    *_ADHOC_QUESTIONS,
]

# Any state, on agent.failed after its one retry, goes to HANDOFF — this
# applies uniformly rather than being listed once per state (design A
# §3.1's "any" row).
_HANDOFF_FROM_ANY: list[Transition] = [
    Transition(
        state,
        Trigger.AGENT_FAILED_AFTER_RETRY,
        S.HANDOFF,
        "open a handoff gate with the raw inputs; record agent.failed "
        "and human.handoff",
    )
    for state in CaseState
    if state not in (S.CLOSED, S.HANDOFF)
]

# A fan-out (one intake job per document) can fail more than once: the second
# failure finds the run already in HANDOFF. It stays there, records its own
# agent.failed and opens its own handoff gate (each failed job has its own raw
# inputs for a human to complete); nothing is dead-lettered and the run does
# not freeze.
_HANDOFF_AGAIN = Transition(
    S.HANDOFF,
    Trigger.AGENT_FAILED_AFTER_RETRY,
    S.HANDOFF,
    "another agent failed while a handoff is open: record agent.failed and "
    "open a handoff gate for its raw inputs",
)

TRANSITIONS.extend(_HANDOFF_FROM_ANY)
TRANSITIONS.append(_HANDOFF_AGAIN)


def apply(
    state: CaseState, trigger: Trigger, ctx: TransitionContext
) -> TransitionResult:
    """Find the first transition matching ``(state, trigger)`` whose guard
    passes, in table order, and return the result. Raises
    :class:`~fcrm_caseflow.exceptions.IllegalTransition` if none match —
    the caller (the real runtime) is responsible for acking the triggering
    message and writing an error activity rather than retrying blindly.
    """
    for transition in TRANSITIONS:
        if transition.from_state != state or transition.trigger != trigger:
            continue
        if transition.guard(ctx):
            to_state = transition.to_state
            if trigger is Trigger.GATE_DECIDED_HANDOFF:
                to_state = _resume_target(state, trigger, ctx, to_state)
            return TransitionResult(
                from_state=state,
                to_state=to_state,
                action=transition.action,
                trigger=trigger,
            )
    raise IllegalTransition(state, trigger)


def _resume_target(
    state: CaseState, trigger: Trigger, ctx: TransitionContext, default: CaseState
) -> CaseState:
    """Where a run leaving HANDOFF goes: ``ctx.resume_state`` (the state it was
    suspended from) when the runtime knows it, else the table's ``default``. A
    run never resumes into HANDOFF or CLOSED.

    Raises:
        IllegalTransition: ``ctx.resume_state`` is HANDOFF or CLOSED.
    """
    if ctx.resume_state is None:
        return default
    target = CaseState(ctx.resume_state)
    if target in (S.HANDOFF, S.CLOSED):
        raise IllegalTransition(state, trigger)
    return target
