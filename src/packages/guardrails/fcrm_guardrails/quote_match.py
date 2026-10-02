"""The verbatim-quote matcher Guard 4 and rule 8 both use.

"Policy is quoted, not generated": an agent's ``Claim.quote`` must be a
contiguous substring of the text its ``source_ref`` points to. Design B
§4.5 step 3 fixes the algorithm; this module is its only implementation.

Normalisation, applied identically to the quote and the source
(:func:`normalise`):

1. Unicode NFKC (folds compatibility forms: ``…`` becomes ``...``, a
   non-breaking space becomes a space, full-width letters become ASCII).
2. Zero-width characters and the soft hyphen are removed.
3. Curly single/double quotes and primes map to ``'`` / ``"``; every
   hyphen/dash variant (en dash, em dash, minus sign, non-breaking hyphen)
   maps to ``-``.
4. ``§`` maps to ``section `` and ``§§`` to ``sections ``; the capitalised
   words ``Section``/``Sections`` map to lower case so ``§3.1`` and
   ``Section 3.1`` compare equal. This is the only case folding done
   before the exact comparison, and it touches only that one word.
5. Every run of whitespace (including line breaks) collapses to one space;
   the result is stripped.

The quote additionally has leading/trailing ellipses and wrapping quotation
marks removed (:func:`normalise_quote`): agents excerpt mid-sentence, as the
seed's own clause quotes do ("…banks should assess…"). An ellipsis *inside*
a quote means the agent elided text, so the quote is not contiguous and is
correctly refused.

Match kinds and how callers must treat them (the threshold choice):

- ``EXACT``: case-sensitive contiguous substring after normalisation. The
  only kind that verifies a claim.
- ``FUZZY``: found only after Unicode case folding of both sides. Reported
  so a human sees "the words are there, the casing is not", but it is
  **not** a pass: Guard 4 counts it as an unverified claim (retry on the
  fallback model, then drop) and rule 8 fails it. Case is meaningful in
  policy text ("High" is a defined jurisdiction rating) and a model that
  re-cases a quote is already rewriting it.
- ``NONE``: not found, or the quote is shorter than ``QUOTE_MIN_CHARS``
  after normalisation (a very short quote proves nothing).

There is deliberately no edit-distance or token-overlap tier: a plausible
paraphrase is exactly what this check exists to catch.
"""

from __future__ import annotations

import re
import unicodedata
from enum import StrEnum
from typing import Optional

from pydantic import BaseModel, ConfigDict

from fcrm_guardrails.thresholds import QUOTE_LINE_TOLERANCE, QUOTE_MIN_CHARS

_ZERO_WIDTH = re.compile("[​‌‍⁠﻿­]")
_WHITESPACE = re.compile(r"\s+")
_SECTION_SIGNS = re.compile(r"§§\s*")
_SECTION_SIGN = re.compile(r"§\s*")
_SECTION_WORD = re.compile(r"\bSection(s?)\b")
_EDGE_ELLIPSIS = re.compile(r"^(?:\s*\.{3})+|(?:\.{3}\s*)+$")
_WRAPPING_QUOTES = "\"'"

_CHAR_MAP = str.maketrans(
    {
        "‘": "'",  # left single quotation mark
        "’": "'",  # right single quotation mark / apostrophe
        "‚": "'",  # single low-9 quotation mark
        "‛": "'",  # single high-reversed-9 quotation mark
        "′": "'",  # prime
        "“": '"',  # left double quotation mark
        "”": '"',  # right double quotation mark
        "„": '"',  # double low-9 quotation mark
        "‟": '"',  # double high-reversed-9 quotation mark
        "″": '"',  # double prime
        "«": '"',  # left guillemet
        "»": '"',  # right guillemet
        "‐": "-",  # hyphen
        "‑": "-",  # non-breaking hyphen
        "‒": "-",  # figure dash
        "–": "-",  # en dash
        "—": "-",  # em dash
        "―": "-",  # horizontal bar
        "−": "-",  # minus sign
    }
)


class MatchKind(StrEnum):
    """How a quote was found in its source (see the module docstring)."""

    EXACT = "exact"
    FUZZY = "fuzzy"
    NONE = "none"


class QuoteMatch(BaseModel):
    """The outcome of one verbatim check. ``start``/``end`` are offsets into
    the *normalised* source text when found."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: MatchKind
    normalised_quote: str
    start: Optional[int] = None
    end: Optional[int] = None
    reason: Optional[str] = None

    @property
    def verified(self) -> bool:
        """Only an exact match verifies a claim; fuzzy is a warning, not a pass."""
        return self.kind is MatchKind.EXACT


def normalise(text: str) -> str:
    """Apply steps 1-5 of the module docstring's normalisation to ``text``.

    Idempotent: ``normalise(normalise(t)) == normalise(t)``.
    """
    out = unicodedata.normalize("NFKC", text)
    out = _ZERO_WIDTH.sub("", out)
    out = out.translate(_CHAR_MAP)
    out = _SECTION_SIGNS.sub("sections ", out)
    out = _SECTION_SIGN.sub("section ", out)
    out = _SECTION_WORD.sub(r"section\1", out)
    return _WHITESPACE.sub(" ", out).strip()


def normalise_quote(quote: str) -> str:
    """Normalise a quote and strip edge ellipses and wrapping quotation marks,
    repeatedly, so ``"…text…"`` and ``... text ...`` both reduce to ``text``.
    """
    out = normalise(quote)
    previous = None
    while previous != out:
        previous = out
        out = _EDGE_ELLIPSIS.sub("", out).strip()
        if len(out) >= 2 and out[0] == out[-1] and out[0] in _WRAPPING_QUOTES:
            out = out[1:-1].strip()
    return out


def match_quote(
    quote: str, source_text: str, *, min_chars: int = QUOTE_MIN_CHARS
) -> QuoteMatch:
    """Look for ``quote`` verbatim in ``source_text``.

    Returns ``EXACT`` for a case-sensitive contiguous substring after
    normalisation, ``FUZZY`` when only a case-folded comparison finds it,
    and ``NONE`` otherwise (including a quote shorter than ``min_chars``).
    """
    needle = normalise_quote(quote)
    if len(needle) < min_chars:
        return QuoteMatch(
            kind=MatchKind.NONE,
            normalised_quote=needle,
            reason=f"quote shorter than {min_chars} characters after normalisation",
        )
    haystack = normalise(source_text)
    start = haystack.find(needle)
    if start >= 0:
        return QuoteMatch(
            kind=MatchKind.EXACT,
            normalised_quote=needle,
            start=start,
            end=start + len(needle),
        )
    folded_start = haystack.casefold().find(needle.casefold())
    if folded_start >= 0:
        return QuoteMatch(
            kind=MatchKind.FUZZY,
            normalised_quote=needle,
            start=folded_start,
            end=folded_start + len(needle),
            reason="found only when case is ignored; not a verbatim quote",
        )
    return QuoteMatch(
        kind=MatchKind.NONE,
        normalised_quote=needle,
        reason="quote not found verbatim in the cited source",
    )


def line_span(
    page_text: str,
    line_start: int,
    line_end: int,
    *,
    tolerance: int = QUOTE_LINE_TOLERANCE,
) -> str:
    """The text of lines ``line_start..line_end`` (1-based, inclusive) of a
    page, widened by ``tolerance`` lines either side and clipped to the page
    (design B §4.5: "document span ± quote_line_tolerance lines").

    Raises:
        ValueError: when the span is empty or lies outside the page; Guard 4
            reports that as an unresolved source, never as a quote miss.
    """
    lines = page_text.splitlines()
    if line_start < 1 or line_end < line_start or line_end > len(lines):
        msg = f"lines {line_start}-{line_end} are outside a {len(lines)}-line page"
        raise ValueError(msg)
    first = max(1, line_start - tolerance)
    last = min(len(lines), line_end + tolerance)
    return "\n".join(lines[first - 1 : last])
