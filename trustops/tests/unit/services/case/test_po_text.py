"""``fcrm_case.po_text``: the checks on the words a product owner types."""

from __future__ import annotations

import pytest
from fcrm_case.po_text import (
    DEFAULT_CORRECTION_REASON,
    DISCARD_REASON_MIN_CHARS,
    clean,
    reject_reserved_terms,
    require_reason,
)
from fcrm_common.errors import ValidationError
from fcrm_guardrails.thresholds import OVERRIDE_REASON_MIN_CHARS


def test_clean_trims_and_none_is_nothing() -> None:
    assert clean("  hello  ") == "hello"
    assert clean(None) == ""
    assert clean("   ") == ""


@pytest.mark.parametrize(
    "text",
    [
        "Q1 2027",
        "About 62,000 per month by month 6",
        "bandwidth for the partner is fine",
        "the vendor provided a scorecard template",
        "Bank deposit; cash pickup at partner agents",
        "CNBV-TR-2041-88",
    ],
)
def test_ordinary_business_text_is_accepted(text: str) -> None:
    reject_reserved_terms(text, "The value", loc="body.value")


@pytest.mark.parametrize(
    ("text", "word"),
    [
        ("Band 3 launch", "band"),
        ("credit scoring model", "score"),
        ("our rating is A", "rating"),
        ("the critic said no", "critic"),
        ("see CTL-T01", "control id"),
        ("Ｂａｎｄ 2", "band"),
    ],
)
def test_a_reserved_word_is_refused_with_the_word_named(text: str, word: str) -> None:
    with pytest.raises(ValidationError) as caught:
        reject_reserved_terms(text, "Your answer", loc="body.answers")
    assert word in caught.value.detail
    assert "Your answer" in caught.value.detail
    assert caught.value.errors == [
        {"loc": "body.answers", "msg": f"contains a reserved word ({word})"}
    ]


def test_a_reason_is_trimmed_and_returned() -> None:
    assert (
        require_reason("  Launch moved a month  ", "A correction")
        == "Launch moved a month"
    )


@pytest.mark.parametrize(
    "reason", [None, "", "   ", "short", "x" * (OVERRIDE_REASON_MIN_CHARS - 1)]
)
def test_a_missing_or_short_reason_is_refused(reason: str | None) -> None:
    with pytest.raises(ValidationError) as caught:
        require_reason(reason, "Removing a change type")
    assert (
        f"at least {OVERRIDE_REASON_MIN_CHARS} characters (rule 6)"
        in caught.value.detail
    )


def test_a_longer_minimum_is_honoured_and_never_below_the_rule() -> None:
    with pytest.raises(ValidationError, match="at least 20"):
        require_reason(
            "x" * 19, "Discarding a draft", min_chars=DISCARD_REASON_MIN_CHARS
        )
    assert require_reason(
        "x" * 20, "Discarding a draft", min_chars=DISCARD_REASON_MIN_CHARS
    )
    with pytest.raises(ValidationError, match="at least 10"):
        require_reason("x" * 9, "Anything", min_chars=1)


def test_a_reason_that_names_a_reserved_word_is_refused() -> None:
    with pytest.raises(ValidationError, match="reserves"):
        require_reason("The rating changed the plan", "A correction")


def test_the_default_correction_reason_satisfies_rule_6() -> None:
    assert len(DEFAULT_CORRECTION_REASON) >= OVERRIDE_REASON_MIN_CHARS
    assert (
        require_reason(DEFAULT_CORRECTION_REASON, "A correction")
        == DEFAULT_CORRECTION_REASON
    )
