"""S1's analyst re-flag routes, in the web's shapes (``src/frontend/src/api/
types/analyst.ts``: ``Reflag``; the acknowledge body is what
``useAcknowledgeReflag`` posts).

* ``GET /reflags``: the watchlist re-flags, newest first, unacknowledged and
  acknowledged alike.
* ``POST /reflags/{id}/acknowledge``: an analyst acknowledges one with a reason
  (at least 20 characters). The decision is stored and ``gate.decided`` is
  emitted in the same transaction; H1 records ``reflag.acknowledged``.
  Acknowledging works on a closed case too: the re-flag is about the list, and a
  closed decision is exactly what a list change may call into question.

Nothing here scores or reopens a case. The web posts the reason as ``note``;
both names are accepted.
"""

from __future__ import annotations

from datetime import datetime
from typing import Final, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: An acknowledgement says what the analyst checked (the web's ``REASON_MIN``).
ACK_REASON_MIN_CHARS: Final = 20
ACK_REASON_MAX_CHARS: Final = 1000


class ReflagAcknowledge(BaseModel):
    """``POST /reflags/{id}/acknowledge``.

    ``reason`` is the contract's name, ``note`` the web's; exactly one need be
    given. ``rerun`` is the analyst's request that research (and assessment,
    where the case's evidence depends on list status) be run again: it is
    recorded with the acknowledgement and never starts anything by itself.
    """

    model_config = ConfigDict(extra="forbid")

    reason: Optional[str] = Field(default=None, max_length=ACK_REASON_MAX_CHARS)
    note: Optional[str] = Field(default=None, max_length=ACK_REASON_MAX_CHARS)
    rerun: bool = True

    @model_validator(mode="after")
    def _a_reason_of_substance(self) -> ReflagAcknowledge:
        text = (self.reason or self.note or "").strip()
        if len(text) < ACK_REASON_MIN_CHARS:
            raise ValueError(
                "acknowledging a re-flag needs a reason of at least "
                f"{ACK_REASON_MIN_CHARS} characters"
            )
        self.reason = text
        self.note = None
        return self


class ReflagOut(BaseModel):
    """One re-flag, the web's ``Reflag`` plus what an analyst needs to judge it."""

    model_config = ConfigDict(extra="forbid")

    id: str
    case_id: str
    case_title: str
    jurisdiction: str
    list_version: str
    fixture: bool
    reason: str
    acknowledged: bool
    list_change: str
    proposed_by: str
    rerun: list[str] = Field(default_factory=list)
    quote: Optional[str] = None
    created_at: datetime
    acknowledged_by: Optional[str] = None
    acknowledged_at: Optional[datetime] = None
    ack_reason: Optional[str] = None
