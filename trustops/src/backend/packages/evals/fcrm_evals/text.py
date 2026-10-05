"""Small text helpers shared by the dataset generators and the metrics."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any, Final

NUMBER_PATTERN: Final = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)*")
"""Tokeniser for numbers in text. Kept identical to Guard 4's
``numbers_grounded`` tokeniser (fcrm_guardrails.output_shape); a unit test
runs both over the same texts so a drift is caught."""

_WORD = re.compile(r"[a-z][a-z'-]{3,}")


def numbers_in(texts: Iterable[str]) -> list[str]:
    """Every number token in ``texts``, thousands separators removed, sorted
    and de-duplicated (the ``allowed_numbers`` label)."""
    found = {
        token.replace(",", "")
        for text in texts
        for token in NUMBER_PATTERN.findall(text)
    }
    return sorted(found)


def strings_in(value: Any) -> list[str]:
    """Every string anywhere inside a JSON-like value (numbers rendered with
    ``str``), in a stable order: mapping keys sorted, lists in order."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        return [s for key in sorted(value) for s in strings_in(value[key])]
    if isinstance(value, (list, tuple)):
        return [s for item in value for s in strings_in(item)]
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return [str(value)]
    return []


def content_words(text: str) -> set[str]:
    """Lower-cased words of four or more letters (a crude content filter)."""
    return set(_WORD.findall(text.casefold()))


def sentences(text: str) -> list[str]:
    """``text`` split on sentence-ending punctuation; empty parts dropped."""
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]
