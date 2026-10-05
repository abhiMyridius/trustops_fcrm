"""``FontStats`` — one pass over every line's font size -> the body-text mode.

Scope & functionality
---------------------
Headings are only meaningful relative to the document's body text size, so
one pass collects each line's (already-rounded) font size, weighted by its
character count so long prose lines outweigh short, large-font headings,
across every page before any heading is classified. ``body_size`` is the
resulting mode; callers fall back to 10pt when no lines were observed
(a page-less or fully image-only document).
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from evoq_ingestion.extractors.pdf.lines import PdfLine

_DEFAULT_BODY_SIZE = 10.0


class FontStats:
    """Accumulate per-line font sizes across every page of one document."""

    def __init__(self) -> None:
        """Start with no observations."""
        self._counts: Counter[float] = Counter()

    def add_lines(self, lines: list[PdfLine]) -> None:
        """Record one page's (or column's) lines; weight by character count."""
        for line in lines:
            if line.size <= 0:
                continue
            self._counts[line.size] += max(1, len(line.text))

    @property
    def body_size(self) -> float:
        """The most-observed line font size, or ``10.0`` when nothing was seen."""
        if not self._counts:
            return _DEFAULT_BODY_SIZE
        return self._counts.most_common(1)[0][0]
