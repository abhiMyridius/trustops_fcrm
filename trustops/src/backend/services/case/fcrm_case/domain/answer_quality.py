"""Whether an answer the owner typed looks like a real answer.

An owner can answer a gap question with anything, and the answer then counts as
a filled field: on 19 October case FCRM-2026-0152 showed "10 of 10 fields" with
"ertert" as its change owner and "erterte" as its licensing status, and nothing
told the analyst. :func:`answer_problem` is the deterministic check behind the
analyst's "Answer may not be valid" flag.

It is deliberately conservative: it only names answers that cannot be a real
value, so a short but meaningful answer ("No", "N/A", "None", "PH", "$48M") is
never flagged. The field set carries no field types, so nothing here guesses
what kind of value a field expects.
"""

from __future__ import annotations

import re
from typing import Final, Optional

PLACEHOLDERS: Final = frozenset(
    {
        "test",
        "testing",
        "tbd",
        "tba",
        "todo",
        "xxx",
        "asdf",
        "asdfg",
        "asdfgh",
        "qwerty",
        "lorem",
        "lorem ipsum",
        "dummy",
        "foo",
        "bar",
        "foobar",
        "placeholder",
    }
)
"""Whole answers that are filler, compared case-insensitively."""

REPEAT_MIN_LETTERS: Final = 5
"""A one-word answer this long or longer made of one short pattern repeated
("ertert", "erterte", "asdasd", "aaaaa") is keyboard filler; shorter ones are
left alone, so a word such as "papa" is not flagged."""

REPEAT_MAX_PATTERN: Final = 3
"""The longest repeated pattern that counts as filler."""

_LETTERS: Final = re.compile(r"[^a-z]")
_MEANINGFUL: Final = re.compile(r"[A-Za-z0-9]")


def _repeats_a_short_pattern(word: str) -> bool:
    """Whether ``word`` (letters only) is one pattern of at most
    :data:`REPEAT_MAX_PATTERN` letters repeated, a partial copy at the end
    allowed."""
    if len(word) < REPEAT_MIN_LETTERS:
        return False
    for size in range(1, REPEAT_MAX_PATTERN + 1):
        pattern = word[:size]
        repeated = pattern * (len(word) // size + 1)
        if repeated[: len(word)] == word:
            return True
    return False


def answer_problem(value: Optional[str]) -> Optional[str]:
    """Why ``value`` does not look like a real answer, or ``None`` when it does.

    Returns a short reason for the analyst: ``"is blank"``, ``"looks like
    placeholder text"`` or ``"repeats the same few letters"``.
    """
    text = (value or "").strip()
    if not _MEANINGFUL.search(text):
        return "is blank"
    if text.lower() in PLACEHOLDERS:
        return "looks like placeholder text"
    if " " not in text and _repeats_a_short_pattern(_LETTERS.sub("", text.lower())):
        return "repeats the same few letters"
    return None
