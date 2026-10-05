"""``ProfileScanner`` / ``compute_profile`` — deterministic, O(n) facts about a body.

Scope & functionality
---------------------
Implements ``profile_hints`` (``AEO/contracts/profile_hints.schema.json``):
one ``MarkdownScanner`` pass over the canonical body (the same grammar the
renderer writes) feeding a handful of small counters — ``_HeadingStats``,
``_TableStats``, ``_ParagraphStats``, ``_RequirementIdStats``,
``_TextQualityStats`` — each owning one family of fields. Produces **facts
only**, never a chunking-strategy decision. When an extractor already knows
authoritative counts (a PDF's real page count) they arrive via
``structure`` and win over text-derived values.

``compute_profile`` is the stable functional wrapper every caller uses;
``extract_requirement_ids`` is the one deduplicating id scanner shared with
``model/structure.py`` and the Confluence/Jira sources.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from evoq_ingestion.core.config import DEFAULT_REQUIREMENT_ID_PATTERN
from evoq_ingestion.core.markdown_scan import MarkdownScan, MarkdownScanner

PROFILE_VERSION = 1

_NON_PRINTABLE_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _prefix_from_match(match: str) -> str:
    """Prefix = the match up to (and excluding) the last '-' segment."""
    idx = match.rfind("-")
    return match[:idx] if idx > 0 else match


def extract_requirement_ids(
    text: str,
    pattern: str = DEFAULT_REQUIREMENT_ID_PATTERN,
    limit: int = 500,
) -> list[str]:
    """Deduped requirement ids in document order, capped at ``limit``."""
    seen: list[str] = []
    seen_set: set[str] = set()
    regex = re.compile(pattern)
    for m in regex.finditer(text or ""):
        value = m.group(1) if m.groups() else m.group(0)
        if value not in seen_set:
            seen_set.add(value)
            seen.append(value)
        if len(seen) >= limit:
            break
    return seen


# --------------------------------------------------------------- counters
@dataclass(frozen=True, slots=True)
class _HeadingStats:
    heading_count: int
    heading_levels: dict[str, int]
    max_heading_depth: int
    heading_density: float
    numbered_section_count: int

    @classmethod
    def compute(cls, scan: MarkdownScan, word_count: int, override: int | None) -> _HeadingStats:
        count = scan.heading_count if override is None else override
        return cls(
            heading_count=count,
            heading_levels={str(k): v for k, v in sorted(scan.heading_levels.items())},
            max_heading_depth=scan.max_heading_depth,
            heading_density=(count / word_count * 1000) if word_count else 0.0,
            numbered_section_count=scan.numbered_section_count,
        )


@dataclass(frozen=True, slots=True)
class _TableStats:
    table_count: int
    table_row_count: int
    table_text_ratio: float

    @classmethod
    def compute(cls, scan: MarkdownScan, char_count: int, override: int | None) -> _TableStats:
        return cls(
            table_count=scan.table_block_count if override is None else override,
            table_row_count=scan.table_row_count,
            table_text_ratio=(scan.table_chars / char_count) if char_count else 0.0,
        )


@dataclass(frozen=True, slots=True)
class _ParagraphStats:
    paragraph_count: int
    avg_paragraph_words: float
    max_paragraph_words: int
    sentence_count: int
    avg_sentence_words: float
    list_item_count: int
    list_item_ratio: float
    code_block_count: int
    code_char_ratio: float
    link_count: int
    image_count: int

    @classmethod
    def compute(
        cls,
        scan: MarkdownScan,
        word_count: int,
        char_count: int,
        *,
        list_override: int | None,
        code_override: int | None,
    ) -> _ParagraphStats:
        counts = scan.paragraph_word_counts
        list_items = scan.list_item_count if list_override is None else list_override
        return cls(
            paragraph_count=len(counts),
            avg_paragraph_words=(sum(counts) / len(counts)) if counts else 0.0,
            max_paragraph_words=max(counts) if counts else 0,
            sentence_count=scan.sentence_count,
            avg_sentence_words=(word_count / scan.sentence_count) if scan.sentence_count else 0.0,
            list_item_count=list_items,
            list_item_ratio=(
                (list_items / scan.non_blank_line_count) if scan.non_blank_line_count else 0.0
            ),
            code_block_count=scan.code_block_count if code_override is None else code_override,
            code_char_ratio=(scan.code_chars / char_count) if char_count else 0.0,
            link_count=scan.link_count,
            image_count=scan.image_count,
        )


@dataclass(frozen=True, slots=True)
class _RequirementIdStats:
    requirement_id_count: int
    requirement_id_unique: int
    requirement_id_density: float
    requirement_id_prefixes: dict[str, int]

    @classmethod
    def compute(cls, text: str, pattern: str, word_count: int) -> _RequirementIdStats:
        matches = [m.group(1) if m.groups() else m.group(0) for m in re.finditer(pattern, text)]
        prefix_counts: dict[str, int] = {}
        for m in matches:
            prefix = _prefix_from_match(m)
            prefix_counts[prefix] = prefix_counts.get(prefix, 0) + 1
        top = dict(sorted(prefix_counts.items(), key=lambda kv: kv[1], reverse=True)[:5])
        return cls(
            requirement_id_count=len(matches),
            requirement_id_unique=len(set(matches)),
            requirement_id_density=(len(matches) / word_count * 1000) if word_count else 0.0,
            requirement_id_prefixes=top,
        )


@dataclass(frozen=True, slots=True)
class _TextQualityStats:
    replacement_char_ratio: float
    non_printable_ratio: float
    language: str
    body_prefix_sha1: str

    @classmethod
    def compute(cls, text: str, *, detect_language: bool, word_count: int) -> _TextQualityStats:
        char_count = len(text)
        language = "und"
        if detect_language and word_count:
            ascii_ratio = sum(1 for c in text if ord(c) < 128) / max(char_count, 1)
            language = "en" if ascii_ratio > 0.9 else "und"
        return cls(
            replacement_char_ratio=(text.count("�") / char_count) if char_count else 0.0,
            non_printable_ratio=(
                len(_NON_PRINTABLE_RE.findall(text)) / char_count if char_count else 0.0
            ),
            language=language,
            body_prefix_sha1=hashlib.sha1(  # noqa: S324 - fingerprint, not security
                text[:256].encode("utf-8", "replace")
            ).hexdigest(),
        )


# ------------------------------------------------------------------ result
@dataclass(slots=True)
class ProfileHints:
    """Typed ``profile_hints``; ``to_dict`` emits the schema shape (rounded)."""

    char_count: int = 0
    word_count: int = 0
    line_count: int = 0
    non_blank_line_count: int = 0
    paragraph_count: int = 0
    avg_paragraph_words: float = 0.0
    max_paragraph_words: int = 0
    sentence_count: int = 0
    avg_sentence_words: float = 0.0
    heading_count: int = 0
    heading_levels: dict[str, int] = field(default_factory=dict)
    max_heading_depth: int = 0
    heading_density: float = 0.0
    numbered_section_count: int = 0
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
    requirement_id_prefixes: dict[str, int] = field(default_factory=dict)
    link_count: int = 0
    image_count: int = 0
    replacement_char_ratio: float = 0.0
    non_printable_ratio: float = 0.0
    is_scanned_pdf: bool = False
    language: str = "und"
    body_prefix_sha1: str = ""

    def to_dict(self) -> dict[str, Any]:
        """The ``profile_hints`` document (``profile_version`` first)."""
        return {
            "profile_version": PROFILE_VERSION,
            "char_count": self.char_count,
            "word_count": self.word_count,
            "line_count": self.line_count,
            "non_blank_line_count": self.non_blank_line_count,
            "paragraph_count": self.paragraph_count,
            "avg_paragraph_words": round(self.avg_paragraph_words, 2),
            "max_paragraph_words": self.max_paragraph_words,
            "sentence_count": self.sentence_count,
            "avg_sentence_words": round(self.avg_sentence_words, 2),
            "heading_count": self.heading_count,
            "heading_levels": dict(self.heading_levels),
            "max_heading_depth": self.max_heading_depth,
            "heading_density": round(self.heading_density, 3),
            "numbered_section_count": self.numbered_section_count,
            "table_count": self.table_count,
            "table_row_count": self.table_row_count,
            "table_text_ratio": round(self.table_text_ratio, 4),
            "list_item_count": self.list_item_count,
            "list_item_ratio": round(self.list_item_ratio, 4),
            "code_block_count": self.code_block_count,
            "code_char_ratio": round(self.code_char_ratio, 4),
            "page_count": self.page_count,
            "sheet_count": self.sheet_count,
            "slide_count": self.slide_count,
            "requirement_id_count": self.requirement_id_count,
            "requirement_id_unique": self.requirement_id_unique,
            "requirement_id_density": round(self.requirement_id_density, 3),
            "requirement_id_prefixes": dict(self.requirement_id_prefixes),
            "link_count": self.link_count,
            "image_count": self.image_count,
            "replacement_char_ratio": round(self.replacement_char_ratio, 5),
            "non_printable_ratio": round(self.non_printable_ratio, 5),
            "is_scanned_pdf": self.is_scanned_pdf,
            "language": self.language,
            "body_prefix_sha1": self.body_prefix_sha1,
        }


class ProfileScanner:
    """Compose the counters into a ``ProfileHints`` for one body."""

    def __init__(
        self,
        *,
        requirement_id_pattern: str = DEFAULT_REQUIREMENT_ID_PATTERN,
        detect_language: bool = False,
    ) -> None:
        """Configure the requirement-id pattern and whether to guess a language."""
        self._pattern = requirement_id_pattern
        self._detect_language = detect_language
        self._scanner = MarkdownScanner()

    def scan(
        self,
        body_markdown: str,
        structure: Mapping[str, Any] | None = None,
        raw_format: str | None = None,
        *,
        is_scanned_pdf: bool | None = None,
    ) -> ProfileHints:
        """Profile *body_markdown*; ``structure`` values override text-derived counts."""
        del raw_format  # accepted for signature stability; facts never depend on it
        structure = structure or {}
        text = body_markdown or ""
        char_count = len(text)
        word_count = len(text.split())
        scan = self._scanner.scan(text)

        headings = _HeadingStats.compute(scan, word_count, structure.get("heading_count"))
        tables = _TableStats.compute(scan, char_count, structure.get("table_count"))
        paragraphs = _ParagraphStats.compute(
            scan,
            word_count,
            char_count,
            list_override=structure.get("list_item_count"),
            code_override=structure.get("code_block_count"),
        )
        req_ids = _RequirementIdStats.compute(text, self._pattern, word_count)
        quality = _TextQualityStats.compute(
            text, detect_language=self._detect_language, word_count=word_count
        )

        return ProfileHints(
            char_count=char_count,
            word_count=word_count,
            line_count=scan.line_count,
            non_blank_line_count=scan.non_blank_line_count,
            paragraph_count=paragraphs.paragraph_count,
            avg_paragraph_words=paragraphs.avg_paragraph_words,
            max_paragraph_words=paragraphs.max_paragraph_words,
            sentence_count=paragraphs.sentence_count,
            avg_sentence_words=paragraphs.avg_sentence_words,
            heading_count=headings.heading_count,
            heading_levels=headings.heading_levels,
            max_heading_depth=headings.max_heading_depth,
            heading_density=headings.heading_density,
            numbered_section_count=headings.numbered_section_count,
            table_count=tables.table_count,
            table_row_count=tables.table_row_count,
            table_text_ratio=tables.table_text_ratio,
            list_item_count=paragraphs.list_item_count,
            list_item_ratio=paragraphs.list_item_ratio,
            code_block_count=paragraphs.code_block_count,
            code_char_ratio=paragraphs.code_char_ratio,
            page_count=structure.get("page_count", len(scan.page_numbers) or None),
            sheet_count=structure.get("sheet_count", scan.sheet_count or None),
            slide_count=structure.get("slide_count", len(scan.slide_numbers) or None),
            requirement_id_count=req_ids.requirement_id_count,
            requirement_id_unique=req_ids.requirement_id_unique,
            requirement_id_density=req_ids.requirement_id_density,
            requirement_id_prefixes=req_ids.requirement_id_prefixes,
            link_count=paragraphs.link_count,
            image_count=paragraphs.image_count,
            replacement_char_ratio=quality.replacement_char_ratio,
            non_printable_ratio=quality.non_printable_ratio,
            is_scanned_pdf=bool(is_scanned_pdf) if is_scanned_pdf is not None else False,
            language=quality.language,
            body_prefix_sha1=quality.body_prefix_sha1,
        )


def compute_profile(
    body_markdown: str,
    structure: Mapping[str, Any] | None = None,
    raw_format: str | None = None,
    *,
    requirement_id_pattern: str = DEFAULT_REQUIREMENT_ID_PATTERN,
    detect_language: bool = False,
    is_scanned_pdf: bool | None = None,
) -> dict[str, Any]:
    """Compute ``profile_hints`` for a canonical Markdown body (functional wrapper)."""
    scanner = ProfileScanner(
        requirement_id_pattern=requirement_id_pattern, detect_language=detect_language
    )
    return scanner.scan(
        body_markdown, structure, raw_format, is_scanned_pdf=is_scanned_pdf
    ).to_dict()
