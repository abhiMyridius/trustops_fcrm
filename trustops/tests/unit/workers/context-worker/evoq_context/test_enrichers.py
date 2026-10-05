"""Tests for ``chunking/enrichers/*`` — the cross-strategy metadata fixes.

The two defects the design doc calls out by name are both exercised here:
(1) heading hierarchy is document-global, so a heading on one page and a
sub-heading after a later page marker still nest correctly
(``HierarchyEnricher`` no longer resets at a page boundary); (2) every
enricher runs on *every* chunk regardless of which segmenter produced it,
so a chunk shaped like a ``page_windows`` window still gets
``requirement_ids`` from ``RequirementIdEnricher`` — an enricher that has
nothing to do with pages.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from evoq_context.chunking.enrichers.finalize import TypeResolver
from evoq_context.chunking.enrichers.hierarchy import HierarchyEnricher
from evoq_context.chunking.enrichers.page import PageEnricher
from evoq_context.chunking.enrichers.requirement import RequirementIdEnricher
from evoq_context.chunking.enrichers.sheet import SheetEnricher
from evoq_context.chunking.enrichers.slide import SlideEnricher
from evoq_context.chunking.enrichers.table import TableEnricher
from evoq_context.chunking.index import DocumentIndex
from evoq_context.chunking.markers import compile_requirement_pattern
from evoq_context.chunking.types import SourceDocument, StructuredChunk
from evoq_context.core import limits

REQ_PATTERN = compile_requirement_pattern(limits.DEFAULT_REQUIREMENT_ID_PATTERN)


def _doc(
    text: str, *, title: str = "", source_type: str = "file", file_path=None
) -> SourceDocument:
    index = DocumentIndex.build(text, REQ_PATTERN)
    return SourceDocument(
        text=text,
        source_type=source_type,
        source_id="doc-1",
        title=title,
        file_path=file_path,
        metadata={},
        profile=None,  # type: ignore[arg-type]  # unused by these enrichers
        index=index,
        requirement_pattern=REQ_PATTERN,
    )


def _chunk(text: str, start: int, end: int) -> StructuredChunk:
    return StructuredChunk(text=text, char_start=start, char_end=end)


# ---------------------------------------------------------------------------
# HierarchyEnricher — document-global nesting across a page boundary
# ---------------------------------------------------------------------------


def test_hierarchy_nests_across_page_boundary() -> None:
    text = (
        "# 8. Transactions\n\n"
        "Intro to the transactions chapter.\n\n"
        "--- Page 38 ---\n\n"
        "Body text on page 38.\n\n"
        "## 8.3 Posting\n\n"
        "--- Page 40 ---\n\n"
        "Posting continues on page 40 with no new heading here.\n"
    )
    doc = _doc(text)
    page40_start = text.index("Posting continues")
    chunk = _chunk("Posting continues on page 40 ...", page40_start, page40_start + 30)

    HierarchyEnricher().enrich(doc, chunk)

    assert chunk.metadata["section"] == "8.3 Posting"
    assert chunk.metadata["heading_level"] == 2
    assert chunk.metadata["section_path"] == "8. Transactions > 8.3 Posting"


def test_hierarchy_preamble_uses_title_when_set() -> None:
    text = "Preamble text before any heading.\n\n# First Heading\n\nBody.\n"
    doc = _doc(text, title="My Document")
    chunk = _chunk("Preamble text", 0, 13)

    HierarchyEnricher().enrich(doc, chunk)

    assert chunk.metadata["section_path"] == "My Document"
    assert "section" not in chunk.metadata


def test_hierarchy_preamble_omits_keys_without_title() -> None:
    text = "Preamble text before any heading.\n\n# First Heading\n\nBody.\n"
    doc = _doc(text)
    chunk = _chunk("Preamble text", 0, 13)

    HierarchyEnricher().enrich(doc, chunk)

    assert "section_path" not in chunk.metadata
    assert "section" not in chunk.metadata


# ---------------------------------------------------------------------------
# PageEnricher / SheetEnricher / SlideEnricher
# ---------------------------------------------------------------------------


def test_page_enricher_omits_key_before_first_marker() -> None:
    text = "Preamble.\n\n--- Page 1 ---\n\nBody.\n"
    doc = _doc(text)
    chunk = _chunk("Preamble.", 0, 9)

    PageEnricher().enrich(doc, chunk)

    assert "page" not in chunk.metadata


def test_page_enricher_uses_last_marker_at_or_before() -> None:
    text = "--- Page 1 ---\n\nOne.\n\n--- Page 2 ---\n\nTwo.\n"
    doc = _doc(text)
    two_start = text.index("Two.")
    chunk = _chunk("Two.", two_start, two_start + 4)

    PageEnricher().enrich(doc, chunk)

    assert chunk.metadata["page"] == 2


def test_sheet_and_slide_enrichers() -> None:
    text = "--- Sheet: Budget ---\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n"
    doc = _doc(text)
    row_start = text.index("| 1 | 2 |")
    chunk = _chunk("| 1 | 2 |", row_start, row_start + 9)
    SheetEnricher().enrich(doc, chunk)
    assert chunk.metadata["sheet"] == "Budget"

    slide_text = "--- Slide 3 ---\n\nWelcome.\n"
    slide_doc = _doc(slide_text)
    welcome_start = slide_text.index("Welcome")
    slide_chunk = _chunk("Welcome.", welcome_start, welcome_start + 8)
    SlideEnricher().enrich(slide_doc, slide_chunk)
    assert slide_chunk.metadata["slide"] == 3


# ---------------------------------------------------------------------------
# RequirementIdEnricher — cross-strategy tagging (the P0-3/P0-5 fix)
# ---------------------------------------------------------------------------


def test_requirement_id_enricher_tags_a_page_windows_shaped_chunk() -> None:
    """A chunk shaped exactly like what ``PageSegmenter`` would emit (one
    page's worth of text, nothing requirement-specific about it) still gets
    ``requirement_ids`` — proving the enricher doesn't care which segmenter
    produced the chunk."""
    text = (
        "--- Page 1 ---\n\n"
        "REQ-777: the system shall do the thing.\n\n"
        "--- Page 2 ---\n\n"
        "Unrelated content with no ids on this page.\n"
    )
    doc = _doc(text)
    page1_start = text.index("REQ-777")
    page1_end = text.index("--- Page 2")
    chunk = _chunk(text[page1_start:page1_end], page1_start, page1_end)

    RequirementIdEnricher().enrich(doc, chunk)

    assert chunk.metadata["requirement_ids"] == "REQ-777"


def test_requirement_id_enricher_block_inheritance_does_not_cross_a_marker() -> None:
    """Regression: a line-leading id with no *other* line-leading id after
    it must not have its 'block' swallow every chunk to the end of the
    document — it stops at the next structural boundary (here, the next
    page marker)."""
    text = (
        "--- Page 1 ---\n\n"
        "REQ-1: the only requirement in this document.\n\n"
        "--- Page 2 ---\n\n"
        "Later, unrelated content far from any requirement.\n"
    )
    doc = _doc(text)
    page2_start = text.index("Later, unrelated")
    chunk = _chunk(text[page2_start:], page2_start, len(text))

    RequirementIdEnricher().enrich(doc, chunk)

    assert "requirement_ids" not in chunk.metadata


def test_requirement_id_enricher_windowed_block_inherits_full_id_set() -> None:
    """One line-leading block mentioning a second id inline (not
    line-leading, so it never starts its own block); a chunk covering only
    the tail of that one line still inherits the *block's* full id set,
    including the line-leading id it doesn't itself contain."""
    text = (
        "REQ-1: first part of one big block, continuing with more filler "
        "words here and then REQ-2 mentioned inline without being "
        "line-leading itself.\n"
    )
    doc = _doc(text)
    tail_start = text.index("REQ-2 mentioned")
    chunk = _chunk(text[tail_start:], tail_start, len(text))

    RequirementIdEnricher().enrich(doc, chunk)

    ids = set(chunk.metadata["requirement_ids"].split(","))
    assert ids == {"REQ-1", "REQ-2"}


# ---------------------------------------------------------------------------
# TableEnricher — columns/row_range/row_count + the header backstop
# ---------------------------------------------------------------------------


def test_table_enricher_sets_columns_and_row_range() -> None:
    text = "| A | B |\n| --- | --- |\n| 1 | 2 |\n| 3 | 4 |\n"
    doc = _doc(text)
    row1_start = text.index("| 1 | 2 |")
    chunk = _chunk(text[row1_start:], row1_start, len(text))

    TableEnricher().enrich(doc, chunk)

    assert chunk.metadata["columns"] == "A,B"
    assert chunk.metadata["row_range"] == "1-2"
    assert chunk.metadata["row_count"] == 2


def test_table_enricher_prepends_a_missing_header() -> None:
    text = "| A | B |\n| --- | --- |\n| 1 | 2 |\n"
    doc = _doc(text)
    row_start = text.index("| 1 | 2 |")
    # A segmenter that "forgot" the header: the chunk is only the data row.
    chunk = _chunk(text[row_start:], row_start, len(text))

    TableEnricher().enrich(doc, chunk)

    assert chunk.text.startswith("| A | B |\n| --- | --- |\n")
    assert chunk.text.endswith("| 1 | 2 |\n")
    # The offset invariant still holds: body (minus the injected prefix)
    # is exactly the source slice.
    assert chunk.body == text[row_start:]


def test_table_enricher_does_not_duplicate_header_already_present() -> None:
    """A chunk that already carries the header (via injected_prefix, the
    way the pipeline's table-batch packer does it) must not get a second,
    duplicate header prepended."""
    text = "| A | B |\n| --- | --- |\n| 1 | 2 |\n"
    doc = _doc(text)
    row_start = text.index("| 1 | 2 |")
    prefix = "| A | B |\n| --- | --- |\n"
    chunk = StructuredChunk(
        text=prefix + text[row_start:],
        char_start=row_start,
        char_end=len(text),
        injected_prefix=prefix,
    )

    TableEnricher().enrich(doc, chunk)

    assert chunk.text.count("| A | B |") == 1


# ---------------------------------------------------------------------------
# TypeResolver — computed once, exactly one value per document
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "source_type", "file_path", "expected"),
    [
        ("--- Page 1 ---\n\nBody.\n", "file", None, "pdf"),
        ("--- Sheet: X ---\n\nBody.\n", "file", None, "tabular"),
        ("--- Slide 1 ---\n\nBody.\n", "file", None, "slides"),
        ("Plain body, no markers.", "jira", None, "jira"),
        ("Plain body, no markers.", "confluence", None, "confluence"),
        ("Plain body, no markers.", "file", Path("doc.pdf"), "pdf"),
        ("Plain body, no markers.", "file", Path("book.xlsx"), "tabular"),
        ("Plain body, no markers.", "file", Path("deck.pptx"), "slides"),
        ("# Heading\n\nBody.", "file", None, "markdown"),
        ("Plain body, no structure at all.", "file", None, "plain_text"),
    ],
)
def test_type_resolver(text: str, source_type: str, file_path, expected: str) -> None:
    doc = _doc(text, source_type=source_type, file_path=file_path)
    assert TypeResolver(doc).resolve() == expected
