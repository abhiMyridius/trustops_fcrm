"""S1 internal outcome route: ``PUT /internal/cases/{case_id}/outcome`` (H1 only).

H1 calls it once a decision exists in S5, to record the outcome in S1, activate
its conditions and close the case: all four in one S1 transaction (design A
§2.1 "from ``outcome.set``: stores ``OutcomeRecord`` + activates conditions").
The body is the whole intended state, so the same body twice changes nothing;
the same ``Idempotency-Key`` with a different body is a 409.

The models carry no score, band or vote: what the PO reads later
(``OutcomePO``) is built from the plain summary and the activated conditions,
and nothing here could hold more.
"""

from __future__ import annotations

from datetime import date
from typing import Final, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from fcrm_contracts.enums import Outcome

#: A reason a person gave for a delegated close is never shorter than this
#: (ADR-22: 10 characters, the same bar S5 holds an outcome reason to).
REASON_MIN_CHARS: Final = 10

#: The words S1 and H1 show for an outcome (business register, no numbers).
OUTCOME_LABEL: Final[dict[Outcome, str]] = {
    Outcome.APPROVE: "Approved",
    Outcome.APPROVE_WITH_CONDITIONS: "Approved with conditions",
    Outcome.DEFER: "Deferred",
    Outcome.REJECT: "Rejected",
}


def outcome_label(outcome: Outcome) -> str:
    """The display label of ``outcome``."""
    return OUTCOME_LABEL[outcome]


class ActivatedCondition(BaseModel):
    """One condition that becomes active when the case is approved with
    conditions, as H1 resolved it from the pack the committee approved.

    Attributes:
        condition_id: Stable id H1 derives (``deterministic_id("cnd", case,
            ref)``), so a replayed step activates nothing twice.
        ref: The pack's own label for it (``CND-1``).
        text: What has to happen.
        owner_id: The responsible user id, ``None`` for a team (``FCRM Ops``).
        owner_label: The owner as people read it.
        due_label: When, as the pack worded it (``before go-live``).
        due_date: ``due_label`` resolved to a date by H1 when it can be (the
            case's go-live date, a number of days or months after the decision).
        status: ``open`` for something due now, ``scheduled`` for something due
            later; a condition is never activated as evidenced or closed.
        origin: Where the condition came from (``overridden control CTL-T01``),
            kept for the analyst; the PO shape does not carry it.
    """

    model_config = ConfigDict(extra="forbid")

    condition_id: str = Field(min_length=1, max_length=128)
    ref: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=2000)
    owner_id: Optional[str] = Field(default=None, max_length=128)
    owner_label: str = Field(min_length=1, max_length=200)
    due_label: str = Field(min_length=1, max_length=200)
    due_date: Optional[date] = None
    status: Literal["open", "scheduled"] = "open"
    origin: str = Field(default="", max_length=500)


class OutcomePut(BaseModel):
    """``PUT /internal/cases/{case_id}/outcome``.

    ``committee`` names the pack that was tallied; ``delegated`` (R1, Band 1-2)
    names the analyst and gives a reason. Conditions go with
    ``approve_with_conditions`` only.
    """

    model_config = ConfigDict(extra="forbid")

    outcome: Outcome
    mode: Literal["committee", "delegated"]
    decided_by: str = Field(min_length=1, max_length=128)
    pack_id: Optional[str] = Field(default=None, max_length=128)
    reason: Optional[str] = Field(default=None, max_length=2000)
    plain_summary: Optional[str] = Field(default=None, max_length=2000)
    conditions: list[ActivatedCondition] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def _rules(self) -> OutcomePut:
        if self.mode == "committee" and not self.pack_id:
            raise ValueError("a committee outcome names the pack that was tallied")
        if self.mode == "delegated":
            if self.pack_id:
                raise ValueError("a delegated outcome has no pack")
            if len((self.reason or "").strip()) < REASON_MIN_CHARS:
                raise ValueError(
                    "a delegated outcome needs a reason of at least "
                    f"{REASON_MIN_CHARS} characters"
                )
        if self.conditions and self.outcome is not Outcome.APPROVE_WITH_CONDITIONS:
            raise ValueError("only an approval with conditions activates conditions")
        ids = [c.condition_id for c in self.conditions]
        if len(ids) != len(set(ids)):
            raise ValueError("a condition is listed twice")
        return self


class OutcomePutResult(BaseModel):
    """What S1 answers: that the case is closed under this outcome.

    Attributes:
        replayed: ``True`` when the same outcome had already been recorded (a
            repeated step or ``Idempotency-Key``): nothing was written.
    """

    model_config = ConfigDict(extra="forbid")

    case_id: str
    outcome: Outcome
    mode: Literal["committee", "delegated"]
    closed: bool
    conditions_activated: int
    replayed: bool
