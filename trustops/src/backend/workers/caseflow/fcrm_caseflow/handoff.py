"""The human handoff (flow F-path): what a handoff gate carries and how the manual
output that completes it becomes the ``agent.done`` it stands in for.

An agent that fails after its one retry sends the run to ``HANDOFF`` and opens a
``handoff`` gate for the analyst. The gate's **subject** names the failed job
(:func:`handoff_subject`) so the decision can be matched to it; the analyst's
decision (``POST /cases/{id}/handoff/{gate_id}/complete``) carries the output in
``GateDecision.after["output"]``. :func:`manual_done_payload` turns that into an
``AgentDone`` body with ``model`` ``human``, after the same proposal contracts and
Guard 4's source-free checks (:mod:`fcrm_guardrails.manual_output`) every agent
output goes through. :class:`~fcrm_caseflow.engine.CaseflowEngine` then handles it
exactly like the agent's own ``agent.done``.

Pure: nothing here reads or writes a store.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final, Optional

from fcrm_contracts.common import GateDecision
from fcrm_contracts.enums import AgentName, CaseState
from fcrm_contracts.events.payloads import AgentDone, AgentFailed
from fcrm_guardrails.manual_output import check_manual_output

#: ``AgentDone.model`` of a result a person supplied; ``AgentDone.usage`` is zeros.
HUMAN_MODEL: Final = "human"

#: ``AgentDone.output["actor"]``: who produced the proposals.
HUMAN_ACTOR: Final = "human"

_ZERO_USAGE: Final = {
    "input_tokens": 0,
    "output_tokens": 0,
    "cache_read_tokens": 0,
    "cache_write_tokens": 0,
}


class HandoffError(ValueError):
    """A handoff gate's decision cannot stand in for the failed job: the gate
    names no job, or the output fails the checks. The decision is not applied and
    the gate stays open, so the analyst can supply a corrected output.
    """


def handoff_subject(
    failed: Mapping[str, Any], suspended_from: Optional[CaseState]
) -> dict[str, str]:
    """The ``subject`` of the handoff gate for the ``agent.failed`` payload
    ``failed``: the job (``agent``, ``job_id``), how it failed (``reason``,
    ``attempt``), the stage the run was in (``step``) and, when H2 recorded
    them, a reference to the raw inputs.

    Raises:
        pydantic.ValidationError: ``failed`` is not an ``AgentFailed`` body.
    """
    body = AgentFailed.model_validate(failed)
    subject = {
        "agent": body.agent.value,
        "job_id": body.job_id,
        "attempt": str(body.attempt),
        "reason": body.reason,
    }
    if suspended_from is not None:
        subject["step"] = suspended_from.value
    if body.raw_inputs_ref:
        subject["raw_inputs_ref"] = body.raw_inputs_ref
    return subject


def _fast_lane(accepted: list[dict[str, Any]]) -> Optional[bool]:
    """The screening proposal's own ``fast_lane`` flag, as H2 copies it to the top
    level of ``agent.done`` for the transition guard; ``None`` for any other item
    list."""
    for item in accepted:
        if isinstance(item.get("fast_lane"), bool):
            return bool(item["fast_lane"])
    return False


def manual_done_payload(
    decision: GateDecision, subject: Mapping[str, str]
) -> dict[str, Any]:
    """The ``agent.done`` body that stands in for the failed job.

    Args:
        decision: The analyst's decision on the handoff gate; its ``after`` holds
            ``{"output": [<proposal>, ...]}``, its ``decided_by`` is the analyst.
        subject: The gate's subject (:func:`handoff_subject`).

    Returns:
        An ``AgentDone`` body (``model`` ``human``, zero usage) whose ``output``
        has the shape H2 publishes (``accepted``, ``dropped``,
        ``notes_for_analyst``, ``guard_summary``, ``attempts``) plus ``actor``
        and ``completed_by``. ``guard_summary.claims_with_source`` is 0: no claim
        was matched against its source, and the note says so.

    Raises:
        HandoffError: the subject names no known job, or the output is missing,
            not a list or fails :func:`~fcrm_guardrails.manual_output
            .check_manual_output`.
    """
    try:
        agent = AgentName(subject["agent"])
        job_id = subject["job_id"]
    except (KeyError, ValueError) as exc:
        raise HandoffError(
            f"gate {decision.gate_id} names no failed job to complete"
        ) from exc
    output = (decision.after or {}).get("output")
    if not isinstance(output, list):
        raise HandoffError("the decision carries no output list to complete the step")
    check = check_manual_output(agent, output)
    if not check.ok:
        raise HandoffError("; ".join(check.problems))
    attempt = int(subject.get("attempt", "1") or 1)
    notes = [
        f"completed by hand by {decision.decided_by}; sources and quotes were "
        "not matched against the documents",
        *check.notes,
    ]
    done = AgentDone(
        job_id=job_id,
        case_id=decision.case_id,
        run_id=decision.run_id,
        agent=agent,
        output={
            "accepted": list(check.accepted),
            "dropped": [],
            "notes_for_analyst": notes,
            "guard_summary": {
                "claims_total": check.claims,
                "claims_with_source": 0,
                "dropped": 0,
            },
            "attempts": attempt,
            "actor": HUMAN_ACTOR,
            "completed_by": decision.decided_by,
        },
        usage=dict(_ZERO_USAGE),
        model=HUMAN_MODEL,
        attempt=attempt,
        fast_lane_proposed=(
            _fast_lane(list(check.accepted)) if agent is AgentName.SCREENING else None
        ),
    )
    return done.model_dump(mode="json")
