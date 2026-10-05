"""``DocumentIndex`` — the one-pass structural index every enricher/segmenter reads.

Scope & functionality
----------------------
``DocumentIndex.build(text, requirement_pattern, *, numbered_headings=...)``
computes, once per document: line start offsets (for O(log n) line lookup
by later modules); every structural marker (``--- Page N ---`` /
``--- Sheet: X ---`` / ``--- Slide N ---`` / ``### Comment N — ...``);
every heading (ATX always; numbered ``8.3 Posting``-style only when the
document has at least three such candidates, nested under the nearest
preceding ATX ancestor when both exist — a numbered heading's level is
``max(dotted_depth, ancestor_level + 1)`` so it always sorts deeper than
the ATX section it falls under, whatever its own dotted depth); every
contiguous GFM table block (header/separator/data rows with exact offsets
and pre-split cell text, plus a caption line when a non-structural,
non-table line precedes it); every requirement-id match with whether it
leads its line (a table row's first cell, a list item, or plain
line-leading placement) — the signal ``RequirementIdEnricher``/segmenters
use to tell a block-starting id from an inline mention; and every
fenced-code span.

This module's dataclasses (``Marker``, ``Heading``, ``RowSpan``,
``TableBlock``, ``ReqHit``, ``DocumentIndex``) are consumed by the
``chunking/enrichers/*`` and ``chunking/segmenters/*`` packages by field
name — treat every field here as part of the frozen cross-lane contract.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field
from typing import Literal

from evoq_context.chunking.markers import (
    CODE_FENCE_RE,
    COMMENT_MARKER_RE,
    HEADING_RE,
    NUMBERED_HEADING_RE,
    PAGE_MARKER_RE,
    SHEET_MARKER_RE,
    SLIDE_MARKER_RE,
    STRUCTURAL_MARKER_RE,
    TABLE_CAPTION_RE,
    TABLE_ROW_RE,
    TABLE_SEP_RE,
    is_numbered_heading,
    numbered_heading_level,
    strip_emphasis,
)
from evoq_context.chunking.types import Span

#: A numbered-heading grammar is only trusted once the document shows this
#: many independent candidates (a lone "3.5 million" or "1. Install the
#: package." must not turn on numbered-section parsing for the whole doc).
_MIN_NUMBERED_SECTIONS = 3
_MAX_CAPTION_LINE_CHARS = 200
#: A requirement id is "line-leading" when nothing but whitespace, a list
#: marker, an ordinal or a table pipe precedes it on its line.
_LEADING_PREFIX_RE = re.compile(r"^[ \t]*(?:[-*+]|\d{1,3}[.)]|\|)?[ \t]*$")

MarkerKind = Literal["page", "sheet", "slide", "comment"]
HeadingSource = Literal["atx", "numbered"]


@dataclass(frozen=True, slots=True)
class Marker:
    """One structural marker line (``--- Page 3 ---`` etc.)."""

    kind: MarkerKind
    value: str
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class Heading:
    """One heading, ATX or numbered, with its dotted-depth-derived level."""

    level: int
    text: str
    start: int
    end: int
    source: HeadingSource = "atx"


@dataclass(frozen=True, slots=True)
class RowSpan:
    """One data row of a table, with its cell texts and exact offsets."""

    start: int
    end: int
    cells: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TableBlock:
    """One contiguous GFM table block."""

    start: int
    end: int
    header_line: str
    sep_line: str | None
    header_cells: tuple[str, ...]
    data_rows: tuple[RowSpan, ...]
    caption: Span | None = None


@dataclass(frozen=True, slots=True)
class ReqHit:
    """One requirement-id match; ``line_leading`` is the block-start signal."""

    id: str
    start: int
    end: int
    line_leading: bool = False


@dataclass(slots=True)
class DocumentIndex:
    """Everything computed once, in one pass, about a document's structure."""

    line_offsets: tuple[int, ...] = ()
    markers: tuple[Marker, ...] = ()
    headings: tuple[Heading, ...] = ()
    tables: tuple[TableBlock, ...] = ()
    requirement_hits: tuple[ReqHit, ...] = ()
    fences: tuple[Span, ...] = field(default_factory=tuple)

    def line_at(self, offset: int) -> int:
        """0-based line number containing ``offset``."""
        return bisect.bisect_right(self.line_offsets, offset) - 1

    @classmethod
    def build(
        cls,
        text: str,
        requirement_pattern: re.Pattern[str],
        *,
        numbered_headings: bool = True,
    ) -> DocumentIndex:
        """Compute every structural fact about ``text`` once.

        ``numbered_headings`` is the caller's gate (typically
        ``profile.numbered_section_count >= 3``); this also re-checks the
        candidate count internally before trusting numbered-heading parsing.
        """
        line_offsets = _line_offsets(text)
        markers = _find_markers(text)
        headings = _find_headings(text, allow_numbered=numbered_headings)
        tables = _find_tables(text)
        requirement_hits = _find_requirement_hits(text, requirement_pattern)
        fences = _find_fences(text)
        return cls(
            line_offsets=tuple(line_offsets),
            markers=tuple(markers),
            headings=tuple(headings),
            tables=tuple(tables),
            requirement_hits=tuple(requirement_hits),
            fences=tuple(fences),
        )


def _line_offsets(text: str) -> list[int]:
    offsets = [0]
    for i, ch in enumerate(text):
        if ch == "\n":
            offsets.append(i + 1)
    return offsets


def _comment_marker(text: str, m: re.Match[str]) -> Marker:
    line_end = text.find("\n", m.end())
    line_end = len(text) if line_end == -1 else line_end
    return Marker("comment", m.group(1), m.start(), line_end)


def _find_markers(text: str) -> list[Marker]:
    markers: list[Marker] = [
        Marker("page", m.group(1), m.start(), m.end()) for m in PAGE_MARKER_RE.finditer(text)
    ]
    markers.extend(
        Marker("sheet", m.group(1).strip(), m.start(), m.end())
        for m in SHEET_MARKER_RE.finditer(text)
    )
    markers.extend(
        Marker("slide", m.group(1), m.start(), m.end()) for m in SLIDE_MARKER_RE.finditer(text)
    )
    markers.extend(_comment_marker(text, m) for m in COMMENT_MARKER_RE.finditer(text))
    markers.sort(key=lambda mk: mk.start)
    return markers


def _find_headings(text: str, *, allow_numbered: bool) -> list[Heading]:
    headings: list[Heading] = [
        Heading(
            level=len(m.group(1)),
            text=strip_emphasis(m.group(2)),
            start=m.start(),
            end=m.end(),
            source="atx",
        )
        for m in HEADING_RE.finditer(text)
    ]

    numbered_candidates: list[tuple[int, int, str, int]] = []  # (start, end, number, offset-in-doc)
    if allow_numbered:
        lines = text.split("\n")
        pos = 0
        for line in lines:
            stripped = line.rstrip("\r")
            if is_numbered_heading(stripped):
                m = NUMBERED_HEADING_RE.match(stripped.strip())
                if m is not None:
                    numbered_candidates.append((pos, pos + len(stripped), m.group(1), pos))
            pos += len(line) + 1

    if len(numbered_candidates) >= _MIN_NUMBERED_SECTIONS:
        atx_sorted = sorted(headings, key=lambda h: h.start)
        for start, end, number, _ in numbered_candidates:
            ancestor_level = 0
            for h in atx_sorted:
                if h.start >= start:
                    break
                ancestor_level = h.level
            line = text[start:end].strip()
            m = NUMBERED_HEADING_RE.match(line)
            title = strip_emphasis(m.group(2)) if m else line
            # Nest under the nearest preceding ATX ancestor regardless of the
            # numbered heading's own dotted depth: a depth-1 "9 Appendix"
            # under an ATX H3 must still sort deeper than that H3, while a
            # depth-2 "8.3 Posting" under an ATX H1 keeps its natural
            # level=2 (matching the documented example) since 2 > 1 already.
            level = max(numbered_heading_level(number), ancestor_level + 1)
            headings.append(
                Heading(
                    level=level,
                    text=f"{number} {title}".strip(),
                    start=start,
                    end=end,
                    source="numbered",
                )
            )

    headings.sort(key=lambda h: h.start)
    return headings


def _find_tables(text: str) -> list[TableBlock]:
    lines = text.split("\n")
    tables: list[TableBlock] = []
    n = len(lines)
    pos = 0
    line_starts: list[int] = []
    for line in lines:
        line_starts.append(pos)
        pos += len(line) + 1

    i = 0
    while i < n:
        line = lines[i].rstrip("\r")
        if not TABLE_ROW_RE.match(line):
            i += 1
            continue
        j = i
        row_starts: list[int] = []
        row_ends: list[int] = []
        while j < n and TABLE_ROW_RE.match(lines[j].rstrip("\r")):
            row_starts.append(line_starts[j])
            row_ends.append(line_starts[j] + len(lines[j].rstrip("\r")))
            j += 1
        if j - i < 2:
            i = j if j > i else i + 1
            continue
        has_sep = TABLE_SEP_RE.match(lines[i + 1].rstrip("\r")) is not None
        header_line = lines[i].rstrip("\r")
        sep_line = lines[i + 1].rstrip("\r") if has_sep else None
        data_start_idx = i + (2 if has_sep else 1)
        header_cells = _split_row(header_line)
        data_rows = [
            RowSpan(
                start=row_starts[k - i],
                end=row_ends[k - i],
                cells=tuple(_split_row(lines[k].rstrip("\r"))),
            )
            for k in range(data_start_idx, j)
        ]
        block_start = row_starts[0]
        block_end = row_ends[-1]
        caption = _find_caption(lines, i, line_starts)
        tables.append(
            TableBlock(
                start=block_start,
                end=block_end,
                header_line=header_line,
                sep_line=sep_line,
                header_cells=tuple(header_cells),
                data_rows=tuple(data_rows),
                caption=caption,
            )
        )
        i = j
    return tables


def _split_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _find_caption(lines: list[str], table_start_idx: int, line_starts: list[int]) -> Span | None:
    k = table_start_idx - 1
    while k >= 0 and not lines[k].strip():
        k -= 1
    if k < 0:
        return None
    candidate = lines[k].rstrip("\r")
    stripped = candidate.strip()
    if not stripped:
        return None
    # A structural marker or another table's row directly above is not a
    # caption — it belongs to the document's structure, not this table.
    if STRUCTURAL_MARKER_RE.match(candidate) or TABLE_ROW_RE.match(candidate):
        return None
    if TABLE_CAPTION_RE.match(stripped) or len(candidate) <= _MAX_CAPTION_LINE_CHARS:
        start = line_starts[k]
        return Span(start, start + len(candidate), candidate, "prose")
    return None


def _find_requirement_hits(text: str, requirement_pattern: re.Pattern[str]) -> list[ReqHit]:
    hits: list[ReqHit] = []
    for m in requirement_pattern.finditer(text):
        line_start = text.rfind("\n", 0, m.start()) + 1
        prefix = text[line_start : m.start()]
        # Leading iff nothing but whitespace / list markers / table pipes /
        # ordinal digits precede the id on its line.
        line_leading = bool(_LEADING_PREFIX_RE.match(prefix))
        hit_id = m.group(1) if m.groups() else m.group(0)
        hits.append(ReqHit(id=hit_id, start=m.start(), end=m.end(), line_leading=line_leading))
    hits.sort(key=lambda h: h.start)
    return hits


def _find_fences(text: str) -> list[Span]:
    fences: list[Span] = []
    lines = text.split("\n")
    pos = 0
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i].rstrip("\r")
        m = CODE_FENCE_RE.match(line)
        if not m:
            pos += len(lines[i]) + 1
            i += 1
            continue
        start = pos
        j = i + 1
        p = pos + len(lines[i]) + 1
        closed = False
        while j < n:
            lj = lines[j].rstrip("\r")
            cm = CODE_FENCE_RE.match(lj)
            closes = (
                cm is not None
                and cm.group(1)[0] == m.group(1)[0]
                and len(cm.group(1)) >= len(m.group(1))
                and not cm.group(2).strip()
            )
            if closes:
                closed = True
                end = p + len(lines[j])
                i = j + 1
                pos = end + 1
                break
            p += len(lines[j]) + 1
            j += 1
        else:
            end = len(text)
            i = n
            pos = end
        fences.append(Span(start, end, text[start:end], "code"))
        if not closed:
            break
    return fences
