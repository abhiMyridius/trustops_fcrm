"""What happens, besides the state transition itself, when a given trigger
fires: which ``agent.run`` job(s) to publish (design A §3.1's "Action"
column, the "dispatch" half) and which gate (if any) to open (the "open
gate" half — Guard 7, design A §3.2).

Both tables are keyed on the :class:`~fcrm_caseflow.triggers.Trigger`
that just fired, not on ``TransitionResult.action``'s free-text label:
each "internal" dispatch trigger
(:mod:`fcrm_caseflow.triggers`'s module docstring) already identifies its
dispatch point uniquely — there is exactly one row in ``TRANSITIONS`` for
each of ``SCREENING_DISPATCH``/``ASSESS_DISPATCH``/``CRITIC_DISPATCH``/
``COMPLETENESS_DISPATCH``/``ALL_ITEMS_DECIDED`` — so switching on the
trigger is exactly as precise as switching on the action text would be,
without parsing a sentence meant for humans.

The fast-lane branch of ``ASSESS_DISPATCH`` (research+assessment in
parallel, or assessment alone) is **not** special-cased here: it reads
``flags.fast_lane`` directly, which :mod:`fcrm_caseflow.flags` already set
correctly by the time ``GATE_DECIDED_FAST_LANE_CONFIRM``'s transition
chains straight into it (both land in ``ASSESS`` in the same
:mod:`fcrm_caseflow.engine` call) — so the "confirmed: assessment only;
declined: full path" branch in that trigger's own action text falls out
of this table automatically rather than needing its own entry.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final, Optional

from fcrm_common.events import build_envelope
from fcrm_common.ids import new_id
from fcrm_contracts.enums import AgentName, GateType, Role, Tier
from fcrm_contracts.events.envelope import Envelope
from fcrm_contracts.events.payloads import JobPayload

from fcrm_caseflow.states import RunFlags
from fcrm_caseflow.store import CaseRun
from fcrm_caseflow.transitions import TransitionContext
from fcrm_caseflow.triggers import Trigger

#: Agent -> tier, duplicated (not imported) from ``fcrm_agents.spec
#: .REGISTRY`` by design: Caseflow "never reasons" and stays free of the
#: AI layer's own package (H1 does not depend on H2's ``fcrm_agents``), it
#: only needs the tier to size the budget reservation and populate
#: ``JobPayload.tier`` for the agent-worker/guard-3 budget check. Keep
#: this in step with ``fcrm_agents.spec.REGISTRY`` by hand if that table
#: ever changes.
AGENT_TIERS: Final[dict[AgentName, Tier]] = {
    AgentName.SCREENING: Tier.CHEAP,
    AgentName.INTAKE: Tier.CHEAP,
    AgentName.RESEARCH: Tier.STRONG,
    AgentName.ASSESSMENT: Tier.STRONG,
    AgentName.CRITIC: Tier.JUDGE,
    AgentName.NARRATIVE: Tier.MID,
    AgentName.EXAMINER: Tier.MID,
    AgentName.WATCHLIST: Tier.CHEAP,
    AgentName.POLICY_GAP: Tier.MID,
}

#: Guard 7's required role per gate type (gates-register.md).
REQUIRED_ROLE: Final[dict[GateType, Role]] = {
    GateType.PO_CONFIRM_CATEGORY: Role.PO,
    GateType.PO_CONFIRM_VALUE: Role.PO,
    GateType.PO_REUSE: Role.PO,
    GateType.PO_ANSWER_GAPS: Role.PO,
    GateType.FAST_LANE_CONFIRM: Role.ANALYST,
    GateType.REVIEW_ITEM: Role.ANALYST,
    GateType.ACK: Role.ANALYST,
    GateType.COMPLETENESS: Role.ANALYST,
    GateType.LOCK: Role.ANALYST,
    GateType.PACK_RELEASE: Role.ANALYST,
    GateType.VOTE: Role.COMMITTEE,
    GateType.REFLAG_ACK: Role.ANALYST,
    GateType.HANDOFF: Role.ANALYST,
    GateType.UNLOCK: Role.ANALYST,
}


@dataclass(frozen=True)
class AgentDispatch:
    """One ``agent.run`` to publish."""

    agent: AgentName
    scope: Optional[str] = None
    changed_fields: tuple[str, ...] = ()
    step_key: Optional[str] = None


@dataclass(frozen=True)
class DispatchPlan:
    """What :mod:`fcrm_caseflow.engine` should do after applying one
    trigger: zero or more agent jobs to publish, and the ``gates`` set
    those jobs' *own* dispatch is tracked under when more than one must
    all report done before the next trigger self-fires (the
    research+assessment / rerun joins).
    """

    agents: tuple[AgentDispatch, ...] = ()


def dispatch_for(
    trigger: Trigger,
    ctx: TransitionContext,
    flags: RunFlags,
    payload: Optional[dict[str, Any]] = None,
) -> DispatchPlan:
    """The jobs to dispatch immediately after ``trigger`` applied, given
    the run's flags *after* :func:`fcrm_caseflow.flags.next_flags` already
    ran for this same trigger (so ``flags.fast_lane`` is already correct
    for ``ASSESS_DISPATCH``'s branch). ``payload`` is the raw event
    payload that produced ``trigger`` (only ``CASE_ANSWERED`` reads it, for
    ``changed_field_ids`` — design A §4.3's ``case.answered`` payload —
    since that is not one of :class:`TransitionContext`'s own fields).
    """
    if trigger is Trigger.SCREENING_DISPATCH:
        return DispatchPlan((AgentDispatch(AgentName.SCREENING),))
    if trigger is Trigger.ASSESS_DISPATCH:
        if flags.fast_lane:
            return DispatchPlan((AgentDispatch(AgentName.ASSESSMENT),))
        return DispatchPlan(
            (AgentDispatch(AgentName.RESEARCH), AgentDispatch(AgentName.ASSESSMENT))
        )
    if trigger is Trigger.CRITIC_DISPATCH:
        return DispatchPlan((AgentDispatch(AgentName.CRITIC),))
    if trigger is Trigger.COMPLETENESS_DISPATCH:
        return DispatchPlan((AgentDispatch(AgentName.NARRATIVE, scope="completeness"),))
    if trigger is Trigger.CASE_ANSWERED:
        changed = tuple((payload or {}).get("changed_field_ids") or ())
        return DispatchPlan(
            (
                AgentDispatch(
                    AgentName.RESEARCH, scope="changed_fields", changed_fields=changed
                ),
                AgentDispatch(
                    AgentName.ASSESSMENT,
                    scope="changed_fields",
                    changed_fields=changed,
                ),
            )
        )
    if trigger is Trigger.ALL_ITEMS_DECIDED:
        return DispatchPlan(
            (AgentDispatch(AgentName.NARRATIVE, scope="recommendation"),)
        )
    if trigger is Trigger.GATE_DECIDED_PO_CONFIRM_CATEGORY_ALL and ctx.gaps_open:
        return DispatchPlan((AgentDispatch(AgentName.INTAKE, scope="questions_only"),))
    return DispatchPlan(())


#: Which gate (if any) opens as a direct consequence of ``trigger``
#: firing, beyond what ``_GATE_OPEN_CONDITIONAL`` decides from ``ctx``.
_GATE_OPEN_UNCONDITIONAL: Final[dict[Trigger, GateType]] = {
    Trigger.AGENT_DONE_NARRATIVE_COMPLETENESS: GateType.COMPLETENESS,
    Trigger.AGENT_FAILED_AFTER_RETRY: GateType.HANDOFF,
    Trigger.CASE_LOCKED: GateType.PACK_RELEASE,
    # One po_confirm_category gate per AGENT_DONE_INTAKE, and one
    # review_item gate per AGENT_DONE_ASSESS_BOTH: a documented
    # simplification of "open PO gates"/per-item review gates (design A
    # §3.1, §3.2) until a real S1 items integration exists to fan these out
    # one per proposed change type / clause / rating / control / critic
    # finding — see module/package docstrings. ``all_items_decided``
    # (:mod:`fcrm_caseflow.engine`'s gate-derived context refresh) reads
    # "all decided" off this same single gate's open count, so the
    # simplification is consistent end to end, not just at open time.
    Trigger.AGENT_DONE_INTAKE: GateType.PO_CONFIRM_CATEGORY,
    Trigger.AGENT_DONE_ASSESS_BOTH: GateType.REVIEW_ITEM,
}


def gate_to_open(trigger: Trigger, ctx: TransitionContext) -> Optional[GateType]:
    """The gate type to open as a result of ``trigger`` having just
    applied, or ``None``.
    """
    direct = _GATE_OPEN_UNCONDITIONAL.get(trigger)
    if direct is not None:
        return direct
    if trigger is Trigger.AGENT_DONE_SCREENING and ctx.fast_lane_proposed:
        return GateType.FAST_LANE_CONFIRM
    if trigger is Trigger.GATE_DECIDED_REVIEW_ITEM and (
        ctx.band_changed or ctx.boundary_flagged
    ):
        return GateType.ACK
    return None


def build_job_envelope(
    dispatch: AgentDispatch, run: CaseRun, *, attempt: int = 1
) -> Envelope:
    """One ``agent.run`` envelope for ``dispatch`` against ``run``'s
    pinned versions (design A §3.1's "open run ... pins from active
    versions").
    """
    payload = JobPayload(
        job_id=new_id("job"),
        case_id=run.snapshot.case_id,
        run_id=run.snapshot.run_id,
        agent=dispatch.agent,
        tier=AGENT_TIERS[dispatch.agent],
        step_key=dispatch.step_key or (dispatch.scope or dispatch.agent.value),
        attempt=attempt,
        scope=dispatch.scope,
        changed_fields=list(dispatch.changed_fields),
        pins=run.pins,
    )
    return build_envelope(
        "agent.run",
        payload.model_dump(mode="json"),
        case_id=run.snapshot.case_id,
        run_id=run.snapshot.run_id,
    )
