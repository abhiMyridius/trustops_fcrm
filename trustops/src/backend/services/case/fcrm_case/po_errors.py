"""The refusals the PO surface answers with, as problem+json types of their own
(so a client, a test or a log can tell "the case is closed" from "you already
submitted" without parsing prose), and the one check every PO write starts with.
"""

from __future__ import annotations

from fcrm_common.errors import ConflictError, FcrmError, NotFoundError, ValidationError
from fcrm_contracts.enums import CaseState

from fcrm_case.domain.record import OPEN_FOR_WRITES_STATES, CaseRecord


class CaseClosedError(ConflictError):
    """The case is decided or closed: it takes no further writes from anyone
    (rule 5, "closed means closed").
    """

    code = "case_closed"
    title = "Case closed"


class AlreadySubmittedError(ConflictError):
    """The case was submitted: its values are frozen and change only through a
    clarification answer.
    """

    code = "already_submitted"
    title = "Already submitted"


class NotReadyError(ConflictError):
    """The case cannot take this write yet (the harness has not opened its run,
    so there is no gate to decide).
    """

    code = "not_ready"
    title = "Not ready"


class SubmitChecksFailedError(ValidationError):
    """A blocking submit check failed; ``errors`` names each one."""

    code = "submit_checks_failed"
    title = "Submit checks failed"


class FileRefusedError(ValidationError):
    """An uploaded file is not one the case can take (type, content or name)."""

    code = "file_refused"
    title = "File refused"


class PayloadTooLargeError(FcrmError):
    """An upload is over the size or count limit."""

    status_code = 413
    code = "payload_too_large"
    title = "Payload too large"


#: States in which a case is decided or closed.
_CLOSED_STATES = frozenset({CaseState.CLOSED, CaseState.DECIDE})


def ensure_open(record: CaseRecord) -> None:
    """Raise unless ``record`` is an open draft the owner may still change.

    Raises:
        NotFoundError: The draft was discarded (it reads as missing).
        CaseClosedError: The case is decided or closed.
        AlreadySubmittedError: The case was submitted.
    """
    if record.discarded_at is not None:
        raise NotFoundError(f"no such case {record.case_id}")
    if record.state in _CLOSED_STATES:
        raise CaseClosedError(f"case {record.case_id} is closed to further writes")
    if record.submitted_at is not None or record.state not in OPEN_FOR_WRITES_STATES:
        raise AlreadySubmittedError(
            f"case {record.case_id} was submitted; its values change only "
            "through an answer to a question from FCRM"
        )
