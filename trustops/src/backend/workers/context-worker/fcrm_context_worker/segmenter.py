"""Clause segmenter: split a policy source into individually citable clauses.

Policy text is quoted verbatim with a source reference (product rule), so
the unit of retrieval is one whole clause — never a fixed-size window that
cuts a clause in half or glues two together. The corpus shape this targets
(design A §6.1; seed corpus v14) is clauses headed by an identifier:

* ``§3.1`` / ``§ 2`` — internal standards (``Policy 4.2 §3.1``);
* ``FFIEC-TPPP-04``, ``FCRM42-3.1``, ``OFAC-1.2`` — dashed clause ids;
* ``3.1`` / ``4.2.7`` — dotted section numbers.

Algorithm (one pass over the lines, fence-aware):

1. A line is a **clause boundary** when, after an optional Markdown heading
   prefix (``#``..``######``), it starts with one of the markers above
   followed by whitespace and text. The marker becomes ``clause_id``; the
   rest of the line is the ``title``.
2. A Markdown heading *without* a marker is a **section** boundary: it closes
   the current clause and becomes the ``section`` label of what follows.
3. A clause runs until the next clause or section boundary. Its ``text`` is
   the verbatim body lines (stripped of leading/trailing blank lines) and it
   keeps 1-based ``line_start``/``line_end`` (heading line through last body
   line) so the clause can be cited page:line once H3 has anchored it.
4. **Fallback**: when no marker occurs anywhere, the text is split on blank
   lines into paragraph segments (``kind="paragraph"``, no ``clause_id``).
5. A clause longer than ``max_chars`` is split at paragraph breaks into parts
   that keep the same ``clause_id`` with ``part`` = 1, 2, ... (no part is cut
   mid-paragraph; a single over-long paragraph stays whole).

Lines inside a fenced code block never start a boundary. Plain list items
(``1. item``) are not markers: a dotted number needs at least two parts.
Text before the first clause of a section (a source's introduction) is not
a clause and is not emitted when the document has clause markers.
"""

from __future__ import annotations

import logging
import re
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict

logger = logging.getLogger(__name__)

DEFAULT_MAX_CHARS = 4000

_MARKER = (
    r"(?P<marker>"
    r"§\s?\d+(?:\.\d+)*"  # §3.1, § 2
    r"|[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*-\d+(?:\.\d+)*"  # FFIEC-TPPP-04, FCRM42-3.1
    r"|\d+(?:\.\d+)+"  # 3.1, 4.2.7
    r")"
)
_BOUNDARY_RE = re.compile(
    rf"^\s{{0,3}}(?:#{{1,6}}\s+)?{_MARKER}[.)]?\s+(?P<title>\S.*?)\s*#*\s*$"
)
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(?P<text>\S.*?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^\s{0,3}(?:`{3,}|~{3,})")


class ClauseSegment(BaseModel):
    """One citable unit of a policy source."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["clause", "paragraph"]
    clause_id: Optional[str]
    title: str
    text: str
    section: Optional[str]
    line_start: int
    line_end: int
    part: Optional[int] = None


def _normalise_marker(marker: str) -> str:
    return re.sub(r"^§\s+", "§", marker)


def _boundaries(lines: list[str]) -> list[tuple[int, str, Optional[str], str]]:
    """``(index, kind, clause_id, label)`` for every clause/section boundary.

    ``kind`` is ``"clause"`` or ``"section"``; ``index`` is 0-based.
    """
    found: list[tuple[int, str, Optional[str], str]] = []
    in_code = False
    for index, line in enumerate(lines):
        if _FENCE_RE.match(line):
            in_code = not in_code
            continue
        if in_code:
            continue
        clause = _BOUNDARY_RE.match(line)
        if clause:
            marker = _normalise_marker(clause.group("marker"))
            found.append((index, "clause", marker, clause.group("title")))
            continue
        heading = _HEADING_RE.match(line)
        if heading:
            found.append((index, "section", None, heading.group("text")))
    return found


def _trim(lines: list[str], start: int, end: int) -> tuple[int, int]:
    """Shrink ``[start, end)`` past leading/trailing blank lines."""
    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1
    return start, end


def _paragraph_spans(lines: list[str], start: int, end: int) -> list[tuple[int, int]]:
    """Blank-line separated ``[s, e)`` spans inside ``[start, end)``."""
    spans: list[tuple[int, int]] = []
    span_start: Optional[int] = None
    for index in range(start, end):
        if lines[index].strip():
            if span_start is None:
                span_start = index
        elif span_start is not None:
            spans.append((span_start, index))
            span_start = None
    if span_start is not None:
        spans.append((span_start, end))
    return spans


def _paragraph_fallback(lines: list[str]) -> list[ClauseSegment]:
    return [
        ClauseSegment(
            kind="paragraph",
            clause_id=None,
            title="",
            text="\n".join(lines[s:e]),
            section=None,
            line_start=s + 1,
            line_end=e,
        )
        for s, e in _paragraph_spans(lines, 0, len(lines))
    ]


def _split_long(
    segment: ClauseSegment, lines: list[str], body: tuple[int, int], max_chars: int
) -> list[ClauseSegment]:
    """Split an over-long clause at paragraph breaks into numbered parts."""
    groups: list[list[tuple[int, int]]] = [[]]
    size = 0
    for span in _paragraph_spans(lines, *body):
        span_chars = sum(len(lines[i]) + 1 for i in range(*span))
        if groups[-1] and size + span_chars > max_chars:
            groups.append([])
            size = 0
        groups[-1].append(span)
        size += span_chars
    if len(groups) == 1:
        return [segment]
    parts: list[ClauseSegment] = []
    for number, group in enumerate(groups, start=1):
        first, last = group[0][0], group[-1][1]
        parts.append(
            segment.model_copy(
                update={
                    "text": "\n".join(lines[first:last]),
                    "line_start": segment.line_start if number == 1 else first + 1,
                    "line_end": last,
                    "part": number,
                }
            )
        )
    return parts


def segment_clauses(
    text: str, *, max_chars: int = DEFAULT_MAX_CHARS
) -> list[ClauseSegment]:
    """Split ``text`` into clause segments (or paragraphs when it has no markers)."""
    lines = (text or "").split("\n")
    boundaries = _boundaries(lines)
    if not any(kind == "clause" for _, kind, _, _ in boundaries):
        return _paragraph_fallback(lines)
    segments: list[ClauseSegment] = []
    section: Optional[str] = None
    for position, (index, kind, clause_id, label) in enumerate(boundaries):
        if kind == "section":
            section = label
            continue
        end = (
            boundaries[position + 1][0]
            if position + 1 < len(boundaries)
            else len(lines)
        )
        body = _trim(lines, index + 1, end)
        segment = ClauseSegment(
            kind="clause",
            clause_id=clause_id,
            title=label,
            text="\n".join(lines[body[0] : body[1]]),
            section=section,
            line_start=index + 1,
            line_end=max(body[1], index + 1),
        )
        segments.extend(_split_long(segment, lines, body, max_chars))
    logger.debug("segmented %d clause segment(s)", len(segments))
    return segments
