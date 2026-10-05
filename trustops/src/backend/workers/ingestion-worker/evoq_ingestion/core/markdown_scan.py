r"""The single regex set and line classifier for canonical Markdown.

Scope & functionality
---------------------
Every place that needs to *read* the canonical GFM this package emits
(``core.profile.ProfileScanner``, the ``Raw`` fallback in
``model.structure.StructureBuilder``, the legacy
``renderers._common.scan_markdown_structure`` shim) uses the regexes and the
one-pass ``MarkdownScanner`` defined here, so heading/table/list/code/marker
grammar is defined exactly once and matches what ``model.renderer.GfmRenderer``
writes (``AEO/contracts/canonical_document.md`` §Structure markers).

The scanner is a small state machine over ``text.split("\\n")``:
fences toggle code mode; blank lines close paragraphs; page/sheet/slide
markers, ATX headings, table rows, horizontal rules and list items are
classified before anything falls through to "paragraph text". It produces
facts only (``MarkdownScan``) — never a strategy decision.

``scan_page_lines`` (TrustOps addition, ``UPSTREAM.md``) is the page:line
anchor reader: it maps every ``--- Page N ---`` marker to the 1-based line
range of that page's content, so a citation "page 3, lines 12-14" resolves
back to real text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

HEADING_RE = re.compile(r"^(#{1,6})\s+(\S.*?)\s*#*\s*$")
LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+]|\d{1,9}[.)])\s+\S")
TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)*\|?\s*$")
FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
HR_RE = re.compile(r"^\s{0,3}(?:(?:-\s*){3,}|(?:\*\s*){3,}|(?:_\s*){3,})$")
NUMBERED_SECTION_RE = re.compile(r"^\d+(?:\.\d+)+\s+\S")
NUMBERED_HEADING_RE = re.compile(r"^\d+(?:\.\d+)*\.?\s+\S")
SENTENCE_END_RE = re.compile(r"[.!?](?:\s|$)")
LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\([^)]+\)")
IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]+\)")
PAGE_MARKER_RE = re.compile(r"^--- Page (\d+) ---\s*$")
SHEET_MARKER_RE = re.compile(r"^--- Sheet: (.+?) ---\s*$")
SLIDE_MARKER_RE = re.compile(r"^--- Slide (\d+) ---\s*$")
COMMENT_HEADING_RE = re.compile(r"^### Comment (\d+) — (.+?) · (.+)$")

PAGE_MARKER = "--- Page {n} ---"
SHEET_MARKER = "--- Sheet: {name} ---"
SLIDE_MARKER = "--- Slide {n} ---"


@dataclass(frozen=True, slots=True)
class HeadingLine:
    """One ATX heading with its 1-based line number in the scanned text."""

    level: int
    text: str
    line: int

    def to_dict(self) -> dict[str, int | str]:
        """Contract shape for ``structure.headings[]``."""
        return {"level": self.level, "text": self.text, "line": self.line}


@dataclass(frozen=True, slots=True)
class PageLines:
    """One page's content span in the canonical Markdown (TrustOps addition).

    ``start_line``/``end_line`` are 1-based, inclusive line numbers in the
    whole body; the ``--- Page N ---`` marker line itself is excluded, as are
    the blank separator lines leading and trailing the page's content (so
    page-relative line 1 is the page's first non-blank line). A page-relative line ``k``
    (1-based, as cited in a ``DocSourceRef``) is body line
    ``start_line + k - 1``. An empty page has ``end_line == start_line - 1``.
    """

    n: int
    start_line: int
    end_line: int

    @property
    def line_count(self) -> int:
        """Number of content lines on the page (0 for an empty page)."""
        return self.end_line - self.start_line + 1

    def to_dict(self) -> dict[str, int]:
        """Contract shape for ``structure.pages[]``."""
        return {"n": self.n, "start_line": self.start_line, "end_line": self.end_line}


@dataclass(slots=True)
class MarkdownScan:
    """Facts gathered by one ``MarkdownScanner.scan`` pass."""

    line_count: int = 0
    non_blank_line_count: int = 0
    headings: list[HeadingLine] = field(default_factory=list)
    heading_levels: dict[int, int] = field(default_factory=dict)
    list_item_count: int = 0
    numbered_section_count: int = 0
    code_block_count: int = 0
    code_chars: int = 0
    table_block_count: int = 0
    table_row_count: int = 0
    table_chars: int = 0
    link_count: int = 0
    image_count: int = 0
    sentence_count: int = 0
    paragraph_word_counts: list[int] = field(default_factory=list)
    page_numbers: set[int] = field(default_factory=set)
    sheet_names: list[str] = field(default_factory=list)
    slide_numbers: set[int] = field(default_factory=set)
    horizontal_rules: int = 0

    @property
    def heading_count(self) -> int:
        """Number of ATX headings outside code fences."""
        return len(self.headings)

    @property
    def max_heading_depth(self) -> int:
        """Deepest heading level seen (0 when none)."""
        return max(self.heading_levels, default=0)

    @property
    def paragraph_count(self) -> int:
        """Number of prose paragraphs (runs of plain text lines)."""
        return len(self.paragraph_word_counts)

    @property
    def sheet_count(self) -> int:
        """Distinct sheet markers seen."""
        return len(set(self.sheet_names))


class MarkdownScanner:
    """One-pass classifier over canonical Markdown lines."""

    __slots__ = ()

    def scan(self, text: str) -> MarkdownScan:
        """Classify every line of *text* and return the gathered facts."""
        scan = MarkdownScan()
        lines = (text or "").split("\n")
        scan.line_count = len(lines)
        state = _ScanState()
        for index, line in enumerate(lines, start=1):
            self._classify(line, index, scan, state)
        state.close_paragraph(scan)
        return scan

    # --------------------------------------------------------------- internals
    def _classify(  # noqa: C901, PLR0912, PLR0915 - one dispatch site keeps the grammar in one place
        self, line: str, index: int, scan: MarkdownScan, state: _ScanState
    ) -> None:
        stripped = line.strip()
        is_blank = stripped == ""

        if FENCE_RE.match(line):
            if not state.in_code:
                state.in_code = True
            else:
                state.in_code = False
                scan.code_block_count += 1
            scan.non_blank_line_count += 1
            state.close_paragraph(scan)
            state.prev_table_row = False
            return

        if not is_blank:
            scan.non_blank_line_count += 1

        if state.in_code:
            scan.code_chars += len(line) + 1
            return

        if is_blank:
            state.close_paragraph(scan)
            state.prev_table_row = False
            return

        if self._classify_marker(stripped, scan, state):
            return

        heading = HEADING_RE.match(line)
        if heading:
            level = len(heading.group(1))
            scan.headings.append(HeadingLine(level, heading.group(2).strip(), index))
            scan.heading_levels[level] = scan.heading_levels.get(level, 0) + 1
            state.close_paragraph(scan)
            state.prev_table_row = False
            return

        if TABLE_ROW_RE.match(line):
            scan.table_chars += len(line) + 1
            if not state.prev_table_row:
                scan.table_block_count += 1
                state.prev_table_row = True
                state.table_rows_in_block = 0
            if TABLE_SEP_RE.match(line) and state.table_rows_in_block == 1:
                # The separator right after the header row is not a data row.
                state.close_paragraph(scan)
                return
            scan.table_row_count += 1
            state.table_rows_in_block += 1
            state.close_paragraph(scan)
            return
        if TABLE_SEP_RE.match(line) and state.prev_table_row:
            return
        state.prev_table_row = False

        if HR_RE.match(line):
            scan.horizontal_rules += 1
            state.close_paragraph(scan)
            return

        if LIST_ITEM_RE.match(line):
            scan.list_item_count += 1
            state.close_paragraph(scan)
            self._inline_counts(line, scan)
            return

        if NUMBERED_SECTION_RE.match(stripped):
            scan.numbered_section_count += 1

        state.para_open = True
        state.para_words += len(stripped.split())
        self._inline_counts(line, scan)

    @staticmethod
    def _classify_marker(stripped: str, scan: MarkdownScan, state: _ScanState) -> bool:
        page = PAGE_MARKER_RE.match(stripped)
        if page:
            scan.page_numbers.add(int(page.group(1)))
        else:
            sheet = SHEET_MARKER_RE.match(stripped)
            if sheet:
                scan.sheet_names.append(sheet.group(1).strip())
            else:
                slide = SLIDE_MARKER_RE.match(stripped)
                if not slide:
                    return False
                scan.slide_numbers.add(int(slide.group(1)))
        state.close_paragraph(scan)
        state.prev_table_row = False
        return True

    @staticmethod
    def _inline_counts(line: str, scan: MarkdownScan) -> None:
        scan.link_count += len(LINK_RE.findall(line))
        scan.image_count += len(IMAGE_RE.findall(line))
        scan.sentence_count += len(SENTENCE_END_RE.findall(line))


class _ScanState:
    """Mutable per-scan state of the line classifier."""

    __slots__ = ("in_code", "para_open", "para_words", "prev_table_row", "table_rows_in_block")

    def __init__(self) -> None:
        self.in_code = False
        self.para_open = False
        self.para_words = 0
        self.prev_table_row = False
        self.table_rows_in_block = 0

    def close_paragraph(self, scan: MarkdownScan) -> None:
        if self.para_open:
            scan.paragraph_word_counts.append(self.para_words)
            self.para_words = 0
            self.para_open = False


def scan_markdown(text: str) -> MarkdownScan:
    """Convenience wrapper: ``MarkdownScanner().scan(text)``."""
    return MarkdownScanner().scan(text)


def _page_markers(lines: list[str]) -> list[tuple[int, int]]:
    """``(line_number, page_n)`` for every page marker outside a code fence."""
    markers: list[tuple[int, int]] = []
    in_code = False
    for index, line in enumerate(lines, start=1):
        if FENCE_RE.match(line):
            in_code = not in_code
            continue
        if in_code:
            continue
        match = PAGE_MARKER_RE.match(line.strip())
        if match:
            markers.append((index, int(match.group(1))))
    return markers


def _skip_leading_blank(lines: list[str], start: int, end: int) -> int:
    """Move *start* forward over blank lines; ``end + 1`` when all are blank."""
    while start <= end and not lines[start - 1].strip():
        start += 1
    return start


def _trim_trailing_blank(lines: list[str], start: int, end: int) -> int:
    """Move *end* back over blank lines, never before ``start - 1``."""
    while end >= start and not lines[end - 1].strip():
        end -= 1
    return end


def scan_page_lines(text: str) -> tuple[PageLines, ...]:
    """Page -> content line range for canonical Markdown (TrustOps addition).

    Fence-aware (a marker inside a code block is not a page break), and reads
    the same ``PAGE_MARKER_RE`` the renderer writes. Content before the first
    marker is not attributed to any page. A body with no page markers at all
    (DOCX, MD, TXT, XLSX, PPTX) is one implicit page 1 spanning every line,
    so every document is citable as page:line. An empty body has no pages.
    A repeated page number keeps its first occurrence only.
    """
    lines = (text or "").split("\n")
    if lines and lines[-1] == "":
        lines.pop()  # the body's single trailing newline is not a line
    markers = _page_markers(lines)
    if not markers:
        start = _skip_leading_blank(lines, 1, len(lines))
        end = _trim_trailing_blank(lines, start, len(lines))
        return (PageLines(1, start, end),) if end >= start else ()
    pages: list[PageLines] = []
    seen: set[int] = set()
    for position, (marker_line, n) in enumerate(markers):
        next_marker = markers[position + 1][0] if position + 1 < len(markers) else None
        last = (next_marker - 1) if next_marker is not None else len(lines)
        start = _skip_leading_blank(lines, marker_line + 1, last)
        end = _trim_trailing_blank(lines, start, last)
        if end < start:  # empty page: a zero-length span right after the marker
            start, end = marker_line + 1, marker_line
        if n not in seen:
            seen.add(n)
            pages.append(PageLines(n, start, end))
    return tuple(pages)
