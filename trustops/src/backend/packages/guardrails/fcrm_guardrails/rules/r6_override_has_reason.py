"""Rule 6: "Override has a reason" (CLAUDE.md product rules: "Overrides
need a reason"; ``OverrideReviewItemRequest.reason``: "required whenever
confirmed_value != proposed_value").

Design B §4.6: "reason stripped length >= 20 and not equal to the proposed
value's rationale". Design A §2.1.2 sets the S1 validator at 10 characters.
The rule uses ``OVERRIDE_REASON_MIN_CHARS`` (10) so it never refuses a
reason S1 already accepted; the conflict is reported to the lead.

The rule only bites when the value actually changed. Values are compared
after canonicalising numbers through ``Decimal(str(x))`` (so ``7.6`` and
``7.60`` are the same rating, and a float's binary noise never invents a
change), recursively through mappings and lists. A confirmation (same
value) needs no reason. A changed value needs a reason that is non-empty
after stripping, at least ``min_chars`` long, and not a copy of the agent's
own rationale for the value being overridden (copying it back is not a
reason to disagree with it).
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any, Final, Optional, Union

from fcrm_contracts.api.case_analyst import OverrideReviewItemRequest, ReviewItem
from pydantic import BaseModel, ConfigDict

from fcrm_guardrails.rules.result import RuleResult, broken, kept
from fcrm_guardrails.thresholds import OVERRIDE_REASON_MIN_CHARS

RULE_ID: Final = "R-6"


class OverrideCheck(BaseModel):
    """The minimal shape of one analyst decision on a proposed value."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    proposed_value: Any
    confirmed_value: Any
    reason: Optional[str] = None
    proposed_rationale: Optional[str] = None

    @classmethod
    def from_review_item(
        cls, item: ReviewItem, request: Optional[OverrideReviewItemRequest] = None
    ) -> OverrideCheck:
        """Build from a stored ``ReviewItem`` (and the incoming request, when
        checking before the write)."""
        if request is not None:
            return cls(
                proposed_value=item.proposed_value,
                confirmed_value=request.confirmed_value,
                reason=request.reason,
            )
        return cls(
            proposed_value=item.proposed_value,
            confirmed_value=item.confirmed_value,
            reason=item.override_reason,
        )


def canonical(value: Any) -> Any:
    """A comparison form of ``value``: numbers become normalised ``Decimal``s,
    bools are tagged so ``True`` never equals ``1``, mappings and sequences
    are canonicalised recursively, everything else is returned as is."""
    if value is None:
        return value
    if isinstance(value, bool):
        return ("bool", value)  # True must not equal the rating 1
    if isinstance(value, (int, float, Decimal)):
        try:
            return Decimal(str(value)).normalize()
        except InvalidOperation:
            return value
    if isinstance(value, Mapping):
        return {str(k): canonical(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [canonical(v) for v in value]
    return value


def values_differ(proposed: Any, confirmed: Any) -> bool:
    """True when the confirmed value is a real change from the proposal."""
    return bool(canonical(proposed) != canonical(confirmed))


def _squash(text: str) -> str:
    return " ".join(text.split()).casefold()


def check(
    override: Union[OverrideCheck, ReviewItem],
    *,
    min_chars: int = OVERRIDE_REASON_MIN_CHARS,
) -> RuleResult:
    """Fail when a changed value has no adequate reason."""
    decision = (
        OverrideCheck.from_review_item(override)
        if isinstance(override, ReviewItem)
        else override
    )
    if not values_differ(decision.proposed_value, decision.confirmed_value):
        return kept(RULE_ID, "value confirmed unchanged; no reason required")
    reason = (decision.reason or "").strip()
    if not reason:
        return broken(RULE_ID, "value changed without a reason")
    if len(reason) < min_chars:
        return broken(
            RULE_ID, f"reason is {len(reason)} characters; at least {min_chars} needed"
        )
    if decision.proposed_rationale and _squash(reason) == _squash(
        decision.proposed_rationale
    ):
        return broken(RULE_ID, "reason repeats the agent's own rationale")
    return kept(RULE_ID, "override carries a reason")
