"""Run-flag bookkeeping (``fast_lane``, ``round_open``, ``locked``,
``pack_version``, ``vote_round``).

:func:`fcrm_caseflow.transitions.apply` is pure and never touches
:class:`~fcrm_caseflow.states.RunFlags` — states.py's own docstring says
flags "are written by transition actions as side information", and
``TransitionResult`` carries only the next state and an action label, not
a flag update. This module is the one place that side information is
actually computed, directly off the same action text already in
``transitions.py``'s table (cited per trigger below) — a judgment call
documented here rather than silently invented in :mod:`fcrm_caseflow
.engine`.
"""

from __future__ import annotations

from fcrm_caseflow.states import RunFlags
from fcrm_caseflow.transitions import TransitionContext
from fcrm_caseflow.triggers import Trigger


def next_flags(trigger: Trigger, ctx: TransitionContext, current: RunFlags) -> RunFlags:
    """``current`` unchanged, except for the one flag (if any) the action
    text for ``trigger`` describes changing.

    The documented mapping:

    - ``GATE_DECIDED_FAST_LANE_CONFIRM``: "confirmed: set fast_lane=true
      ...; declined: full path" -> ``fast_lane = ctx.fast_lane_confirmed``.
    - ``GATE_DECIDED_COMPLETENESS_ASK``: "pause the analyst clock (one
      round only, D-16)" -> opens the one clarification round.
    - ``AGENT_DONE_RERUN_BOTH``: the rerun that answers the one
      clarification round completes -> closes it.
    - ``CASE_LOCKED``: "build the pack from S1's view and S4's head; POST
      /packs" -> locks the case and starts a new pack version.
    - ``CASE_UNLOCKED`` / ``COMMITTEE_ANSWERED_CHANGED`` ("R6: unlock with
      reason, exactly like case.unlocked") -> unlocks (a later re-lock
      bumps ``pack_version`` again via ``CASE_LOCKED``, giving pack v2
      naturally).
    - ``PACK_RELEASED``: entering ``DECIDE`` opens a fresh voting round on
      the newly released pack.
    - ``VOTE_NO_MAJORITY``: "open a new round on the same pack" -> another
      round, same pack version.

    Every other trigger returns ``current`` unchanged — the safe default
    for anything this layer has no documented flag effect for.
    """
    if trigger is Trigger.GATE_DECIDED_FAST_LANE_CONFIRM:
        return current.model_copy(update={"fast_lane": ctx.fast_lane_confirmed})
    if trigger is Trigger.GATE_DECIDED_COMPLETENESS_ASK:
        return current.model_copy(update={"round_open": True})
    if trigger is Trigger.AGENT_DONE_RERUN_BOTH:
        return current.model_copy(update={"round_open": False})
    if trigger is Trigger.CASE_LOCKED:
        return current.model_copy(
            update={"locked": True, "pack_version": current.pack_version + 1}
        )
    if trigger in (Trigger.CASE_UNLOCKED, Trigger.COMMITTEE_ANSWERED_CHANGED):
        return current.model_copy(update={"locked": False})
    if trigger is Trigger.PACK_RELEASED:
        return current.model_copy(update={"vote_round": current.vote_round + 1})
    if trigger is Trigger.VOTE_NO_MAJORITY:
        return current.model_copy(update={"vote_round": current.vote_round + 1})
    return current
