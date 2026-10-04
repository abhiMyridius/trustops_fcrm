"""Text normalisation shared by the guards that match text against a list.

Two users with different needs share these primitives:

- :mod:`fcrm_guardrails.quote_match` (verbatim quotes) removes zero-width
  and bidi-control characters before comparing a quote with its source.
- :mod:`fcrm_guardrails.rules.r3_po_no_restricted` (restricted terms in a
  PO-facing payload) additionally folds Unicode compatibility forms,
  collapses whitespace and maps the handful of Cyrillic letters that are
  visually identical to Latin ones, so ``Ｂａｎｄ``, ``Bаnd`` (Cyrillic а)
  and ``B​and`` all read as ``Band`` to the scanner.

The homoglyph fold is deliberately narrow (one-to-one lookalikes only) and
is used for *scanning*, never for quoting: it would be wrong to rewrite a
policy quote, but it is right to refuse to be fooled by one.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final

#: Characters with no width that an attacker (or a copy-paste) can hide
#: inside a word without changing how it renders: zero-width space/joiner/
#: non-joiner (U+200B-D), left-to-right/right-to-left marks (U+200E-F),
#: bidi embedding/override controls (U+202A-E) and isolates (U+2066-9),
#: word joiner and invisible operators (U+2060-4), the BOM (U+FEFF), the
#: soft hyphen (U+00AD), the combining grapheme joiner (U+034F) and the
#: Mongolian vowel separator (U+180E).
ZERO_WIDTH_CHARS: Final[str] = "".join(
    (
        "​‌‍‎‏",
        "‪‫‬‭‮",
        "⁠⁡⁢⁣⁤",
        "⁦⁧⁨⁩",
        "﻿­͏᠎",
    )
)
ZERO_WIDTH: Final[re.Pattern[str]] = re.compile(f"[{ZERO_WIDTH_CHARS}]")

_WHITESPACE = re.compile(r"\s+")

#: Cyrillic letters that are pixel-identical (or nearly so) to a Latin
#: letter in common fonts, mapped to that Latin letter. Lower and upper
#: case are listed separately so the fold never changes case.
_CYRILLIC_TO_LATIN: Final[dict[str, str]] = {
    "а": "a",
    "е": "e",
    "о": "o",
    "р": "p",
    "с": "c",
    "у": "y",
    "х": "x",
    "і": "i",
    "ј": "j",
    "ѕ": "s",
    "һ": "h",
    "ԁ": "d",
    "ԛ": "q",
    "ԝ": "w",
    "ѵ": "v",
    "А": "A",
    "В": "B",
    "Е": "E",
    "К": "K",
    "М": "M",
    "Н": "H",
    "О": "O",
    "Р": "P",
    "С": "C",
    "Т": "T",
    "Х": "X",
    "І": "I",
    "Ј": "J",
    "Ѕ": "S",
    "Ү": "Y",
}
_HOMOGLYPH_TABLE: Final = str.maketrans(_CYRILLIC_TO_LATIN)


def strip_zero_width(text: str) -> str:
    """Remove every :data:`ZERO_WIDTH_CHARS` character from ``text``."""
    return ZERO_WIDTH.sub("", text)


def fold_homoglyphs(text: str) -> str:
    """Map Cyrillic lookalike letters to their Latin twins (scanning only)."""
    return text.translate(_HOMOGLYPH_TABLE)


def normalise_for_scan(text: str) -> str:
    """The restricted-term scanner's view of ``text``: NFKC (fullwidth and
    other compatibility forms become ASCII, NBSP becomes a space), then
    zero-width characters removed, Cyrillic homoglyphs folded, and every
    run of whitespace (including line breaks) collapsed to one space.
    Case is preserved; the patterns are case-insensitive themselves.
    """
    out = unicodedata.normalize("NFKC", text)
    out = strip_zero_width(out)
    out = fold_homoglyphs(out)
    return _WHITESPACE.sub(" ", out).strip()
