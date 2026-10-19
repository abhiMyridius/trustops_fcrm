"""``fcrm_case.domain.answer_quality``: an owner's answer that cannot be a real
value is named for the analyst; a short but real one never is."""

from __future__ import annotations

from typing import Optional

import pytest
from fcrm_case.domain.answer_quality import answer_problem


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        # Seen live on FCRM-2026-0152 (19 Oct): F01 and F22.
        ("ertert", "repeats the same few letters"),
        ("erterte", "repeats the same few letters"),
        ("asdasd", "repeats the same few letters"),
        ("aaaaa", "repeats the same few letters"),
        ("ABABAB", "repeats the same few letters"),
        ("test", "looks like placeholder text"),
        ("TBD", "looks like placeholder text"),
        ("Lorem ipsum", "looks like placeholder text"),
        ("", "is blank"),
        ("   ", "is blank"),
        ("...", "is blank"),
        (None, "is blank"),
    ],
)
def test_an_answer_that_cannot_be_real_is_named(
    value: Optional[str], reason: str
) -> None:
    assert answer_problem(value) == reason


@pytest.mark.parametrize(
    "value",
    [
        "No",
        "N/A",
        "None",
        "PH",
        "$48M year 1",
        "Priya Nair",
        "Licensed by BSP as a remittance agent",
        "papa",
        "Anna",
        "2026-11-30",
        "ertert ertert",  # more than one word: left to the analyst
    ],
)
def test_a_short_or_unusual_real_answer_is_left_alone(value: str) -> None:
    assert answer_problem(value) is None
