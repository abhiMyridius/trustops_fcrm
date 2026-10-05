"""H1's decision core: the harness steps of scenes 17-18 (ADR-28, ADR-31, ADR-36).

Committee members vote in S5, where the votes stay hidden until the third; H1
records *that* each vote happened, answers the committee's questions' side
effects, and, when S5 reports a tally, turns it into a recorded outcome:

* ``vote.record`` writes ``vote.cast`` without the option (the event does not
  carry it, and :class:`~fcrm_contracts.events.payloads.VoteCastPayload`
  refuses a payload that does).
* ``committee.question`` and ``committee.answer`` record the thread; an answer
  that changes numbers drives R6 (unlock, supersede, every cast vote
  re-requested), one that does not is an addendum.
* ``decide.outcome`` reveals the votes, cross-checks S5's tally against the
  same pure :func:`fcrm_contracts.voting.tally` S5 used, asks S5 to record the
  tallied outcome, and has S1 activate the conditions and close the case.
* ``decide.no_majority`` confirms the re-vote round S5 already opened (ADR-31:
  a 1-1-1 split is never an outcome, never ``defer``).
* ``decide.delegated`` closes a Band 1-2 case under delegated authority (R1) after
  Guard 5.

**Nothing here decides.** The outcome is whatever S5 tallied from people's
votes (or an analyst's delegated close with a reason); H1 records it and checks
its own arithmetic. **Safe to run twice:** every S5 write carries an
``Idempotency-Key``, S1's ``PUT outcome`` is the whole intended state, and
audit records are keyed by the step (:mod:`fcrm_caseflow.steps`).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal, Optional

from fcrm_common.errors import (
    ConflictError,
    GateRequiredError,
    NotFoundError,
    RuleViolationError,
)
from fcrm_common.errors import ValidationError as FcrmValidationError
from fcrm_common.ids import deterministic_id
from fcrm_common.time import utcnow
from fcrm_contracts.api.case_internal import CaseRecordInternal, StatePatch
from fcrm_contracts.api.case_internal_outcome import (
    ActivatedCondition,
    OutcomePut,
    outcome_label,
)
from fcrm_contracts.api.decision import OutcomeCondition, OutcomeRecord, PackView
from fcrm_contracts.enums import CaseState, Outcome, Role, StepKind
from fcrm_contracts.events.payloads import (
    CaseCloseRequested,
    CommitteeAnswered,
    CommitteeQuestionAsked,
    HarnessStep,
    VoteCastPayload,
    VoteNoMajority,
    VoteTallied,
)
from fcrm_contracts.voting import tally
from fcrm_guardrails.rules import RulesContext, check_all
from fcrm_guardrails.rules.r3_po_no_restricted import restricted_terms_in
from pydantic import ValidationError

from fcrm_caseflow.cores.closing_conditions import (
    activated_conditions,
    outcome_conditions,
)
from fcrm_caseflow.cores.intake_records import plural
from fcrm_caseflow.cores.step_support import payload_of, show
from fcrm_caseflow.steps import StepDeps, StepHandler, StepResult

logger = logging.getLogger(__name__)

#: The committee's decision is "the committee" in the record; the people behind
#: it are the revealed votes, kept in the ``vote.revealed`` record.
COMMITTEE_DECIDER: Final = "committee"

#: A delegated close is for Band 1-2 (R1).
DELEGATED_BANDS: Final = (1, 2)

#: What the PO is told, per outcome, in the business's words.
_COMMITTEE_SENTENCE: Final[Mapping[Outcome, str]] = {
    Outcome.APPROVE: "The committee approved this change.",
    Outcome.APPROVE_WITH_CONDITIONS: (
        "The committee approved this change with conditions."
    ),
    Outcome.DEFER: "The committee deferred this change.",
    Outcome.REJECT: "The committee did not approve this change.",
}

#: The longest business summary appended to the PO's sentence.
_MAX_SUMMARY_CHARS: Final = 1200


# --- small helpers -------------------------------------------------------------


def counts_text(counts: Mapping[str, int]) -> str:
    """``"2 approve with conditions · 1 defer"`` from ``{"approve_with_conditions":
    2, "defer": 1}``, options in the order given.
    """
    return " · ".join(
        f"{count} {option.replace('_', ' ')}" for option, count in counts.items()
    )


def committee_plain_summary(
    outcome: Outcome, business_summary: Optional[str], conditions: int
) -> str:
    """What the product owner reads about the committee's outcome: one plain
    sentence, how many conditions apply, and the pack's own business summary
    (already scanned by Guard 4 when it was written) unless it now names a
    restricted term, in which case it is left out and the sentence stands alone.
    """
    parts = [_COMMITTEE_SENTENCE[outcome]]
    if conditions:
        parts.append(
            f"{plural(conditions, 'condition')} now apply and "
            f"{'are' if conditions != 1 else 'is'} tracked on your case."
        )
    summary = (business_summary or "").strip()
    if summary and not restricted_terms_in(summary):
        parts.append(summary[:_MAX_SUMMARY_CHARS])
    elif summary:
        logger.warning("the pack's business summary names a restricted term; omitted")
    return " ".join(parts)


def check_tally(tallied: VoteTallied, step: HarnessStep) -> Outcome:
    """The outcome ``tallied`` carries, confirmed by tallying its revealed votes
    again with the same pure function S5 used (ADR-36).

    Raises:
        fcrm_common.errors.ConflictError: The votes do not tally to the outcome
            S5 reported, or are not a full round: nothing is recorded.
    """
    reported = tallied.vote_outcome
    again = tally([vote.vote.value for vote in tallied.votes])
    if reported is None or again.outcome is None or again.outcome != reported:
        logger.error(
            "H1 and S5 disagree on the tally",
            extra={"step_id": step.step_id, "pack_id": tallied.pack_id},
        )
        raise ConflictError(
            "the revealed votes do not tally to the outcome S5 reported",
            pack_id=tallied.pack_id,
            round=tallied.round,
        )
    return again.outcome


@dataclass(frozen=True)
class Decision:
    """What is being recorded: a committee outcome or a delegated close.

    Attributes:
        mode: ``committee`` or ``delegated``.
        outcome: What was decided.
        decided_by: ``committee``, or the analyst's user id.
        plain_summary: What the product owner reads (business words).
        pack_id: The tallied pack (committee mode only).
        reason: The analyst's reason (delegated mode only).
    """

    mode: Literal["committee", "delegated"]
    outcome: Outcome
    decided_by: str
    plain_summary: str
    pack_id: Optional[str] = None
    reason: Optional[str] = None


def outcome_record(
    case_id: str, decision: Decision, conditions: Sequence[OutcomeCondition]
) -> OutcomeRecord:
    """The :class:`OutcomeRecord` S5 is asked to record, or the step's own
    validation error.

    Raises:
        fcrm_common.errors.ValidationError: The fields break the contract (a
            delegated reason that is too short): a step is retried, then parked,
            rather than crashing on a bare pydantic error.
    """
    try:
        return OutcomeRecord(
            case_id=case_id,
            mode=decision.mode,
            outcome=decision.outcome,
            pack_id=decision.pack_id,
            decided_by=decision.decided_by,
            reason=decision.reason,
            plain_summary=decision.plain_summary,
            conditions=list(conditions),
        )
    except ValidationError as exc:
        raise FcrmValidationError(
            "the outcome to record breaks its contract",
            case_id=case_id,
            errors_count=exc.error_count(),
        ) from exc


def _outcome_put(
    case_id: str, decision: Decision, activated: Sequence[ActivatedCondition]
) -> OutcomePut:
    """S1's whole-state body for what S5 recorded.

    Raises:
        fcrm_common.errors.ValidationError: The body breaks the contract
            (an empty owner, a condition listed twice): the pack is wrong, not
            this call.
    """
    try:
        return OutcomePut(
            outcome=decision.outcome,
            mode=decision.mode,
            decided_by=decision.decided_by,
            pack_id=decision.pack_id,
            reason=decision.reason,
            plain_summary=decision.plain_summary,
            conditions=list(activated),
        )
    except ValidationError as exc:
        raise FcrmValidationError(
            "the outcome and its conditions do not make a valid close",
            case_id=case_id,
            errors_count=exc.error_count(),
        ) from exc


def conditions_pack(outcome: Outcome, pack: Optional[PackView]) -> Optional[PackView]:
    """``pack`` when ``outcome`` activates its drafted conditions (an approval
    with conditions), else ``None``. An approval with conditions whose pack
    drafted none is logged: the committee voted for conditions nobody wrote.
    """
    if outcome is not Outcome.APPROVE_WITH_CONDITIONS or pack is None:
        return None
    if not pack.conditions:
        logger.warning(
            "approved with conditions but the pack drafted none",
            extra={"case_id": pack.case_id, "pack_id": pack.pack_id},
        )
    return pack


async def record_decision(
    step: HarnessStep,
    deps: StepDeps,
    decision: Decision,
    pack: Optional[PackView],
    case: CaseRecordInternal,
) -> StepResult:
    """Record ``decision`` in S5, then in S1 (which closes the case and
    activates the conditions the pack drafted), then in the audit trail: the
    common tail of a committee outcome and a delegated close.

    Each part is safe to repeat, so a step that fails between them is retried
    from the top. The conditions' due dates count from the date S5 holds for the
    decision, not from today: S5 stored it when it first recorded the decision,
    so every attempt sends S1 the same body.
    """
    case_id = step.case_id
    drafting = conditions_pack(decision.outcome, pack)
    record = outcome_record(
        case_id,
        decision,
        outcome_conditions(drafting, case) if drafting is not None else [],
    )
    recorded = await deps.require_decision_votes().record_outcome(
        record,
        idempotency_key=deterministic_id("idem", step.step_id, "outcome"),
    )
    decided_on = (recorded.decided_at or utcnow()).date()
    activated = (
        activated_conditions(case_id, drafting, case, decided_on)
        if drafting is not None
        else []
    )
    put = await deps.require_case().put_outcome(
        case_id,
        _outcome_put(case_id, decision, activated),
        idempotency_key=deterministic_id("idem", step.step_id, "outcome-case"),
    )
    label = outcome_label(decision.outcome)
    tail = f" · {plural(len(activated), 'condition')} activated" if activated else ""
    reason = f" · {decision.reason}" if decision.mode == "delegated" else ""
    await deps.append_record(
        step,
        "outcome.set",
        f"Outcome set: {label}{tail}{reason}",
        {
            "outcome": decision.outcome.value,
            "mode": decision.mode,
            "pack_id": decision.pack_id,
            "round": recorded.round,
            "decided_by": decision.decided_by,
            "reason": decision.reason,
            "conditions": [
                {"id": c.condition_id, "ref": c.ref, "owner": c.owner_label}
                for c in activated
            ],
        },
        actor=decision.decided_by if decision.mode == "delegated" else "system",
    )
    return StepResult(
        refs_patch={
            "outcome": decision.outcome.value,
            "outcome_mode": decision.mode,
            "outcome_pack_id": decision.pack_id,
            "conditions_activated": put.conditions_activated,
        }
    )


# --- scene 17: the committee votes and asks --------------------------------------


async def vote_record(step: HarnessStep, deps: StepDeps) -> StepResult:
    """``vote.cast``: record that a member voted (count only). The audit record,
    like the event it answers, never names the option, and a payload that does
    is refused before anything is written.
    """
    cast = payload_of(step, VoteCastPayload)
    await deps.append_record(
        step,
        "vote.cast",
        f"Vote {cast.cast_count} of {cast.quorum} cast",
        {
            "pack_id": cast.pack_id,
            "round": cast.round,
            "member_id": cast.member_id,
            "cast_count": cast.cast_count,
            "quorum": cast.quorum,
        },
        actor=cast.member_id,
    )
    return StepResult()


async def committee_question(step: HarnessStep, deps: StepDeps) -> StepResult:
    """``committee.question_asked``: record the question and show it to the
    analyst. No state changes; the committee's question is not a vote.
    """
    asked = payload_of(step, CommitteeQuestionAsked)
    audit = await deps.append_record(
        step,
        "committee.question",
        f"Committee question: {asked.text[:200]}",
        {
            "pack_id": asked.pack_id,
            "thread_id": asked.thread_id,
            "member_id": asked.member_id,
            "question": asked.text,
        },
        actor=asked.member_id,
    )
    await show(
        deps,
        step,
        "committee.question",
        "analyst",
        f"A committee member asked: {asked.text[:200]}",
        {"pack_id": asked.pack_id, "thread_id": asked.thread_id},
        record_seq=audit.seq,
    )
    return StepResult()


async def committee_answer(step: HarnessStep, deps: StepDeps) -> StepResult:
    """``committee.answered``: record the answer as the pack's addendum, or, when
    it changes numbers (R6), unlock the recommendation, supersede the pack and
    let S5 ask every member who had voted to vote again on the corrected one.
    """
    answered = payload_of(step, CommitteeAnswered)
    if answered.numbers_changed:
        return await _answer_with_changed_numbers(step, deps, answered)
    audit = await deps.append_record(
        step,
        "committee.answer",
        "Answer attached to the pack as an addendum · numbers unchanged",
        {
            "pack_id": answered.pack_id,
            "thread_id": answered.thread_id,
            "addendum_id": answered.addendum_id,
            "numbers_changed": False,
        },
        actor=answered.by,
    )
    await show(
        deps,
        step,
        "committee.answer",
        "analyst",
        "The question was answered and attached to the pack as an addendum",
        {"pack_id": answered.pack_id, "thread_id": answered.thread_id},
        record_seq=audit.seq,
    )
    return StepResult()


async def _answer_with_changed_numbers(
    step: HarnessStep, deps: StepDeps, answered: CommitteeAnswered
) -> StepResult:
    """R6, in order: record the answer, unlock the case in S1 (back to review,
    ``locked`` off), supersede the pack in S5 with the analyst's reason (its cast
    votes are voided and re-requested for the corrected pack), then say so. The
    assessment core's lock/build/release steps run again once the analyst
    corrects and locks.
    """
    reason = answered.unlock_reason or ""
    await deps.append_record(
        step,
        "committee.answer",
        "Answer changes the numbers · the recommendation is unlocked (R6)",
        {
            "pack_id": answered.pack_id,
            "thread_id": answered.thread_id,
            "addendum_id": answered.addendum_id,
            "numbers_changed": True,
            "unlock_reason": reason,
        },
        actor=answered.by,
    )
    await deps.require_case().patch_state(
        step.case_id,
        StatePatch(stage=CaseState.REVIEW, run_id=step.run_id, locked=False),
    )
    await deps.append_record(
        step,
        "case.unlocked",
        f"Recommendation unlocked after a committee answer · {reason}",
        {"unlock_reason": reason, "source": "committee.answered"},
        actor=answered.by,
    )
    superseded = await deps.require_decision_votes().supersede(
        answered.pack_id,
        reason,
        idempotency_key=deterministic_id("idem", step.step_id, "supersede"),
    )
    await deps.append_record(
        step,
        "pack.superseded",
        f"Pack v{superseded.version} superseded · "
        f"{plural(superseded.voided_votes, 'cast vote')} voided (kept)",
        {
            "pack_id": superseded.pack_id,
            "version": superseded.version,
            "voided_votes": superseded.voided_votes,
            "reason": reason,
        },
        actor="system",
    )
    if superseded.rerequested:
        await deps.append_record(
            step,
            "vote.rerequested",
            f"{plural(len(superseded.rerequested), 'member')} asked to vote again "
            "on the corrected pack",
            {"pack_id": superseded.pack_id, "member_ids": superseded.rerequested},
            actor="system",
        )
    await show(
        deps,
        step,
        "pack.superseded",
        "analyst",
        f"Pack v{superseded.version} was superseded: correct the numbers and lock "
        f"again. {plural(superseded.voided_votes, 'vote')} will be asked for again.",
        {"pack_id": superseded.pack_id, "voided_votes": superseded.voided_votes},
    )
    return StepResult(refs_patch={"superseded_pack_id": superseded.pack_id})


# --- scene 18: the tally, the outcome, the conditions ----------------------------


async def decide_outcome(step: HarnessStep, deps: StepDeps) -> StepResult:
    """``vote.tallied``: reveal the votes, confirm the tally, record the
    committee's outcome in S5 and S1 (which activates the conditions and closes
    the case) and write ``vote.revealed`` and ``outcome.set``.

    Raises:
        fcrm_common.errors.ConflictError: The votes do not tally to what S5
            reported.
        fcrm_caseflow.clients.decision_votes_client.DecisionRefusedError: S5
            refused the outcome (``not_tallied``, ``outcome_mismatch``...).
    """
    tallied = payload_of(step, VoteTallied)
    outcome = check_tally(tallied, step)
    await deps.append_record(
        step,
        "vote.revealed",
        f"Votes revealed · {counts_text(tallied.counts)}",
        {
            "pack_id": tallied.pack_id,
            "round": tallied.round,
            "counts": dict(tallied.counts),
            "outcome": outcome.value,
            "votes": [
                {
                    "member_id": vote.member_id,
                    "vote": vote.vote.value,
                    "rationale": vote.rationale,
                }
                for vote in tallied.votes
            ],
        },
        actor="system",
    )
    pack = await deps.require_decision_votes().get_pack(tallied.pack_id)
    case = await deps.require_case().get_record(step.case_id)
    drafted = len(pack.conditions) if outcome is Outcome.APPROVE_WITH_CONDITIONS else 0
    return await record_decision(
        step,
        deps,
        Decision(
            mode="committee",
            outcome=outcome,
            pack_id=tallied.pack_id,
            decided_by=COMMITTEE_DECIDER,
            plain_summary=committee_plain_summary(
                outcome, pack.business_summary, drafted
            ),
        ),
        pack,
        case,
    )


async def decide_no_majority(step: HarnessStep, deps: StepDeps) -> StepResult:
    """``vote.no_majority``: record the split and confirm the next round, which
    S5 opened in the same transaction that tallied it. The run stays in DECIDE
    and no outcome exists: a 1-1-1 split is never ``defer`` (ADR-08, ADR-31).
    """
    split = payload_of(step, VoteNoMajority)
    await deps.append_record(
        step,
        "vote.no_majority",
        f"No majority in round {split.round} ({counts_text(split.counts)}) "
        "· re-vote requested",
        {"pack_id": split.pack_id, "round": split.round, "counts": dict(split.counts)},
        actor="system",
    )
    revote = await deps.require_decision_votes().confirm_revote(
        split.pack_id,
        idempotency_key=deterministic_id("idem", step.step_id, "revote"),
    )
    await deps.append_record(
        step,
        "vote.rerequested",
        f"Round {revote.round}: {plural(len(revote.member_ids), 'member')} asked to "
        "vote again",
        {
            "pack_id": revote.pack_id,
            "round": revote.round,
            "member_ids": revote.member_ids,
        },
        actor="system",
    )
    await show(
        deps,
        step,
        "vote.no_majority",
        "analyst",
        f"Round {split.round} ended without a majority; round {revote.round} is "
        "open. The committee may discuss in the thread and vote again.",
        {"pack_id": split.pack_id, "round": revote.round},
    )
    return StepResult()


# --- R1: the delegated close -----------------------------------------------------


def _guard_delegated(
    request: CaseCloseRequested, deps: StepDeps, step: HarnessStep
) -> int:
    """Guard 5 for a delegated close, before anything is asked of S5: the band
    routes to the analyst (R1), the recommendation is locked, the reason is
    written and the eight rules keep (closed is untouched, the analyst is not
    editing a submission...). Returns the band.

    Raises:
        fcrm_common.errors.RuleViolationError: R1 or one of the eight rules.
        fcrm_common.errors.GateRequiredError: The recommendation is not locked.
    """
    band = request.band if request.band is not None else deps.run.band
    if band not in DELEGATED_BANDS:
        raise RuleViolationError(
            "a delegated close is only for a case the analyst may close (R1); "
            "this one goes to the committee",
            rule_id="R1",
            case_id=step.case_id,
        )
    if not deps.run.snapshot.flags.locked:
        raise GateRequiredError(
            "the recommendation must be locked before a delegated close",
            gate_type="lock",
            case_id=step.case_id,
        )
    results = check_all(
        RulesContext(
            actor_role=Role.ANALYST,
            action="delegated_close",
            case_state=deps.run.state,
        )
    )
    broken = [r.rule_id for r in results if not r.ok]
    if broken:
        raise RuleViolationError(
            f"Guard 5 refused the delegated close: {', '.join(broken)}",
            rule_id=broken[0],
            case_id=step.case_id,
        )
    return band


async def _latest_pack(step: HarnessStep, deps: StepDeps) -> Optional[PackView]:
    """The case's latest pack (it drafted the conditions of a delegated approval
    with conditions), or ``None`` when the case has no pack (S5 answers 404).
    """
    try:
        return await deps.require_decision_votes().latest_pack(step.case_id)
    except NotFoundError:
        logger.warning(
            "no pack to take conditions from",
            extra={"case_id": step.case_id, "step_id": step.step_id},
        )
        return None


async def decide_delegated(step: HarnessStep, deps: StepDeps) -> StepResult:
    """``case.close_requested``: Guard 5, then S5 records the delegated outcome
    (the analyst and their reason), S1 closes the case and the step writes
    ``outcome.set``. S5 then publishes ``outcome.set`` (``delegated``), which
    moves the run to CLOSED.

    Raises:
        fcrm_common.errors.RuleViolationError: R1 or Guard 5 refused.
        fcrm_common.errors.GateRequiredError: Not locked.
        fcrm_caseflow.clients.decision_votes_client.DecisionRefusedError: S5
            refused (``committee_pending``, ``case_decided``).
    """
    request = payload_of(step, CaseCloseRequested)
    _guard_delegated(request, deps, step)
    reason = (request.reason or "").strip()
    summary = (request.plain_summary or "").strip()
    if not summary:
        raise FcrmValidationError(
            "a delegated close needs a plain summary for the owner",
            step_id=step.step_id,
        )
    wants_conditions = request.outcome is Outcome.APPROVE_WITH_CONDITIONS
    pack = await _latest_pack(step, deps) if wants_conditions else None
    case = await deps.require_case().get_record(step.case_id)
    return await record_decision(
        step,
        deps,
        Decision(
            mode="delegated",
            outcome=request.outcome,
            decided_by=request.actor,
            reason=reason,
            plain_summary=summary,
        ),
        pack,
        case,
    )


#: The decision core's handlers by step kind (``fcrm_caseflow.cores.default_handlers``).
HANDLERS: Final[Mapping[StepKind, StepHandler]] = {
    StepKind.VOTE_RECORD: vote_record,
    StepKind.COMMITTEE_QUESTION: committee_question,
    StepKind.COMMITTEE_ANSWER: committee_answer,
    StepKind.DECIDE_OUTCOME: decide_outcome,
    StepKind.DECIDE_NO_MAJORITY: decide_no_majority,
    StepKind.DECIDE_DELEGATED: decide_delegated,
}

__all__ = ["HANDLERS"]
