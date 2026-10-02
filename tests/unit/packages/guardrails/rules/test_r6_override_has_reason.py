"""Rule 6: a changed value carries a reason. Example tests use the demo's
CTL-T01 override (7.6 -> 4.5, "audit predates the corridor arrangement")."""

from __future__ import annotations

from decimal import Decimal

from fcrm_contracts.api.case_analyst import OverrideReviewItemRequest, ReviewItem
from fcrm_contracts.enums import ItemKind, ItemStatus
from fcrm_guardrails.rules.r6_override_has_reason import (
    OverrideCheck,
    check,
    values_differ,
)
from fcrm_guardrails.thresholds import OVERRIDE_REASON_MIN_CHARS
from hypothesis import given
from hypothesis import strategies as st

DEMO_REASON = "audit predates the corridor arrangement"
CTL_T01 = ReviewItem(
    item_id="itm_ctl_t01",
    kind=ItemKind.CONTROL,
    status=ItemStatus.OVERRIDDEN,
    proposed_value={"control_id": "CTL-T01", "rating": 7.6},
    confirmed_value={"control_id": "CTL-T01", "rating": 4.5},
    override_reason=DEMO_REASON,
)

VALUES = st.one_of(
    st.integers(-100, 100),
    st.floats(allow_nan=False, allow_infinity=False, width=32),
    st.text(max_size=8),
    st.dictionaries(
        st.sampled_from(["rating", "control_id"]), st.integers(0, 10), max_size=2
    ),
)
BLANKS = st.sampled_from([None, "", "   ", "\n\t"])
GOOD_REASONS = st.text(min_size=OVERRIDE_REASON_MIN_CHARS, max_size=80).filter(
    lambda r: len(r.strip()) >= OVERRIDE_REASON_MIN_CHARS
)


def test_check_demo_override_with_reason_passes() -> None:
    result = check(CTL_T01)
    assert result.ok
    assert result.rule_id == "R-6"


def test_check_demo_override_without_reason_fails() -> None:
    result = check(CTL_T01.model_copy(update={"override_reason": None}))
    assert not result.ok
    assert result.detail == "value changed without a reason"


def test_check_too_short_reason_fails() -> None:
    assert not check(CTL_T01.model_copy(update={"override_reason": "old"})).ok


def test_check_reason_copied_from_agent_rationale_fails() -> None:
    override = OverrideCheck(
        proposed_value=7.6,
        confirmed_value=4.5,
        reason="  Audit of domestic operations, 2023 ",
        proposed_rationale="audit of domestic operations, 2023",
    )
    assert not check(override).ok


def test_check_unchanged_confirmation_needs_no_reason() -> None:
    assert check(OverrideCheck(proposed_value=8.4, confirmed_value=8.40)).ok
    assert check(OverrideCheck(proposed_value={"r": 7}, confirmed_value={"r": 7.0})).ok


def test_from_review_item_with_incoming_request() -> None:
    request = OverrideReviewItemRequest(confirmed_value={"rating": 4.5}, reason="")
    pending = CTL_T01.model_copy(
        update={"confirmed_value": None, "override_reason": None}
    )
    assert not check(OverrideCheck.from_review_item(pending, request)).ok


def test_values_differ_canonicalises_numbers() -> None:
    assert not values_differ(7.6, Decimal("7.60"))
    assert not values_differ([1, 2.0], [1.0, 2])
    assert values_differ(7.6, 4.5)
    assert values_differ(True, 1)  # a bool is not a rating


@given(VALUES, VALUES, BLANKS)
def test_property_changed_value_without_reason_always_fails(
    a: object, b: object, blank: str | None
) -> None:
    result = check(OverrideCheck(proposed_value=a, confirmed_value=b, reason=blank))
    assert result.ok is (not values_differ(a, b))


@given(VALUES, st.one_of(BLANKS, GOOD_REASONS))
def test_property_unchanged_value_always_passes(a: object, reason: str | None) -> None:
    assert check(OverrideCheck(proposed_value=a, confirmed_value=a, reason=reason)).ok


@given(VALUES, VALUES, GOOD_REASONS)
def test_property_changed_value_with_adequate_reason_passes(
    a: object, b: object, reason: str
) -> None:
    assert check(OverrideCheck(proposed_value=a, confirmed_value=b, reason=reason)).ok
