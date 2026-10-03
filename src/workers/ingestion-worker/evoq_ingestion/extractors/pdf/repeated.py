"""``RepeatedLineDetector`` — cross-page header/footer removal.

Scope & functionality
---------------------
Collects the first and last two lines of every page (normalised: digit runs
-> ``#``, so ``"Page 3 of 79"`` and ``"Page 71 of 79"`` compare equal) and,
once at least 3 pages have been observed, reports every normalised line
seen on ``>= max(3, 30%)`` of pages — the repeated headers/footers to drop
entirely from every page's text, not just keep once.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from evoq_ingestion.extractors.pdf.lines import normalize_repeated_line

if TYPE_CHECKING:
    from evoq_ingestion.extractors.pdf.lines import PdfLine

_EDGE_LINES = 2


class RepeatedLineDetector:
    """Observe each page's edge lines; report the ones repeated across pages."""

    def __init__(self, *, min_pages: int = 3, min_fraction: float = 0.3) -> None:
        """Construct a detector; ``repeated()`` is empty below ``min_pages`` pages."""
        self._min_pages = min_pages
        self._min_fraction = min_fraction
        self._counts: dict[str, int] = {}
        self._page_count = 0

    def observe(self, lines: list[PdfLine]) -> None:
        """Record one page's first/last two lines (deduplicated within the page)."""
        self._page_count += 1
        edge = lines[:_EDGE_LINES] + lines[-_EDGE_LINES:] if lines else []
        normalised_texts = {
            normalize_repeated_line(line.text) for line in edge if line.text.strip()
        }
        for normalised in normalised_texts:
            self._counts[normalised] = self._counts.get(normalised, 0) + 1

    def repeated(self) -> set[str]:
        """Normalised lines seen on enough pages to be a header/footer."""
        if self._page_count < self._min_pages:
            return set()
        threshold = max(self._min_pages, self._min_fraction * self._page_count)
        return {text for text, count in self._counts.items() if count >= threshold}
