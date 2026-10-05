"""Exceptions raised by the pure state machine."""

from __future__ import annotations

from fcrm_contracts.enums import CaseState

from fcrm_caseflow.triggers import Trigger


class IllegalTransition(Exception):
    """Raised when no transition matches ``(state, trigger)`` with a
    passing guard. Design A §3.1: "the message is acked, an error activity
    is written and an alert metric incremented; the run is never left
    half-applied". This exception is the seam the real runtime catches to
    do exactly that — the pure reducer itself just refuses to guess.
    """

    def __init__(self, state: CaseState, trigger: Trigger, reason: str = "") -> None:
        self.state = state
        self.trigger = trigger
        detail = f": {reason}" if reason else ""
        super().__init__(
            f"no transition from {state.value!r} on trigger {trigger.value!r}{detail}"
        )
