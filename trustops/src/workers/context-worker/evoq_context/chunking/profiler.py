"""Deterministic document profiling for chunking-strategy selection.

Scope & functionality
----------------------
``DocumentProfile`` carries every fact the strategy selector
(``evoq_context.chunking.selector``) reasons over, including every
*optional* field defined by ``AEO/contracts/profile_hints.schema.json`` —
flat dataclass fields, never nested. ``build_profile`` adopts a Canonical
Document's ``profile_hints`` verbatim when ``profile_version == 1``
(``adopt_profile_hints``) and only falls back to computing them locally
(``compute_profile``, one pass over ``splitlines()`` using the shared
regexes in ``chunking.markers``) when hints are absent or from an
unrecognized version.

Both paths also derive a handful of selector-only extras that never
appear in ``profile_hints`` itself (``avg_section_words``,
``comment_count``, ``token_estimate``, ``format_detected``,
``structural_heading_count``, ``structural_heading_density``,
``requirement_line_leading_ratio``) — these are **always** (re-)computed
locally regardless of ``hints_source``, since they depend on the
tokenizer/estimator and the Jira comment-marker grammar the context
engine — not ingestion — owns, or (for the two ``structural_heading_*``
and the line-leading ratio) because they are used as a *selector safety
net* that must not silently trust a stale or absent ingestion value.

Two defects fixed here (see the audit, §4):

1. ``code_block_count`` used a fence regex without ``re.MULTILINE``,
   so it was always 0 on any multi-line body. It now reuses
   ``markers.CODE_FENCE_RE`` (declared with ``re.MULTILINE``).
2. ``adopt_profile_hints`` used to stuff the whole ``hints`` dict into
   ``extras["raw_hints"]``, making every optional hint structurally
   unreachable by ``selector._feature_value`` (which only ever looks at
   ``profile.<name>`` or the *top level* of ``profile.extras``). Every
   schema-defined key present in ``hints`` is now copied onto the
   matching flat ``DocumentProfile`` field directly; only genuinely
   unrecognized keys land in ``extras``.

No strategy decision is made here — see ``selector.py``.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from evoq_context.chunking import markers
from evoq_context.chunking.index import DocumentIndex
from evoq_context.chunking.tokens import TokenEstimator
from evoq_context.core.limits import DEFAULT_REQUIREMENT_ID_PATTERN

__all__ = [
    "DEFAULT_REQUIREMENT_ID_PATTERN",
    "DocumentProfile",
    "adopt_profile_hints",
    "build_profile",
    "compute_profile",
]

_LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\([^)]+\)")
_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]+\)")
_SENTENCE_END_RE = re.compile(r"[.!?](?:\s|$)")
_NON_PRINTABLE_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

#: A requirement-id match's prefix is "line-leading" when nothing but
#: whitespace, a single list marker/ordinal, or a single leading table
#: pipe sits between the start of its line and the match itself — i.e.
#: the id opens the line/list-item/table-cell rather than being mentioned
#: mid-prose. Documented precisely because it drives a selector predicate
#: (``requirement_line_leading_ratio_min``).
_LEADING_PREFIX_RE = re.compile(r"^[ \t]*(?:[-*+]|\d{1,3}[.)])?[ \t]*\|?[ \t]*$")

#: A numbered section needs at least this many hits before it is trusted
#: as a heading signal (plan §2.2's ``structural_heading_count`` rule) —
#: below this, three digits-and-a-dot in a row are more likely to be a
#: version string or a stray decimal than real section numbering.
STRUCTURAL_NUMBERED_MIN = 3


@dataclass
class DocumentProfile:
    """Facts about a document body — never a strategy decision.

    Fields fall into three groups: (1) the schema's *required*
    ``profile_hints`` facts, (2) every *optional* ``profile_hints`` field
    (flat — ``adopt_profile_hints`` copies them here directly, never into
    a nested ``extras`` bag), and (3) selector-only extras that are never
    part of the ``profile_hints`` contract and are always computed
    locally regardless of ``hints_source``.
    """

    profile_version: int = 1

    # -- profile_hints: required -----------------------------------------
    char_count: int = 0
    word_count: int = 0
    line_count: int = 0
    paragraph_count: int = 0
    avg_paragraph_words: float = 0.0
    heading_count: int = 0
    heading_density: float = 0.0
    table_count: int = 0
    table_row_count: int = 0
    table_text_ratio: float = 0.0
    list_item_count: int = 0
    list_item_ratio: float = 0.0
    code_block_count: int = 0
    code_char_ratio: float = 0.0
    page_count: int | None = None
    sheet_count: int | None = None
    slide_count: int | None = None
    requirement_id_count: int = 0
    requirement_id_unique: int = 0
    requirement_id_density: float = 0.0

    # -- profile_hints: optional (flat; see module docstring, defect 2) --
    non_blank_line_count: int = 0
    max_paragraph_words: int = 0
    sentence_count: int = 0
    avg_sentence_words: float = 0.0
    heading_levels: dict[str, int] = field(default_factory=dict)
    max_heading_depth: int = 0
    numbered_section_count: int = 0
    requirement_id_prefixes: dict[str, int] = field(default_factory=dict)
    link_count: int = 0
    image_count: int = 0
    replacement_char_ratio: float = 0.0
    non_printable_ratio: float = 0.0
    is_scanned_pdf: bool = False
    language: str = "und"
    body_prefix_sha1: str = ""

    # -- selector-only extras — never adopted from profile_hints ---------
    avg_section_words: float = 0.0
    comment_count: int = 0
    token_estimate: int = 0
    format_detected: str = "markdown"
    structural_heading_count: int = 0
    structural_heading_density: float = 0.0
    requirement_line_leading_ratio: float = 0.0

    hints_source: str = "computed"  # "ingestion" | "computed"
    #: Genuinely unrecognized keys only (from a newer ingestion contract
    #: revision or a caller-supplied hint) — still reachable by
    #: ``selector._feature_value``'s fallback lookup.
    extras: dict[str, Any] = field(default_factory=dict)


#: Every ``profile_hints.schema.json`` property with a 1:1 ``DocumentProfile``
#: field (everything except ``profile_version``, handled separately). Used by
#: ``adopt_profile_hints`` to decide "flat field" vs "extras".
_SCHEMA_KNOWN_FIELDS: frozenset[str] = frozenset(
    {
        "char_count", "word_count", "line_count", "non_blank_line_count",
        "paragraph_count", "avg_paragraph_words", "max_paragraph_words",
        "sentence_count", "avg_sentence_words",
        "heading_count", "heading_levels", "max_heading_depth", "heading_density",
        "numbered_section_count",
        "table_count", "table_row_count", "table_text_ratio",
        "list_item_count", "list_item_ratio",
        "code_block_count", "code_char_ratio",
        "page_count", "sheet_count", "slide_count",
        "requirement_id_count", "requirement_id_unique", "requirement_id_density",
        "requirement_id_prefixes",
        "link_count", "image_count",
        "replacement_char_ratio", "non_printable_ratio",
        "is_scanned_pdf", "language", "body_prefix_sha1",
    }
)


# ---------------------------------------------------------------------------
# compute_profile — local one-pass scan
# ---------------------------------------------------------------------------


@dataclass
class _RawCounts:
    non_blank_line_count: int = 0
    list_item_count: int = 0
    numbered_section_count: int = 0
    table_count: int = 0
    table_row_lines: list[str] = field(default_factory=list)
    paragraph_word_counts: list[int] = field(default_factory=list)
    sentence_count: int = 0
    link_count: int = 0
    image_count: int = 0
    code_chars: int = 0


class _LineScanner:
    """Feed lines one at a time into a :class:`_RawCounts` accumulator.

    Split into small single-purpose methods (rather than one large
    ``for`` loop with a dozen branches) purely to keep each method's
    cyclomatic complexity low and readable — the classification logic
    itself is unchanged fence/table/list/heading/marker-aware line
    dispatch.
    """

    def __init__(self) -> None:
        self.counts = _RawCounts()
        self._in_code = False
        self._prev_table_row = False
        self._para_open = False
        self._para_words = 0

    def feed(self, ln: str) -> None:
        """Classify one line and update the running counts."""
        stripped = ln.strip()
        is_blank = stripped == ""
        if not is_blank:
            self.counts.non_blank_line_count += 1

        if markers.CODE_FENCE_RE.match(ln):
            self._toggle_fence()
            return
        if self._in_code:
            self.counts.code_chars += len(ln) + 1
            return
        if is_blank:
            self._close_paragraph()
            self._prev_table_row = False
            return
        if self._is_non_prose_structural(ln):
            self._close_paragraph()
            self._prev_table_row = False
            return
        if markers.TABLE_ROW_RE.match(ln):
            self._feed_table_row(ln)
            return
        self._prev_table_row = False
        if markers.LIST_ITEM_RE.match(ln):
            self._feed_list_item(ln)
            return
        self._feed_prose_line(ln, stripped)

    def finish(self) -> _RawCounts:
        """Flush any open paragraph and return the accumulated counts."""
        self._close_paragraph()
        return self.counts

    def _toggle_fence(self) -> None:
        self._in_code = not self._in_code
        self._close_paragraph()
        self._prev_table_row = False

    @staticmethod
    def _is_non_prose_structural(ln: str) -> bool:
        """Marker, comment header or ATX heading — never paragraph text."""
        return bool(
            markers.STRUCTURAL_MARKER_RE.match(ln)
            or markers.COMMENT_MARKER_RE.match(ln)
            or markers.HEADING_RE.match(ln)
        )

    def _feed_table_row(self, ln: str) -> None:
        if not self._prev_table_row:
            self.counts.table_count += 1
        self._prev_table_row = True
        if not markers.TABLE_SEP_RE.match(ln):
            self.counts.table_row_lines.append(ln)
        self._close_paragraph()

    def _feed_list_item(self, ln: str) -> None:
        self.counts.list_item_count += 1
        self._close_paragraph()
        self._count_inline(ln)

    def _feed_prose_line(self, ln: str, stripped: str) -> None:
        if markers.is_numbered_heading(ln):
            self.counts.numbered_section_count += 1
        self._para_open = True
        self._para_words += len(stripped.split())
        self._count_inline(ln)

    def _count_inline(self, ln: str) -> None:
        self.counts.link_count += len(_LINK_RE.findall(ln))
        self.counts.image_count += len(_IMAGE_RE.findall(ln))
        self.counts.sentence_count += len(_SENTENCE_END_RE.findall(ln))

    def _close_paragraph(self) -> None:
        if self._para_open:
            self.counts.paragraph_word_counts.append(self._para_words)
            self._para_words = 0
            self._para_open = False


def _classify_and_count(text: str) -> _RawCounts:
    """Run one pass over ``text.splitlines()`` via :class:`_LineScanner`.

    Covers everything that needs line classification (fence/table/list/
    heading/marker-aware) — the counts ``compute_profile`` cannot get
    from a single whole-text regex pass.
    """
    scanner = _LineScanner()
    for ln in text.splitlines():
        scanner.feed(ln)
    return scanner.finish()


def _headings(text: str) -> tuple[int, dict[str, int]]:
    """ATX heading count + ``{level: count}`` via ``markers.HEADING_RE``."""
    levels: dict[str, int] = {}
    count = 0
    for m in markers.HEADING_RE.finditer(text):
        level = str(len(m.group(1)))
        levels[level] = levels.get(level, 0) + 1
        count += 1
    return count, levels


def _requirement_ids(text: str, pattern: str) -> list[str]:
    rx = re.compile(pattern)
    return [m.group(1) if m.groups() else m.group(0) for m in rx.finditer(text)]


def _prefix_from_match(match: str) -> str:
    """Prefix = the match up to (and excluding) the last '-' segment."""
    idx = match.rfind("-")
    return match[:idx] if idx > 0 else match


def _top_prefixes(ids: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for i in ids:
        prefix = _prefix_from_match(i)
        counts[prefix] = counts.get(prefix, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:5])


def _is_line_leading_prefix(prefix: str) -> bool:
    return bool(_LEADING_PREFIX_RE.match(prefix))


def _requirement_line_leading_ratio(
    text: str,
    pattern: str,
    *,
    index: DocumentIndex | None = None,
) -> float:
    """Compute the fraction of requirement-id hits that open their line.

    A hit that is mid-prose/mid-cell (see ``_LEADING_PREFIX_RE``) is not
    "line-leading". Uses a pre-built ``DocumentIndex.requirement_hits``
    (from lane C-B) when one is supplied; otherwise does its own
    lightweight per-match line scan. Either path is O(n) and
    deterministic.
    """
    if index is not None and index.requirement_hits:
        leading = sum(1 for hit in index.requirement_hits if hit.line_leading)
        return round(leading / len(index.requirement_hits), 4)

    rx = re.compile(pattern)
    lines = text.splitlines(keepends=True)
    line_starts: list[int] = []
    pos = 0
    for ln in lines:
        line_starts.append(pos)
        pos += len(ln)

    total = 0
    leading = 0
    line_idx = 0
    for m in rx.finditer(text):
        while line_idx + 1 < len(line_starts) and line_starts[line_idx + 1] <= m.start():
            line_idx += 1
        line_start = line_starts[line_idx] if line_starts else 0
        prefix = text[line_start : m.start()]
        total += 1
        if _is_line_leading_prefix(prefix):
            leading += 1
    return round(leading / total, 4) if total else 0.0


def _avg_section_words(word_count: int, structural_heading_count: int) -> float:
    if structural_heading_count <= 0:
        return float(word_count)
    return round(word_count / (structural_heading_count + 1), 2)


def _selector_extras(
    text: str,
    *,
    heading_count: int,
    numbered_section_count: int,
    word_count: int,
    requirement_id_pattern: str,
    estimator: TokenEstimator | None,
    format_detected: str,
    index: DocumentIndex | None,
) -> dict[str, Any]:
    """Compute the selector-only extras that never appear in ``profile_hints``.

    Always (re-)computed locally, never adopted from ingestion's hints.
    """
    structural_heading_count = heading_count + (
        numbered_section_count if numbered_section_count >= STRUCTURAL_NUMBERED_MIN else 0
    )
    structural_heading_density = (
        round(structural_heading_count / word_count * 1000, 3) if word_count else 0.0
    )
    return {
        "avg_section_words": _avg_section_words(word_count, structural_heading_count),
        "comment_count": len(markers.COMMENT_MARKER_RE.findall(text)),
        "token_estimate": estimator.count(text) if estimator else 0,
        "format_detected": format_detected,
        "structural_heading_count": structural_heading_count,
        "structural_heading_density": structural_heading_density,
        "requirement_line_leading_ratio": _requirement_line_leading_ratio(
            text, requirement_id_pattern, index=index
        ),
    }


def compute_profile(
    body: str,
    *,
    requirement_id_pattern: str = DEFAULT_REQUIREMENT_ID_PATTERN,
    estimator: TokenEstimator | None = None,
    format_detected: str = "markdown",
    index: DocumentIndex | None = None,
) -> DocumentProfile:
    """Compute a :class:`DocumentProfile` locally, one pass over the body.

    ``is_scanned_pdf`` is always ``False`` here: detecting a scanned PDF
    needs per-page image/text-layer information this text-only scan does
    not have — that flag is only ever meaningful when it arrives via
    :func:`adopt_profile_hints` from ingestion, which did see the pages.
    """
    text = body or ""
    char_count = len(text)
    words = text.split()
    word_count = len(words)
    line_count = len(text.splitlines())

    counts = _classify_and_count(text)
    heading_count, heading_levels = _headings(text)
    max_heading_depth = max((int(k) for k in heading_levels), default=0)
    heading_density = round(heading_count / word_count * 1000, 3) if word_count else 0.0

    table_row_count = len(counts.table_row_lines)
    table_chars = sum(len(ln) for ln in counts.table_row_lines)
    table_text_ratio = round(table_chars / char_count, 4) if char_count else 0.0

    list_item_ratio = (
        round(counts.list_item_count / counts.non_blank_line_count, 4)
        if counts.non_blank_line_count
        else 0.0
    )

    # Defect fix: markers.CODE_FENCE_RE is declared with re.MULTILINE, so
    # this correctly counts every fence line in a multi-line body (the old
    # local regex lacked the flag and always returned 0 for >1 fence pair).
    code_block_count = len(markers.CODE_FENCE_RE.findall(text)) // 2
    code_char_ratio = round(counts.code_chars / char_count, 4) if char_count else 0.0

    paragraph_count = len(counts.paragraph_word_counts)
    avg_paragraph_words = (
        round(sum(counts.paragraph_word_counts) / paragraph_count, 2) if paragraph_count else 0.0
    )
    max_paragraph_words = max(counts.paragraph_word_counts, default=0)

    avg_sentence_words = (
        round(word_count / counts.sentence_count, 2) if counts.sentence_count else 0.0
    )

    page_count = len(markers.PAGE_MARKER_RE.findall(text)) or None
    sheet_count = len(set(markers.SHEET_MARKER_RE.findall(text))) or None
    slide_count = len(markers.SLIDE_MARKER_RE.findall(text)) or None

    req_ids = _requirement_ids(text, requirement_id_pattern)
    requirement_id_count = len(req_ids)
    requirement_id_unique = len(set(req_ids))
    requirement_id_density = (
        round(requirement_id_count / word_count * 1000, 3) if word_count else 0.0
    )
    requirement_id_prefixes = _top_prefixes(req_ids)

    replacement_chars = text.count("\N{REPLACEMENT CHARACTER}")
    replacement_char_ratio = round(replacement_chars / char_count, 5) if char_count else 0.0
    non_printable_ratio = (
        round(len(_NON_PRINTABLE_RE.findall(text)) / char_count, 5) if char_count else 0.0
    )
    body_prefix_sha1 = hashlib.sha1(  # noqa: S324 - fingerprint, not security
        text[:256].encode("utf-8", "replace")
    ).hexdigest()

    selector_extras = _selector_extras(
        text,
        heading_count=heading_count,
        numbered_section_count=counts.numbered_section_count,
        word_count=word_count,
        requirement_id_pattern=requirement_id_pattern,
        estimator=estimator,
        format_detected=format_detected,
        index=index,
    )

    return DocumentProfile(
        profile_version=1,
        char_count=char_count,
        word_count=word_count,
        line_count=line_count,
        paragraph_count=paragraph_count,
        avg_paragraph_words=avg_paragraph_words,
        heading_count=heading_count,
        heading_density=heading_density,
        table_count=counts.table_count,
        table_row_count=table_row_count,
        table_text_ratio=table_text_ratio,
        list_item_count=counts.list_item_count,
        list_item_ratio=list_item_ratio,
        code_block_count=code_block_count,
        code_char_ratio=code_char_ratio,
        page_count=page_count,
        sheet_count=sheet_count,
        slide_count=slide_count,
        requirement_id_count=requirement_id_count,
        requirement_id_unique=requirement_id_unique,
        requirement_id_density=requirement_id_density,
        non_blank_line_count=counts.non_blank_line_count,
        max_paragraph_words=max_paragraph_words,
        sentence_count=counts.sentence_count,
        avg_sentence_words=avg_sentence_words,
        heading_levels=heading_levels,
        max_heading_depth=max_heading_depth,
        numbered_section_count=counts.numbered_section_count,
        requirement_id_prefixes=requirement_id_prefixes,
        link_count=counts.link_count,
        image_count=counts.image_count,
        replacement_char_ratio=replacement_char_ratio,
        non_printable_ratio=non_printable_ratio,
        is_scanned_pdf=False,
        language="und",
        body_prefix_sha1=body_prefix_sha1,
        hints_source="computed",
        **selector_extras,
    )


# ---------------------------------------------------------------------------
# adopt_profile_hints — trust ingestion's facts, flat
# ---------------------------------------------------------------------------


def _req_int(hints: dict[str, Any], key: str, default: int = 0) -> int:
    value = hints.get(key, default)
    return int(value) if value is not None else default


def _req_float(hints: dict[str, Any], key: str, default: float = 0.0) -> float:
    value = hints.get(key, default)
    return float(value) if value is not None else default


def _opt_int(hints: dict[str, Any], key: str) -> int | None:
    value = hints.get(key)
    return int(value) if value is not None else None


def _str_int_dict(hints: dict[str, Any], key: str) -> dict[str, int]:
    raw = hints.get(key) or {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): int(v) for k, v in raw.items()}


def adopt_profile_hints(
    hints: dict[str, Any],
    *,
    body: str,
    estimator: TokenEstimator | None = None,
    format_detected: str = "markdown",
    requirement_id_pattern: str = DEFAULT_REQUIREMENT_ID_PATTERN,
    index: DocumentIndex | None = None,
) -> DocumentProfile:
    """Adopt an ingestion-computed ``profile_hints`` dict (``profile_version==1``).

    Every schema-defined key present in ``hints`` is copied onto the
    matching flat :class:`DocumentProfile` field (this is the fix for the
    ``extras["raw_hints"]`` bug — see the module docstring); anything else
    in ``hints`` is genuinely unrecognized and goes into ``extras``.
    """
    text = body or ""
    extras = {
        key: value
        for key, value in hints.items()
        if key not in _SCHEMA_KNOWN_FIELDS and key != "profile_version"
    }

    heading_count = _req_int(hints, "heading_count")
    word_count = _req_int(hints, "word_count")
    numbered_section_count = _req_int(hints, "numbered_section_count")

    selector_extras = _selector_extras(
        text,
        heading_count=heading_count,
        numbered_section_count=numbered_section_count,
        word_count=word_count,
        requirement_id_pattern=requirement_id_pattern,
        estimator=estimator,
        format_detected=format_detected,
        index=index,
    )

    return DocumentProfile(
        profile_version=1,
        char_count=_req_int(hints, "char_count"),
        word_count=word_count,
        line_count=_req_int(hints, "line_count"),
        paragraph_count=_req_int(hints, "paragraph_count"),
        avg_paragraph_words=_req_float(hints, "avg_paragraph_words"),
        heading_count=heading_count,
        heading_density=_req_float(hints, "heading_density"),
        table_count=_req_int(hints, "table_count"),
        table_row_count=_req_int(hints, "table_row_count"),
        table_text_ratio=_req_float(hints, "table_text_ratio"),
        list_item_count=_req_int(hints, "list_item_count"),
        list_item_ratio=_req_float(hints, "list_item_ratio"),
        code_block_count=_req_int(hints, "code_block_count"),
        code_char_ratio=_req_float(hints, "code_char_ratio"),
        page_count=_opt_int(hints, "page_count"),
        sheet_count=_opt_int(hints, "sheet_count"),
        slide_count=_opt_int(hints, "slide_count"),
        requirement_id_count=_req_int(hints, "requirement_id_count"),
        requirement_id_unique=_req_int(hints, "requirement_id_unique"),
        requirement_id_density=_req_float(hints, "requirement_id_density"),
        non_blank_line_count=_req_int(hints, "non_blank_line_count"),
        max_paragraph_words=_req_int(hints, "max_paragraph_words"),
        sentence_count=_req_int(hints, "sentence_count"),
        avg_sentence_words=_req_float(hints, "avg_sentence_words"),
        heading_levels=_str_int_dict(hints, "heading_levels"),
        max_heading_depth=_req_int(hints, "max_heading_depth"),
        numbered_section_count=numbered_section_count,
        requirement_id_prefixes=_str_int_dict(hints, "requirement_id_prefixes"),
        link_count=_req_int(hints, "link_count"),
        image_count=_req_int(hints, "image_count"),
        replacement_char_ratio=_req_float(hints, "replacement_char_ratio"),
        non_printable_ratio=_req_float(hints, "non_printable_ratio"),
        is_scanned_pdf=bool(hints.get("is_scanned_pdf", False)),
        language=str(hints.get("language") or "und"),
        body_prefix_sha1=str(hints.get("body_prefix_sha1") or ""),
        hints_source="ingestion",
        extras=extras,
        **selector_extras,
    )


def build_profile(
    body: str,
    *,
    profile_hints: dict[str, Any] | None = None,
    requirement_id_pattern: str = DEFAULT_REQUIREMENT_ID_PATTERN,
    estimator: TokenEstimator | None = None,
    format_detected: str = "markdown",
    index: DocumentIndex | None = None,
) -> DocumentProfile:
    """Adopt ``profile_hints`` when ``profile_version == 1``, else compute locally."""
    if isinstance(profile_hints, dict) and profile_hints.get("profile_version") == 1:
        return adopt_profile_hints(
            profile_hints,
            body=body,
            estimator=estimator,
            format_detected=format_detected,
            requirement_id_pattern=requirement_id_pattern,
            index=index,
        )
    return compute_profile(
        body,
        requirement_id_pattern=requirement_id_pattern,
        estimator=estimator,
        format_detected=format_detected,
        index=index,
    )
