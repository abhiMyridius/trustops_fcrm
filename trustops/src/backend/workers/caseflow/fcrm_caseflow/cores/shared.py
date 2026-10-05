"""Helpers every core's step handlers share: reading a trigger's payload, showing
an event on the case stream, and naming the ids a handler derives.

Nothing here talks to a peer except :func:`show`, and nothing decides anything.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Final, Optional

from fcrm_common.errors import NotFoundError
from fcrm_common.errors import ValidationError as FcrmValidationError
from fcrm_common.ids import deterministic_id
from fcrm_contracts.api.audit import Proposal, ProposalAppend, ProposalTokens
from fcrm_contracts.api.case_internal import (
    StatePatch,
    UiActor,
    UiEventIn,
    UiVisibility,
)
from fcrm_contracts.events.payloads import HarnessStep
from pydantic import BaseModel, ValidationError

from fcrm_caseflow.dispatch import AGENT_TIERS
from fcrm_caseflow.steps import StepDeps
from fcrm_caseflow.store import AgentResult, CaseRun

#: Who H1 is on the case stream.
UI_ACTOR: Final = UiActor(id="H1", role="system", component="H1")


def step_payload[P: BaseModel](step: HarnessStep, model: type[P]) -> P:
    """The trigger's payload (``step.args["payload"]``) as ``model``.

    Raises:
        fcrm_common.errors.ValidationError: the payload is missing or does not
            match the contract (the step is retried, then parked).
    """
    try:
        return model.model_validate(step.args.get("payload"))
    except ValidationError as exc:
        raise FcrmValidationError(
            f"the {step.step.value} payload is not a valid {model.__name__}",
            step_id=step.step_id,
            errors_count=exc.error_count(),
        ) from exc


async def show(
    deps: StepDeps,
    step: HarnessStep,
    event_type: str,
    visibility: UiVisibility,
    summary: str,
    detail: Optional[Mapping[str, Any]] = None,
    *,
    record_seq: Optional[int] = None,
    ordinal: int = 0,
) -> None:
    """Ask S1 to show an event on the case stream, once per ``(step, type,
    ordinal)`` (the idempotency key makes a rerun a no-op at S1).

    ``visibility="analyst"`` is the only value the assessment half uses: a score,
    a band, a rating or a critic finding never reaches the PO.
    """
    event = UiEventIn(
        type=event_type,
        visibility=visibility,
        summary=summary,
        detail=dict(detail or {}),
        actor=UI_ACTOR,
        record_seq=record_seq,
    )
    await deps.require_case().post_event(
        step.case_id,
        event,
        idempotency_key=deterministic_id(
            "idem", step.step_id, "ui", event_type, str(ordinal)
        ),
    )


def idem(step: HarnessStep, *parts: str) -> str:
    """The ``Idempotency-Key`` of one write a step makes: derived from the step
    id and what the write is, so a step that runs twice sends the same keys.
    """
    return deterministic_id("idem", step.step_id, *parts)


def result_for(
    results: Sequence[AgentResult], step: HarnessStep, agent: str
) -> AgentResult:
    """The stored result the step's trigger reported: the job named by the
    ``agent.done`` payload, else the newest (``results`` are oldest first).

    Raises:
        fcrm_common.errors.NotFoundError: no result is stored for the run (a
            wiring fault: the engine stores each ``agent.done``).
    """
    if not results:
        raise NotFoundError(f"no {agent} agent result is stored for run {step.run_id}")
    payload = step.args.get("payload")
    job_id = payload.get("job_id") if isinstance(payload, Mapping) else None
    for result in results:
        if result.job_id == job_id:
            return result
    return results[-1]


def tokens_of(results: Iterable[AgentResult]) -> int:
    """The tokens the model calls behind ``results`` used, input and output."""
    return sum(
        r.usage.get("input_tokens", 0) + r.usage.get("output_tokens", 0)
        for r in results
    )


async def post_proposal(
    deps: StepDeps,
    step: HarnessStep,
    result: AgentResult,
    *,
    kind: str,
    payload: Mapping[str, Any],
    key: str,
    supersedes: Optional[str] = None,
    carry_usage: bool = True,
) -> Proposal:
    """Append one proposal to S4's evidence ledger, once per ``(step, key)``.

    ``carry_usage`` puts the job's token usage on this entry; a job that yields
    several entries carries it on the first only, so summing the ledger does not
    count one model call per entry.

    Raises:
        fcrm_common.errors.FcrmError: S4 refused (a ``supersedes`` that already
            has a successor is a conflict) or was unreachable.
    """
    usage = result.usage if carry_usage else {}
    appended = ProposalAppend(
        case_id=step.case_id,
        run_id=step.run_id,
        agent=result.agent.value,
        kind=kind,
        payload=dict(payload),
        model=result.model or "unknown",
        prompt_v=deps.run.pins.prompts_v,
        tier=AGENT_TIERS[result.agent].value,
        tokens=ProposalTokens(
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            cache_read_tokens=usage.get("cache_read_tokens", 0),
            cache_write_tokens=usage.get("cache_write_tokens", 0),
        ),
        supersedes=supersedes,
    )
    return await deps.audit.append_proposal(
        appended, idempotency_key=idem(step, "proposal", key)
    )


def state_patch(run: CaseRun) -> StatePatch:
    """What S1 should hold about the run now: its stage and the run-snapshot
    flags (``PATCH /internal/cases/{id}/state`` changes only what is set, and the
    same patch twice changes nothing).
    """
    flags = run.snapshot.flags
    return StatePatch(
        stage=run.state,
        run_id=run.snapshot.run_id,
        round_open=flags.round_open,
        locked=flags.locked,
        pack_version=flags.pack_version or None,
        vote_round=flags.vote_round or None,
    )
