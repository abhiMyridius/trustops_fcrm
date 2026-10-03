"""The single home of every Markdown structure regex the engine uses.

Scope & functionality
---------------------
Every pattern that recognises canonical-Markdown structure (ATX and
numbered headings, page/sheet/slide/comment markers, GFM table rows and
separators, fences, list items, table captions) is compiled here exactly
once with fixed flags, and every other module — profiler, index,
segmenters, enrichers, normalizer, synthesis — imports it from here. The
grammar is the one pinned in ``AEO/contracts/fixtures/markers.md``.
``evoq_context/tests/test_markers.py`` fails the build if any of these
names or pattern literals is declared a second time anywhere in the
package.

``DEFAULT_REQUIREMENT_ID_PATTERN`` deliberately does *not* live here (it
is a shared default, ``core/limits.py``); ``compile_requirement_pattern``
turns a configured pattern into a compiled regex and raises ``ConfigError``
instead of silently substituting the default.
"""

from __future__ import annotations

import re

from evoq_context.core.errors import ConfigError

#: ATX heading: ``#{1,6}`` + whitespace + text (+ optional closing hashes).
HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$", re.MULTILINE)

#: Numbered heading candidate: ``8``, ``8.3``, ``8.3.1`` (+ optional ``.``/``)``) + title.
#: Use through :func:`is_numbered_heading`, which applies the shape checks.
NUMBERED_HEADING_RE = re.compile(r"^(\d+(?:\.\d+){0,3})[.)]?[ \t]+(\S.{0,118})$", re.MULTILINE)

#: Structural markers — grammar from contracts/fixtures/markers.md; all capture their value.
PAGE_MARKER_RE = re.compile(r"^---[ \t]*Page[ \t]+(\d+)[ \t]*---[ \t]*$", re.MULTILINE)
SHEET_MARKER_RE = re.compile(r"^---[ \t]*Sheet:[ \t]*(.+?)[ \t]*---[ \t]*$", re.MULTILINE)
SLIDE_MARKER_RE = re.compile(r"^---[ \t]*Slide[ \t]+(\d+)[ \t]*---[ \t]*$", re.MULTILINE)
#: Union of the three — "is this line a hard structural boundary?"
STRUCTURAL_MARKER_RE = re.compile(
    r"^---[ \t]*(?:Page[ \t]+\d+|Sheet:[ \t]*.+?|Slide[ \t]+\d+)[ \t]*---[ \t]*$", re.MULTILINE
)

#: Jira rendered comment header ``### Comment {i} — {author} · {created}``.
COMMENT_MARKER_RE = re.compile(r"^### Comment (\d+) — ", re.MULTILINE)
#: Zero-width split point in front of every comment header (for ``re.split``).
COMMENT_SPLIT_RE = re.compile(r"(?=^### Comment \d+ — )", re.MULTILINE)

#: GFM table row / separator — per-line patterns, use with ``.match(line)``.
TABLE_ROW_RE = re.compile(r"^[ \t]*\|.*\|[ \t]*$")
TABLE_SEP_RE = re.compile(r"^(?=.*-)[ \t]*\|?[ \t:|-]+\|[ \t:|-]*\|?[ \t]*$")

#: Fenced code opener/closer (``` or ~~~, up to 3 leading spaces); group 2 = info string.
CODE_FENCE_RE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})(.*)$", re.MULTILINE)

#: A bare horizontal rule line (never a marker — those carry text).
HORIZONTAL_RULE_RE = re.compile(r"^-{3,}$", re.MULTILINE)

#: Bullet or ordered list item — per-line, use with ``.match(line)``.
LIST_ITEM_RE = re.compile(r"^[ \t]*(?:[-*+]|\d{1,3}[.)])[ \t]+\S")

#: A table caption line (``Table 3: …`` / ``Tab. 3 …``) directly above a table.
TABLE_CAPTION_RE = re.compile(r"^(?:Table|Tab\.)[ \t]*\d+[.:]?[ \t]", re.IGNORECASE)

_EMPHASIS_WRAP_RE = re.compile(r"^([*_]{1,3})(.+?)\1$")
_NUMBERED_HEADING_MAX_WORDS = 14
_NUMBERED_HEADING_TERMINATORS = ".,;:"


def strip_emphasis(text: str) -> str:
    """``**Bold title**`` / ``_Title_`` → ``Bold title`` (one wrapping layer)."""
    stripped = text.strip()
    match = _EMPHASIS_WRAP_RE.match(stripped)
    return match.group(2).strip() if match else stripped


def is_numbered_heading(line: str) -> bool:
    """Does this line look like a numbered section heading (``8.3 Posting``)?

    Shape checks on top of :data:`NUMBERED_HEADING_RE`: at most 14 words of
    title, no sentence-terminating punctuation, and a title that starts
    with a letter or an opening quote/bracket — so a numbered *list item*
    (``1. Install the package.``) or a decimal (``3.5 million``) is not one.
    """
    match = NUMBERED_HEADING_RE.match(line.strip())
    if match is None:
        return False
    title = match.group(2).strip()
    if not title or title[-1] in _NUMBERED_HEADING_TERMINATORS:
        return False
    if not (title[0].isalpha() or title[0] in "\"'“‘(["):
        return False
    return len(title.split()) <= _NUMBERED_HEADING_MAX_WORDS


def numbered_heading_level(number: str) -> int:
    """``"8"`` → 1, ``"8.3"`` → 2, ``"8.3.1"`` → 3 (dotted depth)."""
    return number.count(".") + 1


def compile_requirement_pattern(pattern: str) -> re.Pattern[str]:
    """Compile the configured requirement-id regex or raise ``ConfigError``.

    Never swaps in the default silently — a bad ``REQUIREMENT_ID_PATTERN``
    is a configuration error the operator must see at startup.
    """
    if not pattern or not pattern.strip():
        raise ConfigError("REQUIREMENT_ID_PATTERN invalid: pattern is empty")
    try:
        return re.compile(pattern)
    except re.error as exc:
        raise ConfigError(f"REQUIREMENT_ID_PATTERN invalid: {exc} (pattern={pattern!r})") from exc
