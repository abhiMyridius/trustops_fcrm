"""Offset-exact text operations: sentence and unit splitting.

Scope & functionality
---------------------
``split_sentences`` finds sentence boundaries in any script — Latin
terminators (``.!?`` plus Arabic/Urdu/Devanagari marks) followed by
whitespace, CJK full-width terminators (no whitespace required), a line
break in front of a list item, and paragraph breaks — with a small
abbreviation/initial/ordinal guard and **no** requirement that the next
sentence start with an ASCII capital (lowercase and non-Latin text used to
get zero sentence boundaries and therefore zero overlap). ``split_units``
walks a Markdown body once and yields paragraphs, with fenced code and GFM
table blocks atomic, consecutive list items grouped, and structural
markers/headings as their own hard-boundary units. Every returned ``Span``
is an exact slice of the input (``text == source[start:end]``), so offsets
downstream are trustworthy. ``ws_collapse`` is the comparison used by the
offset invariant tests.
"""

from __future__ import annotations

import re

from evoq_context.chunking.markers import (
    CODE_FENCE_RE,
    HEADING_RE,
    LIST_ITEM_RE,
    STRUCTURAL_MARKER_RE,
    TABLE_ROW_RE,
    TABLE_SEP_RE,
)
from evoq_context.chunking.types import Span, SpanKind

_SENTENCE_BOUNDARY_RE = re.compile(
    r"(?P<latin>[.!?؟۔।]+[\"'”’)\]]*(?=\s|$))"
    r"|(?P<cjk>[。！？]+[\"'”’)\]』」】]*)"
    r"|(?P<para>\n[ \t]*\n)"
    r"|(?P<list>\n(?=[ \t]*(?:[-*+]|\d{1,3}[.)])[ \t]+\S))"
)
_WORD_BEFORE_RE = re.compile(r"(\S+)$")
_ABBREVIATIONS = frozenset(
    {
        "e.g", "i.e", "etc", "vs", "cf", "fig", "dr", "mr", "mrs", "ms", "prof",
        "inc", "ltd", "approx", "dept", "vol", "jr", "sr", "al", "eq", "sec",
    }
)


def ws_collapse(text: str) -> str:
    """Collapse every whitespace run to one space and strip — the offset-invariant comparator."""
    return " ".join(text.split())


def _trimmed(text: str, start: int, end: int, kind: SpanKind, base: int,
             attrs: dict[str, str | int] | None = None) -> Span | None:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    if start >= end:
        return None
    if attrs:
        return Span(base + start, base + end, text[start:end], kind, attrs)
    return Span(base + start, base + end, text[start:end], kind)


def _is_guarded_terminator(text: str, terminator_at: int) -> bool:
    """Abbreviation (``e.g.``), single initial (``J.``) or line-leading ordinal (``1.``)?"""
    before = text[:terminator_at]
    match = _WORD_BEFORE_RE.search(before)
    if match is None:
        return False
    word = match.group(1)
    lowered = word.rstrip(".").lower()
    if lowered in _ABBREVIATIONS:
        return True
    if len(word) == 1 and word.isalpha():
        return True
    if word.isdigit():
        line_prefix = before[: match.start(1)]
        return line_prefix.strip() == "" or line_prefix.endswith("\n")
    return False


def split_sentences(text: str, base: int = 0) -> list[Span]:
    """Split ``text`` into sentence spans with exact offsets (``+ base``).

    Whitespace between sentences belongs to no span; every span's text is
    ``text[start-base:end-base]`` verbatim. Text without any terminator is
    one sentence.
    """
    spans: list[Span] = []
    cursor = 0
    for match in _SENTENCE_BOUNDARY_RE.finditer(text):
        if match.lastgroup == "latin" and _is_guarded_terminator(text, match.start()):
            continue
        sentence_end = match.end() if match.lastgroup in ("latin", "cjk") else match.start()
        span = _trimmed(text, cursor, sentence_end, "prose", base)
        if span is not None:
            spans.append(span)
        cursor = match.end()
    tail = _trimmed(text, cursor, len(text), "prose", base)
    if tail is not None:
        spans.append(tail)
    return spans


def _is_structural_line(line: str) -> bool:
    return bool(
        STRUCTURAL_MARKER_RE.match(line)
        or HEADING_RE.match(line)
        or CODE_FENCE_RE.match(line)
        or TABLE_ROW_RE.match(line)
    )


def _closes_fence(line: str, opener: str) -> bool:
    match = CODE_FENCE_RE.match(line)
    return (
        match is not None
        and match.group(1)[0] == opener[0]
        and len(match.group(1)) >= len(opener)
        and not match.group(2).strip()
    )


def split_units(text: str, *, base: int = 0) -> list[Span]:
    """One pass over a Markdown body → ordered, offset-exact units.

    Kinds: ``marker`` (page/sheet/slide line), ``heading``, ``code`` (whole
    fence, atomic), ``table`` (contiguous GFM rows, atomic; ``attrs`` carry
    ``line_count``/``data_rows``), ``list`` (consecutive items plus their
    indented continuation lines) and ``prose`` (blank-line paragraphs). A
    blank line, a marker, a heading, a fence or a table row always ends the
    unit before it — no unit ever spans a structural boundary.
    """
    lines = text.split("\n")
    units: list[Span] = []
    pos = 0
    i = 0
    n = len(lines)

    def _emit(start: int, end: int, kind: SpanKind, attrs: dict[str, str | int] | None = None) -> None:
        span = _trimmed(text, start, end, kind, base, attrs)
        if span is not None:
            units.append(span)

    while i < n:
        line = lines[i].rstrip("\r")
        line_start = pos
        line_end = pos + len(lines[i])
        if not line.strip():
            pos = line_end + 1
            i += 1
            continue

        fence = CODE_FENCE_RE.match(line)
        if fence:
            j = i + 1
            p = line_end + 1
            while j < n and not _closes_fence(lines[j].rstrip("\r"), fence.group(1)):
                p += len(lines[j]) + 1
                j += 1
            if j < n:
                end = p + len(lines[j])
                i = j + 1
            else:
                end = len(text)
                i = n
            _emit(line_start, end, "code", {"lang": fence.group(2).strip()})
            pos = end + 1
            continue

        if STRUCTURAL_MARKER_RE.match(line):
            _emit(line_start, line_end, "marker")
            pos = line_end + 1
            i += 1
            continue

        if HEADING_RE.match(line):
            _emit(line_start, line_end, "heading")
            pos = line_end + 1
            i += 1
            continue

        if TABLE_ROW_RE.match(line):
            j = i
            p = line_start
            last_end = line_end
            while j < n and TABLE_ROW_RE.match(lines[j].rstrip("\r")):
                last_end = p + len(lines[j])
                p = last_end + 1
                j += 1
            row_count = j - i
            has_sep = row_count >= 2 and TABLE_SEP_RE.match(lines[i + 1].rstrip("\r")) is not None
            if row_count >= 2:
                data_rows = row_count - (2 if has_sep else 1)
                _emit(line_start, last_end, "table",
                      {"line_count": row_count, "data_rows": max(0, data_rows)})
                pos = p
                i = j
                continue
            # a lone pipe line is prose

        if LIST_ITEM_RE.match(line):
            j = i
            p = line_start
            last_end = line_end
            while j < n:
                lj = lines[j].rstrip("\r")
                if not lj.strip():
                    break
                if j > i and not (LIST_ITEM_RE.match(lj) or lj[:1] in (" ", "\t")):
                    break
                if j > i and (STRUCTURAL_MARKER_RE.match(lj) or HEADING_RE.match(lj)
                              or CODE_FENCE_RE.match(lj) or TABLE_ROW_RE.match(lj)):
                    break
                last_end = p + len(lines[j])
                p = last_end + 1
                j += 1
            _emit(line_start, last_end, "list")
            pos = p
            i = j
            continue

        j = i
        p = line_start
        last_end = line_end
        while j < n:
            lj = lines[j].rstrip("\r")
            if not lj.strip():
                break
            if j > i and (_is_structural_line(lj) or LIST_ITEM_RE.match(lj)):
                break
            last_end = p + len(lines[j])
            p = last_end + 1
            j += 1
        _emit(line_start, last_end, "prose")
        pos = p
        i = j

    return units
