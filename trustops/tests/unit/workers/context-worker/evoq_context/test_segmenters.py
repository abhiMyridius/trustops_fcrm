"""One test class per segmenter, plus a cross-cutting marker-boundary test.

Each segmenter is exercised against a small, purpose-built document; the
final parametrized test asserts the shared invariant that holds regardless
of which of the seven segmenters ran: no produced unit ever spans a
structural marker (page/sheet/slide/comment).
"""

from __future__ import annotations

import re

import pytest
from evoq_context.chunking.budget import ChunkBudget
from evoq_context.chunking.index import DocumentIndex
from evoq_context.chunking.profiler import DocumentProfile
from evoq_context.chunking.segmenters import (
    SEGMENTERS,
    CommentThreadSegmenter,
    HeadingSegmenter,
    PageSegmenter,
    ParagraphSegmenter,
    RequirementSegmenter,
    SheetTableSegmenter,
    SlideSegmenter,
)
from evoq_context.chunking.types import SourceDocument
from evoq_context.core.limits import DEFAULT_REQUIREMENT_ID_PATTERN

PATTERN = re.compile(DEFAULT_REQUIREMENT_ID_PATTERN)
BUDGET = ChunkBudget(target=200, overlap=20, min=10, hard_max=2000)


def make_doc(
    text: str, *, title: str = "", numbered_headings: bool = False
) -> SourceDocument:
    index = DocumentIndex.build(text, PATTERN, numbered_headings=numbered_headings)
    return SourceDocument(
        text=text,
        source_type="file",
        source_id="doc-1",
        title=title,
        file_path=None,
        metadata={},
        profile=DocumentProfile(),
        index=index,
        requirement_pattern=PATTERN,
    )


class TestSegmenterRegistry:
    def test_all_seven_strategies_registered_as_classes(self) -> None:
        expected = {
            "heading_sections",
            "page_windows",
            "slide_units",
            "table_aware",
            "paragraph_windows",
            "comment_thread",
            "requirement_units",
        }
        assert set(SEGMENTERS) == expected
        for name, cls in SEGMENTERS.items():
            instance = cls()
            assert instance.name == name


class TestHeadingSegmenter:
    def test_heading_line_is_actual_source_line_not_hardcoded(self) -> None:
        text = "Preamble text.\n\n# Title\n\nBody under title.\n\n### Deep Dive\n\nDeep body.\n"
        doc = make_doc(text)
        segs = HeadingSegmenter().segment(doc, BUDGET)
        heading_lines = [s.heading_line for s in segs]
        assert "# Title" in heading_lines
        assert "### Deep Dive" in heading_lines
        assert "## Deep Dive" not in heading_lines  # the old hardcoded-"##" bug

    def test_preamble_segment_titled_with_doc_title(self) -> None:
        text = "Preamble.\n\n# Title\n\nBody.\n"
        doc = make_doc(text, title="My Doc")
        segs = HeadingSegmenter().segment(doc, BUDGET)
        assert segs[0].parent_kind == "section"
        assert segs[0].text.strip().startswith("Preamble")
        assert segs[0].heading_line == "My Doc"

    def test_one_segment_per_heading(self) -> None:
        text = "# A\n\nBody A.\n\n## B\n\nBody B.\n\n## C\n\nBody C.\n"
        doc = make_doc(text)
        segs = HeadingSegmenter().segment(doc, BUDGET)
        assert len(segs) == 3


class TestPageSegmenter:
    def test_one_segment_per_page_marker(self) -> None:
        text = "--- Page 1 ---\n\nOne.\n\n--- Page 2 ---\n\nTwo.\n\n--- Page 3 ---\n\nThree.\n"
        doc = make_doc(text)
        segs = PageSegmenter().segment(doc, BUDGET)
        assert len(segs) == 3
        assert all(s.parent_kind == "page" for s in segs)

    def test_falls_back_to_headings_when_no_page_markers(self) -> None:
        text = "# A\n\nBody.\n\n## B\n\nMore.\n"
        doc = make_doc(text)
        segs = PageSegmenter().segment(doc, BUDGET)
        assert segs == HeadingSegmenter().segment(doc, BUDGET)

    def test_preamble_before_first_page_marker(self) -> None:
        text = "Preamble.\n\n--- Page 1 ---\n\nBody.\n"
        doc = make_doc(text)
        segs = PageSegmenter().segment(doc, BUDGET)
        assert len(segs) == 2
        assert segs[0].text.strip() == "Preamble."


class TestSlideSegmenter:
    def test_one_segment_per_slide_marker(self) -> None:
        text = "--- Slide 1 ---\n\nOne.\n\n--- Slide 2 ---\n\nTwo.\n"
        doc = make_doc(text)
        segs = SlideSegmenter().segment(doc, BUDGET)
        assert len(segs) == 2
        assert all(s.parent_kind == "slide" for s in segs)

    def test_table_inside_a_slide_stays_atomic(self) -> None:
        text = "--- Slide 1 ---\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n"
        doc = make_doc(text)
        (seg,) = SlideSegmenter().segment(doc, BUDGET)
        table_units = [u for u in seg.units if u.kind == "table"]
        assert len(table_units) == 1


class TestSheetTableSegmenter:
    def test_three_tables_produce_three_or_more_table_segments(self) -> None:
        text = "\n\n".join(
            f"Intro {i}.\n\n| A | B |\n| --- | --- |\n| {i} | {i} |" for i in range(3)
        )
        doc = make_doc(text)
        segs = SheetTableSegmenter().segment(doc, BUDGET)
        table_segs = [s for s in segs if any(u.kind == "table" for u in s.units)]
        assert len(table_segs) >= 3

    def test_multi_sheet_document_tags_every_sheet_region(self) -> None:
        text = (
            "--- Sheet: Budget ---\n\nJust prose, no table here.\n\n"
            "--- Sheet: Headcount ---\n\n| Role | FTE |\n| --- | --- |\n| Eng | 5 |\n"
        )
        doc = make_doc(text)
        segs = SheetTableSegmenter().segment(doc, BUDGET)
        # The Headcount sheet's marker line + table splits into two segments
        # (the region prefix and the atomic table); both still belong to the
        # sheet, so every segment is "sheet"-scoped either way.
        assert len(segs) >= 2
        assert all(s.parent_kind == "sheet" for s in segs)
        assert any("Budget" in s.text or "prose" in s.text for s in segs)
        assert any(any(u.kind == "table" for u in s.units) for s in segs)

    def test_no_sheet_markers_prose_between_tables_is_window(self) -> None:
        text = (
            "Intro.\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n\n"
            "Middle prose.\n\n| C | D |\n| --- | --- |\n| 3 | 4 |\n"
        )
        doc = make_doc(text)
        segs = SheetTableSegmenter().segment(doc, BUDGET)
        window_segs = [s for s in segs if s.parent_kind == "window"]
        assert any("Middle prose" in s.text for s in window_segs)


class TestParagraphSegmenter:
    def test_whole_document_when_no_markers(self) -> None:
        text = "Para one.\n\nPara two.\n\nPara three.\n"
        doc = make_doc(text)
        segs = ParagraphSegmenter().segment(doc, BUDGET)
        assert len(segs) == 1
        assert segs[0].parent_kind == "window"

    def test_one_window_per_marker_bounded_region(self) -> None:
        text = "Before.\n\n--- Page 1 ---\n\nAfter.\n"
        doc = make_doc(text)
        segs = ParagraphSegmenter().segment(doc, BUDGET)
        assert len(segs) == 2


class TestCommentThreadSegmenter:
    def test_header_plus_one_segment_per_comment(self) -> None:
        text = (
            "Issue description here.\n\n"
            "### Comment 1 — Alice · today\n\nFirst comment.\n\n"
            "### Comment 2 — Bob · today\n\nSecond comment.\n"
        )
        doc = make_doc(text)
        segs = CommentThreadSegmenter().segment(doc, BUDGET)
        assert segs[0].parent_kind == "comment_window"
        assert "Issue description" in segs[0].text
        assert any("First comment" in s.text for s in segs[1:])
        assert any("Second comment" in s.text for s in segs[1:])

    def test_falls_back_to_paragraph_windows_without_comment_markers(self) -> None:
        text = "Just prose, no comments.\n"
        doc = make_doc(text)
        segs = CommentThreadSegmenter().segment(doc, BUDGET)
        assert segs == ParagraphSegmenter().segment(doc, BUDGET)


class TestRequirementSegmenter:
    def test_prose_requirements_survive_a_non_leading_table_mention(self) -> None:
        paras = [f"REQ-{i:03d}: requirement text number {i}.\n" for i in range(1, 30)]
        table = (
            "| Role | Notes |\n"
            "| --- | --- |\n"
            "| admin | mentions REQ-999 in passing, not leading |\n"
        )
        text = "\n\n".join(paras) + "\n\n" + table + "\n"
        doc = make_doc(text)

        # Sanity: the table's mention really is not line-leading.
        table_hit = next(h for h in doc.index.requirement_hits if h.id == "REQ-999")
        assert table_hit.line_leading is False

        segs = RequirementSegmenter().segment(doc, BUDGET)
        combined = "\n".join(s.text for s in segs)
        for i in range(1, 30):
            assert f"REQ-{i:03d}" in combined, f"REQ-{i:03d} was lost"
        # No hijack: more than one block survives.
        assert len(segs) > 1

    def test_line_leading_table_id_becomes_its_own_block(self) -> None:
        text = (
            "REQ-001: first requirement.\n\n"
            "| ID | Note |\n| --- | --- |\n| REQ-002 | table-based requirement |\n\n"
            "REQ-003: third requirement.\n"
        )
        doc = make_doc(text)
        segs = RequirementSegmenter().segment(doc, BUDGET)
        table_block = next(s for s in segs if any(u.kind == "table" for u in s.units))
        assert "REQ-002" in table_block.text
        assert "REQ-001" not in table_block.text
        assert "REQ-003" not in table_block.text

    def test_no_ids_falls_back_to_a_single_window(self) -> None:
        text = "Just prose with no requirement ids at all.\n"
        doc = make_doc(text)
        segs = RequirementSegmenter().segment(doc, BUDGET)
        assert len(segs) == 1
        assert segs[0].parent_kind == "window"


MARKER_DOC = (
    "# Title\n\n"
    "Intro paragraph before any marker.\n\n"
    "--- Page 1 ---\n\n"
    "Page one content REQ-001: something.\n\n"
    "--- Sheet: Budget ---\n\n"
    "| A | B |\n| --- | --- |\n| 1 | 2 |\n\n"
    "--- Slide 2 ---\n\n"
    "Slide two content.\n\n"
    "### Comment 1 — Alice · today\n\n"
    "First comment body.\n\n"
    "### Comment 2 — Bob · today\n\n"
    "Second comment body.\n"
)

ALL_SEGMENTER_CLASSES = [
    HeadingSegmenter,
    PageSegmenter,
    SlideSegmenter,
    SheetTableSegmenter,
    ParagraphSegmenter,
    CommentThreadSegmenter,
    RequirementSegmenter,
]


@pytest.mark.parametrize("segmenter_cls", ALL_SEGMENTER_CLASSES, ids=lambda c: c.name)
def test_no_unit_crosses_a_marker_boundary(segmenter_cls: type) -> None:
    doc = make_doc(MARKER_DOC)
    marker_starts = sorted(m.start for m in doc.index.markers)
    segs = segmenter_cls().segment(doc, BUDGET)
    for seg in segs:
        for unit in seg.units:
            for marker_start in marker_starts:
                assert not (unit.start < marker_start < unit.end), (
                    f"{segmenter_cls.name}: unit [{unit.start},{unit.end}) "
                    f"crosses marker at {marker_start}"
                )
