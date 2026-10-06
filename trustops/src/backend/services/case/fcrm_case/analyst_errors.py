"""The refusals S1's analyst write routes answer with, each a 409 whose machine
``code`` is the last segment of the problem ``type`` (the same convention as
:mod:`fcrm_case.errors`), so a client can tell "the case is not in review yet"
from "this gate was decided differently" without parsing prose.
"""

from __future__ import annotations

from fcrm_common.errors import ConflictError


class WrongStageError(ConflictError):
    """The case is not at the stage this action belongs to (a decision on an
    item before review has started, a lock before the narrative exists).
    """

    code = "wrong_stage"
    title = "Wrong stage"


class NotReadyError(ConflictError):
    """Caseflow has not opened the gate this action decides (no run yet, no
    completeness report, no proposal), so there is nothing to decide.
    """

    code = "not_ready"
    title = "Not ready"


class GateAlreadyDecidedError(ConflictError):
    """The gate was decided, differently: a decision is made once per gate."""

    code = "gate_already_decided"
    title = "Gate already decided"


class NoOpenGateError(ConflictError):
    """There is no open gate of the kind named (nothing to acknowledge)."""

    code = "no_open_gate"
    title = "No open gate"


class ItemSupersededError(ConflictError):
    """A re-run replaced this item; its replacement is the one to decide."""

    code = "item_superseded"
    title = "Item superseded"


class DecisionNotAllowedError(ConflictError):
    """The item's kind does not take this decision (a clause is accepted or
    removed, never overridden) or the item cannot be decided here at all.
    """

    code = "decision_not_allowed"
    title = "Decision not allowed"


class RoundAlreadyOpenError(ConflictError):
    """A clarification round was already sent: there is one round (D-16)."""

    code = "round_already_sent"
    title = "Round already sent"
