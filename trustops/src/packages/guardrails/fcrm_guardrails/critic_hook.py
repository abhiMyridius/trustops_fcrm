"""Guard 6: the critic scheduling hook (state; H1; design B §4.7).

Not the critic agent (``fcrm_agents`` owns that) and not a check: the seam
H1 Caseflow calls to decide *when* the critic runs, so the rule lives next
to the other guards and is unit-tested, rather than being an ``if`` buried
in a transition handler.

Design A §3.1: ``ASSESS --agent.done research/assessment--> POST /score``,
record ``score.computed``, go to ``CRITIC``; ``CRITIC --internal--> agent.run
critic``; ``CRITIC --agent.done critic--> COMPLETENESS``. Design B §4.7:
"after S2 returns the first score for a run, H1 dispatches the critic job;
the state machine cannot enter completeness until agent.done for the critic
has landed or the critic hit F6".

So the critic is scheduled exactly once per run: on ``score.computed`` while
the case is in ``ASSESS`` (or has just moved to ``CRITIC`` on that event).
A ``score.recomputed`` after an analyst override, or a re-score after the
clarification round, does not re-run it: the analyst has already answered
the findings, and the committee sees the override reasons instead.
"""

from __future__ import annotations

from typing import Final, Literal, Optional

from fcrm_contracts.enums import CaseState
from fcrm_contracts.events.catalogue import EVENT_TYPES

CRITIC_TRIGGER_EVENT: Final = "score.computed"
CRITIC_STATES: Final[frozenset[CaseState]] = frozenset(
    {CaseState.ASSESS, CaseState.CRITIC}
)

CriticStatus = Literal["pending", "done", "handoff"]


class UnknownEventError(ValueError):
    """The event type is not in the catalogue (a typo must not read as
    "do not schedule")."""


def should_schedule_critic(event: str, state: Optional[CaseState] = None) -> bool:
    """True exactly when H1 should dispatch ``agent.run critic``.

    Args:
        event: the event H1 just recorded or consumed.
        state: the case state when the event happened; ``None`` skips the
            state check (for callers that have already matched the
            transition).

    Raises:
        UnknownEventError: ``event`` is not in ``EVENT_TYPES``.
    """
    if event not in EVENT_TYPES:
        msg = f"unknown event type {event!r}"
        raise UnknownEventError(msg)
    if event != CRITIC_TRIGGER_EVENT:
        return False
    return state is None or state in CRITIC_STATES


def may_enter_completeness(critic_status: CriticStatus) -> bool:
    """True once the critic has landed (``done``) or failed after its retry
    into the handoff gate (``handoff``, F6); never while ``pending``."""
    return critic_status in ("done", "handoff")
