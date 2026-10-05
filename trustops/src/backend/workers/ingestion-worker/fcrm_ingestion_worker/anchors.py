"""Page:line anchors over canonical Markdown (design A §7.1 step 3).

A ``DocSourceRef`` cites ``page`` plus a page-relative ``line_start``/
``line_end`` (both 1-based, inclusive). This module turns the canonical
Markdown H3 stores into the page -> line-range map that makes those
citations resolvable, and resolves a citation back to its exact text.

The range computation itself lives in the engine
(``evoq_ingestion.scan_page_lines``, also emitted as ``structure.pages`` by
``GfmRenderer``) so there is exactly one reader of the ``--- Page N ---``
grammar; this module is the TrustOps-facing API over it. Semantics:

* a page's range is ``(start_line, end_line)`` in absolute 1-based body
  lines, excluding the marker line and the blank separator lines before
  and after the page's content (page-relative line 1 is its first
  non-blank line);
* page-relative line ``k`` is body line ``start_line + k - 1``;
* a body without page markers (DOCX, MD, TXT, XLSX, PPTX) is one implicit
  page 1, so every document is citable;
* an empty page has ``end_line == start_line - 1`` (zero lines).
"""

from __future__ import annotations

import logging
from typing import Optional

from evoq_ingestion import scan_page_lines

logger = logging.getLogger(__name__)


class CitationOutOfRangeError(ValueError):
    """A page:line citation does not resolve to lines inside the document."""


def compute_page_line_offsets(markdown_output: str) -> dict[int, tuple[int, int]]:
    """Map each page number to its ``(line_start, line_end)`` in ``markdown_output``.

    Line numbers are absolute and 1-based within the whole Markdown body.
    """
    return {p.n: (p.start_line, p.end_line) for p in scan_page_lines(markdown_output)}


def resolve_citation(
    markdown_output: str, page: int, line_start: int, line_end: int
) -> str:
    """Return the text a ``DocSourceRef(page, line_start, line_end)`` points at.

    ``line_start``/``line_end`` are page-relative, 1-based and inclusive.
    Raises :class:`CitationOutOfRangeError` when the page does not exist or
    the span falls outside it, so a fabricated citation fails loudly.
    """
    offsets = compute_page_line_offsets(markdown_output)
    if page not in offsets:
        raise CitationOutOfRangeError(
            f"page {page} not in document (pages {sorted(offsets)})"
        )
    start, end = offsets[page]
    page_lines = end - start + 1
    if line_start < 1 or line_end < line_start or line_end > page_lines:
        raise CitationOutOfRangeError(
            f"lines {line_start}-{line_end} outside page {page} (1-{page_lines})"
        )
    body = markdown_output.split("\n")
    return "\n".join(body[start + line_start - 2 : start + line_end - 1])


def read_pages(
    markdown_output: str,
    page_start: Optional[int] = None,
    page_end: Optional[int] = None,
) -> str:
    """Slice ``markdown_output`` to pages ``page_start..page_end`` (inclusive).

    Mirrors ``fcrm_contracts.tools.document_reader.DocumentReaderInput``:
    ``None`` bounds mean "from the first page" / "to the last page". The
    returned slice keeps the page markers so the reader still sees anchors.
    """
    offsets = compute_page_line_offsets(markdown_output)
    if not offsets:
        return ""
    pages = sorted(offsets)
    first = page_start if page_start is not None else pages[0]
    last = page_end if page_end is not None else pages[-1]
    chosen = [n for n in pages if first <= n <= last]
    if not chosen:
        raise CitationOutOfRangeError(
            f"pages {first}-{last} not in document (pages {pages})"
        )
    body = markdown_output.split("\n")
    from_line = _marker_line(body, chosen[0], offsets[chosen[0]][0])
    to_line = offsets[chosen[-1]][1]
    return "\n".join(body[from_line - 1 : to_line])


def _marker_line(body: list[str], page: int, start_line: int) -> int:
    """The ``--- Page N ---`` line above ``start_line``, or ``start_line`` if none."""
    line = start_line
    while line > 1 and not body[line - 2].strip():
        line -= 1
    if line > 1 and body[line - 2].strip() == f"--- Page {page} ---":
        return line - 1
    return start_line
