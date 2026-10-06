"""H1's oversight core, the examiner half and the re-flag acknowledgement (scene
20; ADR-28). The watchlist half is :mod:`fcrm_caseflow.cores.oversight_watchlist`;
both register their handlers in :data:`HANDLERS`.

**The examiner answers from the audit chain and nothing else.** One step kind,
``examiner.answer``, does the work in its two ends:

* ``examiner.asked`` (S4 stored a question): record it, and dispatch one
  ``agent.run examiner`` whose only inputs are the question and the case. The
  job's id is derived from the question, and the run's ``refs`` remember which
  question it answers.
* ``agent.done examiner``: **check before believing.** The chain is verified by
  code (a chain that does not verify is never answered from), every sentence must
  cite at least one record, and every cited ``seq`` must exist in the chain. Any
  failure turns the *whole* answer into a refusal; a dropped or unknown
  reference never becomes a fabricated citation. The checked answer is posted to
  S4 (which waits for it), ``examiner.answered`` is appended to the chain and the
  analyst's stream shows it.
* ``agent.failed examiner``: a refusal that says the examiner could not answer.

The examiner never decides anything and never writes a score: its answer is
shown to whoever asked, and logged.

``reflag.ack`` records an analyst's acknowledgement of a watchlist re-flag on the
re-flagged case's own chain. It changes no score and reopens nothing (ADR-07).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Optional

from fcrm_common.errors import ConflictError, NotFoundError
from fcrm_common.ids import deterministic_id
from fcrm_contracts.api.audit_oversight import (
    ANSWER_MAX_SENTENCES,
    ExaminerAnswerIn,
    ExaminerSentenceOut,
)
from fcrm_contracts.enums import AgentName, StepKind
from fcrm_contracts.events.payloads import ExaminerAsked, GateDecided, HarnessStep
from fcrm_contracts.proposals.examiner import ExaminerAnswer
from fcrm_contracts.versions import PinnedVersions
from pydantic import ValidationError

from fcrm_caseflow.cores.intake_records import short
from fcrm_caseflow.cores.oversight_watchlist import HANDLERS as WATCHLIST_HANDLERS
from fcrm_caseflow.cores.shared import idem, show, step_payload, tokens_of
from fcrm_caseflow.dispatch import AgentDispatch
from fcrm_caseflow.oversight_runs import OVERSIGHT_REF
from fcrm_caseflow.steps import (
    AgentJobSpec,
    StepDeps,
    StepHandler,
    StepResult,
    UnknownStepKindError,
)
from fcrm_caseflow.triggers import Trigger

logger = logging.getLogger(__name__)

#: ``ExaminerSentenceOut.t`` is capped; a longer sentence is refused, not cut.
SENTENCE_MAX_CHARS: Final = 640
#: What a question looks like in the chain's record of it.
QUESTION_RECORD_CHARS: Final = 300

REFUSAL_NO_ANSWER: Final = "the records of this case do not hold the answer"
REFUSAL_UNKNOWN_RECORD: Final = "the answer cited a record that is not in the chain"
REFUSAL_UNCITED: Final = "an answer sentence cited no record"
REFUSAL_TOO_LONG: Final = "an answer sentence was longer than the answer allows"
REFUSAL_CHAIN: Final = "the audit chain of this case does not verify"
REFUSAL_FAILED: Final = "the examiner could not produce an answer"


@dataclass(frozen=True)
class CheckedAnswer:
    """The examiner's output after H1's checks.

    Attributes:
        sentences: The sentences that stand (empty for a refusal).
        refusal_reason: Why the answer is a refusal, else ``None``.
    """

    sentences: tuple[ExaminerSentenceOut, ...]
    refusal_reason: Optional[str] = None

    @property
    def refused(self) -> bool:
        """Whether the answer is a refusal."""
        return self.refusal_reason is not None


def job_ref(job_id: str) -> str:
    """The ``CaseRun.refs`` key that remembers which question a job answers."""
    return f"examiner_job_{job_id}"


def _refusal(reason: str) -> CheckedAnswer:
    return CheckedAnswer(sentences=(), refusal_reason=reason)


def check_answer(accepted: Sequence[Mapping[str, Any]], records: int) -> CheckedAnswer:
    """The examiner's kept output, held to what the chain holds.

    Args:
        accepted: ``AgentDone.output["accepted"]``: the proposals Guard 4 kept.
        records: How many records the verified chain has; a citation is a
            ``seq`` from 1 to this number.

    Returns:
        The answer, or a refusal: no sentence at all, a sentence that cites
        nothing or something the chain does not hold, or one too long to show.
        A refusal never carries a citation.
    """
    sentences: list[ExaminerSentenceOut] = []
    for raw in accepted:
        try:
            proposal = ExaminerAnswer.model_validate(raw)
        except ValidationError:
            logger.warning("the examiner's output is not an ExaminerAnswer")
            return _refusal(REFUSAL_NO_ANSWER)
        for sentence in proposal.sentences:
            refs = list(dict.fromkeys(sentence.record_refs))
            if not refs:
                return _refusal(REFUSAL_UNCITED)
            unknown = [ref for ref in refs if not 1 <= ref <= records]
            if unknown:
                logger.warning(
                    "the examiner cited records the chain does not hold",
                    extra={"unknown": unknown, "records": records},
                )
                return _refusal(REFUSAL_UNKNOWN_RECORD)
            text = sentence.text.strip()
            if not text or len(text) > SENTENCE_MAX_CHARS:
                return _refusal(REFUSAL_TOO_LONG)
            sentences.append(ExaminerSentenceOut(t=text, refs=refs))
    if not sentences:
        return _refusal(REFUSAL_NO_ANSWER)
    return CheckedAnswer(sentences=tuple(sentences[:ANSWER_MAX_SENTENCES]))


async def _pins(step: HarnessStep, deps: StepDeps) -> Optional[PinnedVersions]:
    """The versions the examiner job is pinned to: a real run's own (``None``,
    so the engine uses them), or, for an oversight run opened for a case H1 never
    ran, the versions S1 holds for that case.

    Raises:
        fcrm_common.errors.NotFoundError: S1 does not hold the case.
    """
    if not deps.run.refs.get(OVERSIGHT_REF):
        return None
    record = await deps.require_case().get_record(step.case_id)
    return record.pins


async def _post(
    step: HarnessStep,
    deps: StepDeps,
    question_id: str,
    checked: CheckedAnswer,
    references: list[str],
    *,
    announce: bool = True,
) -> None:
    """Hand the answer to S4, record it on the chain and, when ``announce``, show
    it on the case's stream (S1 must hold the case for that).
    """
    clients = deps.require_oversight()
    await clients.examiner.post_answer(
        ExaminerAnswerIn(
            question_id=question_id,
            case_id=step.case_id,
            run_id=step.run_id,
            refused=checked.refused,
            sentences=list(checked.sentences),
            references=references,
            refusal_reason=checked.refusal_reason,
        ),
        idempotency_key=idem(step, "answer", question_id),
    )
    outcome = "refused" if checked.refused else f"{len(checked.sentences)} sentences"
    summary = f"Examiner answered ({outcome}) · question {question_id}"
    data = {
        "question_id": question_id,
        "refused": checked.refused,
        "refusal_reason": checked.refusal_reason,
        "cited": sorted({ref for s in checked.sentences for ref in s.refs}),
    }
    record = await deps.append_record(
        step,
        "examiner.answered",
        summary,
        data,
        actor="system" if checked.refused else "ai",
    )
    if announce:
        await show(
            deps,
            step,
            "examiner.answered",
            "analyst",
            summary,
            {"question_id": question_id, "refused": checked.refused},
            record_seq=record.seq,
        )


async def _ask(step: HarnessStep, deps: StepDeps) -> StepResult:
    """``examiner.asked``: record the question and dispatch the examiner."""
    asked = step_payload(step, ExaminerAsked)
    try:
        pins = await _pins(step, deps)
    except NotFoundError:
        logger.warning(
            "the examiner was asked about a case S1 does not hold",
            extra={"case_id": step.case_id, "question_id": asked.question_id},
        )
        await _post(
            step,
            deps,
            asked.question_id,
            _refusal(REFUSAL_NO_ANSWER),
            [],
            announce=False,
        )
        return StepResult()
    await deps.append_record(
        step,
        "examiner.asked",
        f"Examiner asked by {asked.asked_by}: {short(asked.question)}",
        {
            "question_id": asked.question_id,
            "question": asked.question[:QUESTION_RECORD_CHARS],
        },
        actor=asked.asked_by,
    )
    job_id = deterministic_id("job", step.run_id, "examiner", asked.question_id)
    return StepResult(
        agent_jobs_to_dispatch=(
            AgentJobSpec(
                dispatch=AgentDispatch(
                    AgentName.EXAMINER,
                    step_key=f"examiner:{asked.question_id}",
                    job_id=job_id,
                ),
                refs={"question": asked.question},
                pins=pins,
            ),
        ),
        refs_patch={
            job_ref(job_id): {
                "question_id": asked.question_id,
                "asked_by": asked.asked_by,
            }
        },
    )


def _question_of(step: HarnessStep, deps: StepDeps) -> tuple[str, str]:
    """``(job id, question id)`` of the job a result is for.

    Raises:
        fcrm_common.errors.ConflictError: The result names no job this run
            dispatched for a question.
    """
    payload = step.args.get("payload")
    job_id = payload.get("job_id") if isinstance(payload, Mapping) else None
    remembered = deps.run.refs.get(job_ref(str(job_id)))
    if not isinstance(job_id, str) or not isinstance(remembered, Mapping):
        raise ConflictError(
            "an examiner result names no question this run dispatched",
            step_id=step.step_id,
        )
    return job_id, str(remembered["question_id"])


async def _answer(step: HarnessStep, deps: StepDeps) -> StepResult:
    """``agent.done examiner``: verify the chain, check the citations, answer."""
    job_id, question_id = _question_of(step, deps)
    results = await deps.require_agent_results().list_for(
        step.run_id, AgentName.EXAMINER
    )
    result = next((r for r in results if r.job_id == job_id), None)
    if result is None:
        raise NotFoundError(f"no examiner result is stored for job {job_id}")
    verified = await deps.audit.verify(step.case_id)
    references: list[str] = []
    if verified.ok:
        checked = check_answer(result.accepted(), verified.record_count)
        if not checked.refused:
            references = [
                f"audit chain of {step.case_id}: {verified.record_count} records, "
                "every hash intact"
            ]
    else:
        logger.error(
            "the audit chain does not verify; the examiner's answer is refused",
            extra={
                "case_id": step.case_id,
                "question_id": question_id,
                "first_break_seq": verified.first_break_seq,
            },
        )
        checked = _refusal(REFUSAL_CHAIN)
    await _post(step, deps, question_id, checked, references)
    return StepResult(
        counter_deltas={"agents_run": 1, "tokens_used": tokens_of([result])}
    )


async def _failed(step: HarnessStep, deps: StepDeps) -> StepResult:
    """``agent.failed examiner``: the asker is told the examiner could not answer."""
    _job_id, question_id = _question_of(step, deps)
    payload = step.args.get("payload")
    reason = payload.get("reason") if isinstance(payload, Mapping) else None
    code = str(reason).split(":", 1)[0] if reason else "unknown"
    logger.error(
        "the examiner failed",
        extra={"case_id": step.case_id, "question_id": question_id, "reason": code},
    )
    await _post(
        step, deps, question_id, _refusal(f"{REFUSAL_FAILED} ({code})"[:240]), []
    )
    return StepResult()


async def examiner_answer(step: HarnessStep, deps: StepDeps) -> StepResult:
    """``examiner.answer``: the question, the answer or the failure, by trigger.

    Raises:
        fcrm_caseflow.steps.UnknownStepKindError: The step names a trigger this
            handler does not own.
        fcrm_common.errors.FcrmError: A peer refused or was unreachable (the
            step is retried, then parked).
    """
    trigger = step.args.get("trigger")
    if trigger == Trigger.EXAMINER_ASKED.value:
        return await _ask(step, deps)
    if trigger == Trigger.AGENT_DONE_EXAMINER.value:
        return await _answer(step, deps)
    if trigger == Trigger.AGENT_FAILED_EXAMINER.value:
        return await _failed(step, deps)
    raise UnknownStepKindError(
        f"examiner.answer cannot be started by {trigger!r}", step=step.step.value
    )


async def reflag_ack(step: HarnessStep, deps: StepDeps) -> StepResult:
    """``gate.decided reflag_ack``: record the acknowledgement on the re-flagged
    case's own chain. Nothing else changes: the case's score, stage and lock are
    untouched (a re-flag asks a human to look; ADR-07).

    Raises:
        fcrm_common.errors.ValidationError: The payload is not a decision.
        fcrm_common.errors.FcrmError: S4 refused or was unreachable.
    """
    decided = step_payload(step, GateDecided)
    value = decided.value if isinstance(decided.value, Mapping) else {}
    reflag_id = decided.subject.get("id", "")
    await deps.append_record(
        step,
        "reflag.acknowledged",
        f"Re-flag acknowledged by {decided.decided_by}"
        f" · {value.get('jurisdiction', 'jurisdiction')}"
        f" · {short(decided.reason)}",
        {
            "gate_id": decided.gate_id,
            "reflag_id": reflag_id,
            "decision": decided.decision,
            "reason": decided.reason,
            "rerun_requested": bool(value.get("rerun", False)),
        },
        actor=decided.decided_by,
        case_id=decided.case_id,
    )
    return StepResult()


#: The oversight core's handlers by step kind (``cores.default_handlers``).
HANDLERS: Final[Mapping[StepKind, StepHandler]] = {
    StepKind.EXAMINER_ANSWER: examiner_answer,
    StepKind.REFLAG_ACK: reflag_ack,
    **WATCHLIST_HANDLERS,
}

__all__ = [
    "HANDLERS",
    "CheckedAnswer",
    "check_answer",
    "examiner_answer",
    "job_ref",
    "reflag_ack",
]
