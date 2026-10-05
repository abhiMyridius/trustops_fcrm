"""S1's analyst routes for closing and conditions, in the web's shapes
(``src/frontend/src/api/types/analyst.ts``: ``ConditionAnalyst``; the delegated
close body is what ``useDelegatedClose`` posts).

* ``POST /cases/{id}/close``: a Band 1-2 analyst closes under delegated
  authority (R1). S1 only *requests* it (``case.close_requested``); H1 checks
  Guard 5 and S5 records the outcome, and the case is closed when S1 receives
  the outcome back.
* ``GET /cases/{id}/conditions`` and ``PATCH /conditions/{id}``: the analyst
  tracks the conditions an approval activated.

Nothing here carries a score or a vote. ``from`` is a Python keyword, so that
field is ``from_`` with the alias ``from`` and always serialises by alias.
"""

from __future__ import annotations

from typing import Final, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from fcrm_contracts.api.case_internal_outcome import REASON_MIN_CHARS
from fcrm_contracts.enums import Outcome

#: Changing a condition's status is a human decision and says why (the web's
#: ``REASON_MIN``, the same bar the committee's reasons meet).
STATUS_REASON_MIN_CHARS: Final = 20

ConditionStatus = Literal["open", "scheduled", "evidence_submitted", "closed"]

#: What an analyst may move a condition to, by its current status. Only the PO's
#: evidence route sets ``evidence_submitted``; ``closed`` is terminal.
ANALYST_CONDITION_MOVES: Final[dict[str, frozenset[str]]] = {
    "open": frozenset({"scheduled", "closed"}),
    "scheduled": frozenset({"open", "closed"}),
    "evidence_submitted": frozenset({"open", "closed"}),
    "closed": frozenset(),
}


class CloseCaseRequest(BaseModel):
    """``POST /cases/{id}/close``: the analyst's delegated close.

    ``plain_summary`` is what the PO reads, so it must be in business words;
    the route refuses one that names a score, band or rating.
    """

    model_config = ConfigDict(extra="forbid")

    outcome: Outcome
    reason: str
    plain_summary: str = Field(min_length=1, max_length=2000)

    @field_validator("reason")
    @classmethod
    def _reason_says_something(cls, value: str) -> str:
        text = value.strip()
        if len(text) < REASON_MIN_CHARS:
            raise ValueError(
                f"a delegated close needs a reason of at least {REASON_MIN_CHARS} "
                "characters"
            )
        return text

    @field_validator("plain_summary")
    @classmethod
    def _summary_is_not_blank(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("a delegated close needs a plain summary for the owner")
        return text


class CloseRequested(BaseModel):
    """What the close route answers (202): the request was recorded and sent to
    H1. The case is closed when the outcome comes back, not before.
    """

    model_config = ConfigDict(extra="forbid")

    case_id: str
    outcome: Outcome
    requested: bool = True
    replayed: bool = False


class ConditionAnalyst(BaseModel):
    """``ConditionAnalyst`` in ``analyst.ts``."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str
    text: str
    owner: str
    due: str
    status: ConditionStatus
    from_: str = Field(alias="from")
    due_date: Optional[str] = None
    note: Optional[str] = None


class ConditionPatch(BaseModel):
    """``PATCH /conditions/{id}``: the analyst updates a condition's status
    and/or records a note on the evidence they reviewed. A status change needs
    a ``reason`` of at least :data:`STATUS_REASON_MIN_CHARS` characters.
    """

    model_config = ConfigDict(extra="forbid")

    status: Optional[ConditionStatus] = None
    reason: Optional[str] = None
    note: Optional[str] = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _rules(self) -> ConditionPatch:
        if self.status is None and self.note is None:
            raise ValueError("a condition update changes the status or adds a note")
        if self.status is not None:
            if self.status == "evidence_submitted":
                raise ValueError(
                    "only the owner's evidence submission sets evidence_submitted"
                )
            reason = (self.reason or "").strip()
            if len(reason) < STATUS_REASON_MIN_CHARS:
                raise ValueError(
                    "a status change needs a reason of at least "
                    f"{STATUS_REASON_MIN_CHARS} characters"
                )
            self.reason = reason
        return self
