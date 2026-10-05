"""S1's own refusals: each a 409 with its machine ``code`` as the last segment of
the problem ``type`` (``https://trustops.local/errors/<code>``), so a client can
tell "this case is closed" from "this outcome differs" without parsing prose.
"""

from __future__ import annotations

from fcrm_common.errors import ConflictError
from fcrm_contracts.enums import CaseState

from fcrm_case.domain.record import CaseRecord


class CaseClosedError(ConflictError):
    """The case is closed and takes no more writes, from any role (rule 5:
    closed is closed). Reads, the audit trail and the conditions' own tracking
    are the only things that continue.
    """

    code = "case_closed"
    title = "Case closed"


class OutcomeConflictError(ConflictError):
    """The case already has an outcome, and it is not the one offered."""

    code = "outcome_already_set"
    title = "Outcome already set"


class IdempotencyKeyReusedError(ConflictError):
    """The ``Idempotency-Key`` was already used for a different request."""

    code = "idempotency_key_reused"
    title = "Idempotency key reused"


class CloseAlreadyRequestedError(ConflictError):
    """A delegated close was already requested for this case, differently."""

    code = "close_already_requested"
    title = "Close already requested"


class ConditionClosedError(ConflictError):
    """The condition is closed: closing is terminal, so it cannot change."""

    code = "condition_closed"
    title = "Condition closed"


class ConditionMoveError(ConflictError):
    """The status change is not one an analyst may make from where the
    condition is.
    """

    code = "condition_move_refused"
    title = "Condition status change refused"


def ensure_open(record: CaseRecord) -> None:
    """Refuse a write to a closed case: the one guard every S1 write path calls.

    Raises:
        CaseClosedError: ``record`` is in the ``closed`` state.
    """
    if record.state is CaseState.CLOSED:
        raise CaseClosedError(
            f"case {record.case_id} is closed and takes no more changes",
            case_id=record.case_id,
        )
