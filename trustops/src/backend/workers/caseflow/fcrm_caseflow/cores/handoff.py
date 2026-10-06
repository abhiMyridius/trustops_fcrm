"""The handoff core (flow F-path): both ends of the human handoff.

An agent that fails after its one retry sends the run to ``handoff`` and the analyst
finishes the step by hand. One step kind, ``handoff.open``, covers both ends, told
apart by the trigger that enqueued it:

* ``agent.failed.after_retry``: the run is in ``handoff``. S1 learns the stage (so
  the analyst's banner shows) and the chain gets ``human.handoff`` with the digest of
  the inputs the gate shows. H2 already wrote ``agent.failed``.
* ``gate.decided.handoff``: the analyst finished a job. S1 learns the stage the run
  is in now (back where it was suspended from, or still ``handoff`` while another
  failed job waits) and the chain gets ``human.handoff`` naming who completed which
  job, how many proposals, and that none of their claims was matched to a source.

Both write through :meth:`~fcrm_caseflow.steps.StepDeps.append_record`, so a step
that runs twice writes each record once.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from fcrm_common.errors import ValidationError as FcrmValidationError
from fcrm_contracts.common import GateDecision
from fcrm_contracts.enums import GateType, StepKind
from fcrm_contracts.events.payloads import AgentFailed, HarnessStep

from fcrm_caseflow.cores.shared import state_patch, step_payload
from fcrm_caseflow.gates import deterministic_gate_id
from fcrm_caseflow.raw_inputs import digest_of
from fcrm_caseflow.steps import StepDeps, StepHandler, StepResult
from fcrm_caseflow.triggers import Trigger

RECORD_TYPE: Final = "human.handoff"


async def _gate_subject(
    step: HarnessStep, deps: StepDeps, gate_id: str
) -> Mapping[str, str]:
    """The subject of ``gate_id`` (the failed job and its inputs), empty when the
    gate store is not wired or holds no such gate."""
    if deps.gates is None:
        return {}
    gate = await deps.gates.get(gate_id)
    return dict((gate or {}).get("subject") or {})


async def _opened(step: HarnessStep, deps: StepDeps) -> StepResult:
    failed = step_payload(step, AgentFailed)
    gate_id = deterministic_gate_id(step.run_id, GateType.HANDOFF, failed.job_id)
    subject = await _gate_subject(step, deps, gate_id)
    data: dict[str, Any] = {
        "phase": "opened",
        "gate_id": gate_id,
        "agent": failed.agent.value,
        "job_id": failed.job_id,
        "reason": failed.reason,
        **digest_of({"raw_inputs_ref": failed.raw_inputs_ref, **subject}),
    }
    await deps.append_record(
        step,
        RECORD_TYPE,
        f"{failed.agent.value} failed twice ({failed.reason}); the step is with the "
        "analyst",
        data,
    )
    await deps.require_case().patch_state(step.case_id, state_patch(deps.run))
    return StepResult()


async def _completed(step: HarnessStep, deps: StepDeps) -> StepResult:
    payload = step.args.get("payload")
    known = set(GateDecision.model_fields)
    try:
        decision = GateDecision.model_validate(
            {k: v for k, v in (payload or {}).items() if k in known}
        )
    except ValueError as exc:
        raise FcrmValidationError(
            "the handoff payload is not a gate decision", step_id=step.step_id
        ) from exc
    subject = await _gate_subject(step, deps, decision.gate_id)
    output = (decision.after or {}).get("output")
    items = len(output) if isinstance(output, list) else 0
    agent = subject.get("agent", "agent")
    await deps.append_record(
        step,
        RECORD_TYPE,
        f"{agent} completed by hand by {decision.decided_by}: {decision.reason}",
        {
            "phase": "completed",
            "gate_id": decision.gate_id,
            "agent": subject.get("agent"),
            "job_id": subject.get("job_id"),
            "items": items,
            "claims_matched_to_sources": 0,
            **digest_of(subject),
        },
        actor=decision.decided_by,
    )
    await deps.require_case().patch_state(step.case_id, state_patch(deps.run))
    return StepResult()


async def handoff_open(step: HarnessStep, deps: StepDeps) -> StepResult:
    """Run either end of the handoff (see the module doc), by the trigger that
    enqueued the step.

    Raises:
        fcrm_common.errors.ValidationError: the trigger's payload is not the body
            its trigger promises (the step is retried, then parked).
    """
    trigger = step.args.get("trigger")
    if trigger == Trigger.GATE_DECIDED_HANDOFF.value:
        return await _completed(step, deps)
    return await _opened(step, deps)


#: The handoff core's handlers by step kind.
HANDLERS: Final[Mapping[StepKind, StepHandler]] = {StepKind.HANDOFF_OPEN: handoff_open}
