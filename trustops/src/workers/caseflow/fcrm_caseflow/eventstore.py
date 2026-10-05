"""Replay: folding ``run_events`` back into a state, purely.

Design A §3.5: "``case_runs`` (snapshot) ... ``run_events`` (append-only)
... Replay = ``Caseflow.rebuild(run_id)`` folds ``run_events`` and must
equal the snapshot (tested)." This module is that fold, written against
plain (trigger, context) pairs rather than a Mongo cursor, so it is usable
identically by the real runtime (which reads ``run_events`` from Mongo and
feeds this function) and by tests (which build the pairs directly).
"""

from __future__ import annotations

from dataclasses import dataclass

from fcrm_contracts.enums import CaseState

from fcrm_caseflow.transitions import TransitionContext, apply
from fcrm_caseflow.triggers import Trigger


@dataclass(frozen=True)
class RecordedTransition:
    """One ``run_events`` row, reduced to what replay needs: which trigger
    fired and what the guard-relevant context was at that moment. The real
    runtime's ``run_events`` documents carry considerably more (timestamps,
    message ids, the full event payload) — this is deliberately the
    minimal projection of that document replay actually needs.
    """

    trigger: Trigger
    context: TransitionContext


def rebuild(initial_state: CaseState, events: list[RecordedTransition]) -> CaseState:
    """Fold every recorded transition over ``initial_state`` in order and
    return the final state. Raises the same
    :class:`~fcrm_caseflow.exceptions.IllegalTransition` the live runtime
    would if the recorded events do not form a legal path — which is
    exactly the guarantee design A §3.5 wants from this function: a replay
    that disagrees with what was recorded is a bug worth surfacing, not
    silently tolerating.
    """
    state = initial_state
    for event in events:
        result = apply(state, event.trigger, event.context)
        state = result.to_state
    return state
