"""Helpers every core's step handlers share: reading a trigger's payload, showing
an event on the case stream, and naming the ids a handler derives.

Nothing here talks to a peer except :func:`show`, and nothing decides anything.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Final, Optional

from fcrm_common.errors import ConflictError, NotFoundError
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
from fcrm_guardrails.restricted_terms import RESTRICTED_KEYS
from fcrm_guardrails.rules.r3_po_no_restricted import find_restricted
from pydantic import BaseModel, ValidationError

from fcrm_caseflow.dispatch import AGENT_TIERS
from fcrm_caseflow.steps import StepDeps
from fcrm_caseflow.store import AgentResult, CaseRun

logger = logging.getLogger(__name__)

#: Who H1 is on the case stream.
UI_ACTOR: Final = UiActor(id="H1", role="system", component="H1")

#: Harness identifiers S1's owner fence refuses beside the shared guardrails list
#: (``fcrm_case.domain.activity.PO_RESTRICTED_KEYS``); a test holds the two
#: together, so a key added there is added here.
HARNESS_ID_KEYS: Final = frozenset(
    {
        "agent",
        "boundary_flag",
        "cost",
        "job_id",
        "model",
        "prompt",
        "record_seq",
        "run_id",
        "step_id",
        "tokens",
        "trace_id",
    }
)
OWNER_REFUSED_KEYS: Final = RESTRICTED_KEYS | HARNESS_ID_KEYS
#: Visibilities the owner can see.
_OWNER_VISIBLE: Final = ("po", "all")


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

    A note for the owner never stops the step (D-130: a refused note once left a
    step stuck, and a stuck step blocks the case's next automatic step). It is
    checked before it is sent and anything the owner fence refuses is dropped;
    if S1 still refuses it, it is sent again without its detail, and, if that is
    refused too, to the analysts instead, with a warning. Only an error that is
    not a refusal (S1 down, a timeout) fails the step, for the broker to retry.
    """
    kept = dict(detail or {})
    if visibility in _OWNER_VISIBLE:
        visibility, kept = _owner_safe(event_type, visibility, summary, kept)
    note = _Note(deps, step, event_type, summary, record_seq, ordinal)
    try:
        await note.post(visibility, kept)
        return
    except ConflictError:
        if visibility not in _OWNER_VISIBLE:
            raise
    logger.warning(
        "S1 refused an owner note; sending it again without its detail",
        extra={"event_type": event_type, "case_id": step.case_id},
    )
    try:
        await note.post(visibility, {}, "bare")
        return
    except ConflictError:
        pass
    logger.warning(
        "S1 refused an owner note again; showing it to the analysts instead",
        extra={"event_type": event_type, "case_id": step.case_id},
    )
    await note.post("analyst", kept, "staff")


def _owner_safe(
    event_type: str,
    visibility: UiVisibility,
    summary: str,
    detail: dict[str, Any],
) -> tuple[UiVisibility, dict[str, Any]]:
    """What may be sent to the owner: the detail without any key the owner fence
    refuses (named in a warning), and the analysts' view instead when the
    summary itself names a restricted term."""
    if find_restricted(summary):
        logger.warning(
            "an owner note names a restricted term; showing it to the analysts",
            extra={"event_type": event_type},
        )
        return "analyst", detail
    kept = {
        key: value
        for key, value in detail.items()
        if not (str(key).lower() in OWNER_REFUSED_KEYS or find_restricted({key: value}))
    }
    if len(kept) != len(detail):
        logger.warning(
            "an owner note carried details the owner fence refuses; dropped",
            extra={
                "event_type": event_type,
                "dropped_keys": sorted(str(k) for k in set(detail) - set(kept)),
            },
        )
    return visibility, kept


class _Note:
    """One event a step shows, posted under an idempotency key that depends on
    which attempt it is, so a re-run of the step lands on the same attempts."""

    def __init__(
        self,
        deps: StepDeps,
        step: HarnessStep,
        event_type: str,
        summary: str,
        record_seq: Optional[int],
        ordinal: int,
    ) -> None:
        self._deps = deps
        self._step = step
        self._type = event_type
        self._summary = summary
        self._record_seq = record_seq
        self._ordinal = ordinal

    async def post(
        self, visibility: UiVisibility, detail: Mapping[str, Any], attempt: str = ""
    ) -> None:
        """Post the event; ``attempt`` is ``""`` for the first try."""
        parts = ["ui", self._type, str(self._ordinal)]
        if attempt:
            parts.append(attempt)
        await self._deps.require_case().post_event(
            self._step.case_id,
            UiEventIn(
                type=self._type,
                visibility=visibility,
                summary=self._summary,
                detail=dict(detail),
                actor=UI_ACTOR,
                record_seq=self._record_seq,
            ),
            idempotency_key=deterministic_id("idem", self._step.step_id, *parts),
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
