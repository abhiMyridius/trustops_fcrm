"""S1's analyst write routes, in the shapes the web posts (ADR-30: the web's route
and body names are the contract; the bodies are what ``analystQueries.ts``
sends and the shapes are ``types/analyst.ts``).

Every human gate of scenes 8-16 is reachable through these bodies. A body that
decides a gate is *stored in S1 first* and only then announced to Caseflow as
``gate.decided`` (``fcrm_case.analyst_write_store``), so H1 reads the decided
value from S1's own item. Nothing here carries an agent's proposal: the analyst
supplies a decision, a reason and, for an override, the replacement input; the
score is S2's answer to those inputs, never a field of a request or response in
this module.

Reasons: an override, a removal, an adjustment and an explanation each need a
reason of at least :data:`REASON_MIN_CHARS` characters (rule 6; Guard 7 asks for
10, the web's own bar is 20). A bulk body (a JSON array) is refused by the model
itself: there is no route that decides more than one item (rule 2).
"""

from __future__ import annotations

from typing import Final, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from fcrm_contracts.enums import ChangeType, ItemStatus, Outcome

#: The shortest reason a decision that changes or removes something may carry.
REASON_MIN_CHARS: Final = 20
#: Longest free text any of these bodies takes (a reason, a note, a question).
MAX_TEXT_CHARS: Final = 2000

ItemDecisionName = Literal[
    "accept", "confirm", "remove", "override", "explain", "adjust"
]
#: The decisions that need a reason.
REASON_REQUIRED_DECISIONS: Final = frozenset(
    {"remove", "override", "explain", "adjust"}
)


def _stripped(value: Optional[str]) -> Optional[str]:
    """``value`` without surrounding whitespace; ``None`` stays ``None``."""
    return value.strip() if value is not None else None


class ItemDecisionBody(BaseModel):
    """``POST /cases/{id}/items/{item_id}``: one decision on one item.

    ``value`` is a control's replacement rating (0 to 10); a factor's
    replacement is the pair ``likelihood`` and ``impact`` (1 to 5 each, an
    extension the web does not send yet). Which decisions an item kind accepts
    is the service's rule (clauses are accepted or removed, controls confirmed,
    overridden or removed, ratings confirmed or overridden, critic findings
    explained), because the kind is on the stored item, not in the body.
    """

    model_config = ConfigDict(extra="forbid")

    decision: ItemDecisionName
    value: Optional[float] = Field(default=None, ge=0.0, le=10.0)
    likelihood: Optional[int] = Field(default=None, ge=1, le=5)
    impact: Optional[int] = Field(default=None, ge=1, le=5)
    reason: Optional[str] = Field(default=None, max_length=MAX_TEXT_CHARS)

    @field_validator("reason")
    @classmethod
    def _trim_reason(cls, value: Optional[str]) -> Optional[str]:
        return _stripped(value) or None

    @model_validator(mode="after")
    def _reason_when_required(self) -> ItemDecisionBody:
        if self.decision in REASON_REQUIRED_DECISIONS and (
            self.reason is None or len(self.reason) < REASON_MIN_CHARS
        ):
            raise ValueError(
                f"{self.decision} needs a reason of at least "
                f"{REASON_MIN_CHARS} characters"
            )
        return self


class ItemDecisionResult(BaseModel):
    """What an item decision answers: the item, where it stands now and whether
    this call changed nothing because the same decision was already recorded.
    """

    model_config = ConfigDict(extra="forbid")

    ok: bool = True
    item_id: str
    status: ItemStatus
    replayed: bool = False


AckKind = Literal["boundary", "band_change"]


class AcknowledgeBody(BaseModel):
    """``POST /cases/{id}/acknowledge``: R3, the analyst acknowledges a boundary
    flag or a band change. ``note`` says what was checked.
    """

    model_config = ConfigDict(extra="forbid")

    kind: AckKind
    note: Optional[str] = Field(default=None, max_length=MAX_TEXT_CHARS)

    @field_validator("note")
    @classmethod
    def _trim_note(cls, value: Optional[str]) -> Optional[str]:
        return _stripped(value) or None


class AcknowledgeResult(BaseModel):
    """What an acknowledgement answers (202): how many open gates of that kind
    it decided; zero with ``replayed`` when they were all decided before.
    """

    model_config = ConfigDict(extra="forbid")

    kind: AckKind
    acknowledged: int
    replayed: bool = False


class FastLaneBody(BaseModel):
    """``POST /cases/{id}/screening``: the analyst confirms or declines the fast
    lane the screening agent proposed (R4). Declining needs a reason.
    """

    model_config = ConfigDict(extra="forbid")

    decision: Literal["confirm_fast_lane", "decline"]
    reason: Optional[str] = Field(default=None, max_length=MAX_TEXT_CHARS)

    @field_validator("reason")
    @classmethod
    def _trim_reason(cls, value: Optional[str]) -> Optional[str]:
        return _stripped(value) or None

    @model_validator(mode="after")
    def _decline_says_why(self) -> FastLaneBody:
        if self.decision == "decline" and (
            self.reason is None or len(self.reason) < REASON_MIN_CHARS
        ):
            raise ValueError(
                f"declining the fast lane needs a reason of at least "
                f"{REASON_MIN_CHARS} characters"
            )
        return self


class GateWriteResult(BaseModel):
    """What a gate-deciding route without a richer answer returns (202)."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    gate_type: str
    decision: str
    replayed: bool = False


# ── completeness, clarification rounds ──────────────────────────────────────────


class RoundQuestion(BaseModel):
    """One question of the round, as the web's composer posts it (``keep``
    false drops it). ``anchor`` is the field or control it is about.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)
    anchor: Optional[str] = Field(default=None, max_length=64)
    keep: bool = True

    @field_validator("text")
    @classmethod
    def _trim_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("a question needs words")
        return text


class CompletenessBody(BaseModel):
    """``POST /cases/{id}/completeness``: ask the owner one round of questions or
    proceed to review. ``questions`` are only read for ``ask``; without them the
    questions the narrative agent drafted (and kept) are asked.
    """

    model_config = ConfigDict(extra="forbid")

    decision: Literal["ask", "proceed"]
    questions: Optional[list[RoundQuestion]] = Field(default=None, max_length=50)


class RoundBody(BaseModel):
    """``POST /cases/{id}/clarifications``: open the round with these questions."""

    model_config = ConfigDict(extra="forbid")

    questions: list[RoundQuestion] = Field(min_length=1, max_length=50)


class ResolveThreadBody(BaseModel):
    """``POST /cases/{id}/clarifications/{thread_id}/resolve``: nothing to say
    but, optionally, why it is resolved.
    """

    model_config = ConfigDict(extra="forbid")

    note: Optional[str] = Field(default=None, max_length=MAX_TEXT_CHARS)


# ── the analyst's other review actions ──────────────────────────────────────────


class ChangeTypeBody(BaseModel):
    """``POST /cases/{id}/change-types``: the analyst adds a change type (adds
    only, D-03). The reason is recorded and needs 20 characters.
    """

    model_config = ConfigDict(extra="forbid")

    change_type: ChangeType
    reason: str

    @field_validator("reason")
    @classmethod
    def _reason_says_something(cls, value: str) -> str:
        text = value.strip()
        if len(text) < REASON_MIN_CHARS:
            raise ValueError(
                f"a re-tag needs a reason of at least {REASON_MIN_CHARS} characters"
            )
        return text


class ChangeTypeResult(BaseModel):
    """What a re-tag answers: the case's change types and required-field count
    afterwards, and whether it was already tagged (nothing changed).
    """

    model_config = ConfigDict(extra="forbid")

    case_id: str
    change_types: list[ChangeType]
    required_fields: int
    replayed: bool = False


class RerunBody(BaseModel):
    """``POST /cases/{id}/runs``: re-run research and assessment, with a reason."""

    model_config = ConfigDict(extra="forbid")

    reason: str
    scope: Literal["changed_fields", "full"] = "full"

    @field_validator("reason")
    @classmethod
    def _reason_says_something(cls, value: str) -> str:
        text = value.strip()
        if len(text) < REASON_MIN_CHARS:
            raise ValueError(
                f"a re-run needs a reason of at least {REASON_MIN_CHARS} characters"
            )
        return text


class RerunResult(BaseModel):
    """The re-run was requested (202); H1 starts it."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    scope: str
    requested: bool = True
    replayed: bool = False


class PlannedControlBody(BaseModel):
    """``POST /cases/{id}/controls/planned``: a control the business plans but
    has not built. It is recorded and shown and never scored (rule 7).
    """

    model_config = ConfigDict(extra="forbid")

    control_id: Optional[str] = Field(default=None, max_length=32)
    description: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)
    owner: str = Field(min_length=1, max_length=200)
    due: str = Field(min_length=1, max_length=64)

    @field_validator("description", "owner", "due")
    @classmethod
    def _trim(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("this field cannot be blank")
        return text


class GapBody(BaseModel):
    """``POST /cases/{id}/gaps``: the analyst searched the corpus by hand and
    found nothing for something the case needs.
    """

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)

    @field_validator("text")
    @classmethod
    def _trim_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("a gap needs words")
        return text


class RecordedWrite(BaseModel):
    """What recording a planned control or a gap answers (201): its id and
    whether the same one was already recorded.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    case_id: str
    replayed: bool = False


# ── the recommendation ──────────────────────────────────────────────────────────


class RecommendationEditBody(BaseModel):
    """``PUT /cases/{id}/recommendation``: what the analyst changed in the draft.
    Each field is optional; an absent one is not edited (the draft stands). The
    analyst's own edits need no reason; the lock and the unlock are separate
    gates.
    """

    model_config = ConfigDict(extra="forbid")

    narrative: Optional[str] = Field(default=None, max_length=20000)
    outcome: Optional[Outcome] = None
    business_summary: Optional[str] = Field(default=None, max_length=MAX_TEXT_CHARS)

    @model_validator(mode="after")
    def _says_something(self) -> RecommendationEditBody:
        if (
            self.narrative is None
            and self.outcome is None
            and self.business_summary is None
        ):
            raise ValueError(
                "an edit changes the narrative, the outcome or the summary"
            )
        return self


class UnlockBody(BaseModel):
    """``POST /cases/{id}/recommendation/unlock``: reopen the numbers, with the
    reason that goes on the chain (R6).
    """

    model_config = ConfigDict(extra="forbid")

    reason: str

    @field_validator("reason")
    @classmethod
    def _reason_says_something(cls, value: str) -> str:
        text = value.strip()
        if len(text) < REASON_MIN_CHARS:
            raise ValueError(
                f"an unlock needs a reason of at least {REASON_MIN_CHARS} characters"
            )
        return text


class LockBody(BaseModel):
    """``POST /cases/{id}/recommendation/lock``: nothing to say."""

    model_config = ConfigDict(extra="forbid")
