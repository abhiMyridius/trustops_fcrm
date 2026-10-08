"""H1's steps for the analyst's ad-hoc questions to the owner (ADR-73).

* ``question.asked`` (``case.question_asked``): record that the analyst asked the
  owner questions outside the clarification round. Nothing else happens: no gate
  is decided and the analyst's clock keeps running (``round_open`` belongs to the
  D-16 round alone).
* ``question.answered`` (``case.question_answered``): record the owner's answer.
  When it changed a value, the agents are re-run on the changed fields only if
  the run is in REVIEW and no agent is running (the same re-run as the round's,
  :func:`~fcrm_caseflow.cores.assessment_review.dispatch_rerun`, which waits for
  any document the answer added); ``AGENT_DONE_RERUN_BOTH`` then publishes it and
  the run stays in REVIEW. At any other stage, or while agents run, the values
  are recorded (S1 stored them and froze a submission version) and the analyst
  is told they changed since the agents ran; no re-run is started beside a
  running one, and no answer is lost.

**Safe to run twice**: the records carry idempotency keys derived from the step
id (``StepDeps.append_record``), the stream event is keyed the same way, and a
re-run's jobs are derived from the step id.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, Final

from fcrm_contracts.enums import CaseState, StepKind
from fcrm_contracts.events.payloads import (
    CaseQuestionAnswered,
    CaseQuestionAsked,
    HarnessStep,
)

from fcrm_caseflow.cores.assessment_review import dispatch_rerun
from fcrm_caseflow.cores.intake_records import plural
from fcrm_caseflow.cores.refs import RERUN_ACTIVE_REF, RERUN_PENDING_REF
from fcrm_caseflow.cores.shared import show, step_payload
from fcrm_caseflow.steps import StepDeps, StepHandler, StepResult
from fcrm_caseflow.store import CaseRun

logger = logging.getLogger(__name__)

#: Stages before research and assessment first run: they read the values as they
#: stand when they do, so a change there needs no re-run.
BEFORE_ASSESSMENT: Final = frozenset({CaseState.TRIAGE, CaseState.SCREENING})


def agents_idle(run: CaseRun) -> bool:
    """Whether no research or assessment job of the run is in flight: no join is
    pending, no owner-answer re-run is running and none waits for a document.
    """
    refs = run.refs
    return (
        not run.pending_agents
        and not refs.get(RERUN_ACTIVE_REF)
        and not refs.get(RERUN_PENDING_REF)
    )


def reruns_now(run: CaseRun, changed: list[str]) -> bool:
    """Whether an answer that changed ``changed`` re-runs the agents now: only in
    REVIEW, only when a value changed, only when the agents are idle (ADR-73).
    """
    return bool(changed) and run.state is CaseState.REVIEW and agents_idle(run)


async def question_asked(step: HarnessStep, deps: StepDeps) -> StepResult:
    """``case.question_asked``: record the questions the analyst sent the owner
    outside the round. S1 already opened the threads and told both feeds.
    """
    asked = step_payload(step, CaseQuestionAsked)
    count = asked.question_count or len(asked.thread_ids)
    await deps.append_record(
        step,
        "question.asked",
        f"{plural(count, 'question')} sent to the owner outside the clarification "
        "round · analyst clock keeps running",
        {"thread_ids": list(asked.thread_ids), "question_count": count},
        actor=asked.actor,
    )
    return StepResult()


async def question_answered(step: HarnessStep, deps: StepDeps) -> StepResult:
    """``case.question_answered``: record the owner's answer, then re-run the
    agents on the changed fields when the run is in REVIEW with the agents idle,
    or flag the changed values to the analyst otherwise.
    """
    answered = step_payload(step, CaseQuestionAnswered)
    changed = sorted(answered.changed_field_ids)
    docs = sorted(answered.added_document_ids)
    run = deps.run
    rerun = reruns_now(run, changed)
    version = answered.submission_version
    saved = f" · submission v{version}" if version is not None else ""
    then = ""
    if changed:
        then = (
            " · re-run on the changed fields"
            if rerun
            else f" · flagged to the analyst, no re-run at {run.state.value}"
        )
    audit = await deps.append_record(
        step,
        "question.answered",
        f"Owner answered question {answered.question_id} · "
        f"{plural(len(changed), 'field')} changed · "
        f"{plural(len(docs), 'document')} added{saved}{then}",
        {
            "thread_id": answered.thread_id,
            "question_id": answered.question_id,
            "changed_fields": changed,
            "added_documents": docs,
            "submission_version": version,
            "rerun": rerun,
        },
        actor=answered.actor,
    )
    if not changed:
        return StepResult()
    if rerun:
        record = await deps.require_case().get_record(step.case_id)
        spec: Mapping[str, Any] = {
            "changed_fields": changed,
            "doc_ids": docs,
            "round": None,
            "submission_version": version,
            "reason": f"owner answer to question {answered.question_id}",
            "thread_id": answered.thread_id,
        }
        return await dispatch_rerun(step, deps, record, spec)
    logger.info(
        "an answer changed values while the agents could not re-run",
        extra={"case_id": step.case_id, "state": run.state.value},
    )
    what = (
        f"The owner's answer to question {answered.question_id} changed "
        f"{plural(len(changed), 'field')}"
    )
    after = (
        "; the assessment reads the new values when it runs"
        if run.state in BEFORE_ASSESSMENT
        else " since the agents ran; ask for a re-run in review if it matters"
    )
    await show(
        deps,
        step,
        "values.changed",
        "analyst",
        what + after,
        {"changed_fields": changed, "state": run.state.value},
        record_seq=audit.seq,
    )
    return StepResult()


#: The ad-hoc questions' handlers by step kind.
HANDLERS: Final[Mapping[StepKind, StepHandler]] = {
    StepKind.QUESTION_ASKED: question_asked,
    StepKind.QUESTION_ANSWERED: question_answered,
}
