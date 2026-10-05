"""Layout-based heading inference: font-size ratio and numbered lines.

Scope & functionality
---------------------
Neither PDF engine gets real style information, so headings are inferred:

* ``HeadingInferencer`` (pdfplumber only — it has font sizes) compares a
  line's size against the document's body-text mode: ratio >= 1.6 -> H1,
  >= 1.3 -> H2, >= 1.12 (or bold) with <= 12 words and no terminal
  punctuation -> H3. A line that doesn't qualify by size falls through to
  the numbered check below.
* ``classify_numbered`` (used by both engines) recognises a line-leading
  numbered heading (``"1. Introduction"``, ``"8.1 Audit and Logging"``) via
  the shared ``NUMBERED_HEADING_RE`` and turns it into a ``Heading`` whose
  level is the dotted depth of the number (capped to 6), independent of
  font size — pypdf's fallback path (no font sizes at all) uses only this.

Both paths set ``inferred=True`` and ``source`` to ``"layout:size"`` or
``"layout:numbered"`` so the extractor can emit one ``inferred_headings``
warning per source actually used, not per heading.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from evoq_ingestion.core.markdown_scan import NUMBERED_HEADING_RE
from evoq_ingestion.model.blocks import Heading

if TYPE_CHECKING:
    from evoq_ingestion.extractors.pdf.lines import PdfLine

_H3_MAX_WORDS = 12
_NUMBERED_MAX_WORDS = 14
_TERMINAL_PUNCTUATION = (".", ",", ";")
SOURCE_SIZE = "layout:size"
SOURCE_NUMBERED = "layout:numbered"


def dotted_depth(text: str) -> int:
    """Number of dot-separated numeric components in the leading token (min 1)."""
    token = text.split(None, 1)[0].rstrip(".")
    if not token:
        return 1
    return token.count(".") + 1


def classify_numbered(text: str, *, max_words: int = _NUMBERED_MAX_WORDS) -> Heading | None:
    """A line-leading numbered heading (``"1.1 Purpose"``), or ``None``."""
    stripped = text.strip()
    if not stripped or not NUMBERED_HEADING_RE.match(stripped):
        return None
    if len(stripped.split()) > max_words:
        return None
    if stripped[-1:] in _TERMINAL_PUNCTUATION:
        return None
    level = min(max(dotted_depth(stripped), 1), 6)
    return Heading(level=level, text=stripped, inferred=True, source=SOURCE_NUMBERED)


class HeadingInferencer:
    """Classify a ``PdfLine`` as a heading using the document's body font size."""

    def __init__(self, body_size: float) -> None:
        """Construct against the document's body-text size mode (never zero)."""
        self._body_size = body_size if body_size > 0 else 10.0

    def classify(self, line: PdfLine) -> Heading | None:
        """A ``Heading`` when *line* reads as one by size, boldness or numbering."""
        text = line.text.strip()
        if not text:
            return None
        ratio = line.size / self._body_size
        words = text.split()
        ends_with_terminal = text[-1:] in _TERMINAL_PUNCTUATION
        if ratio >= 1.6:
            return Heading(level=1, text=text, inferred=True, source=SOURCE_SIZE)
        if ratio >= 1.3:
            return Heading(level=2, text=text, inferred=True, source=SOURCE_SIZE)
        if (ratio >= 1.12 or line.bold) and len(words) <= _H3_MAX_WORDS and not ends_with_terminal:
            return Heading(level=3, text=text, inferred=True, source=SOURCE_SIZE)
        return classify_numbered(text)
