"""Tests for ``chunking.index.DocumentIndex.build``.

Covers: ATX + numbered headings (gated at >= 3 candidates, nested under the
nearest preceding ATX ancestor), GFM tables with/without a caption (and
caption exclusion when a marker/table row sits directly above), the
line-leading vs. mid-prose distinction for requirement-id hits, and every
structural marker kind (page/sheet/slide/comment).
"""

from __future__ import annotations

import re

from evoq_context.chunking.index import DocumentIndex
from evoq_context.chunking.markers import is_numbered_heading
from evoq_context.core.limits import DEFAULT_REQUIREMENT_ID_PATTERN

PATTERN = re.compile(DEFAULT_REQUIREMENT_ID_PATTERN)


def _numbered_candidate_count(text: str) -> int:
    """Test-local mirror of the profiler's ``numbered_section_count`` gate."""
    return sum(1 for line in text.splitlines() if is_numbered_heading(line))


class TestHeadings:
    def test_atx_headings_have_correct_level_and_text(self) -> None:
        text = "# Title\n\nBody.\n\n## Sub\n\nMore body.\n"
        idx = DocumentIndex.build(text, PATTERN, numbered_headings=False)
        assert [(h.level, h.text, h.source) for h in idx.headings] == [
            (1, "Title", "atx"),
            (2, "Sub", "atx"),
        ]

    def test_numbered_headings_require_the_three_candidate_gate(self) -> None:
        text = "# Title\n\nText.\n\n8.1 Foo\n\nText.\n\n8.2 Bar\n\nText.\n"
        assert _numbered_candidate_count(text) == 2
        idx = DocumentIndex.build(text, PATTERN, numbered_headings=True)
        assert not any(h.source == "numbered" for h in idx.headings)

    def test_numbered_headings_detected_and_nested_once_gate_passes(self) -> None:
        text = (
            "# 8. Transactions\n\n"
            "Intro.\n\n"
            "8.1 Overview\n\n"
            "Text.\n\n"
            "8.2 Details\n\n"
            "Text.\n\n"
            "8.3 Posting\n\n"
            "Text.\n"
        )
        assert _numbered_candidate_count(text) >= 3
        idx = DocumentIndex.build(text, PATTERN, numbered_headings=True)
        numbered = [h for h in idx.headings if h.source == "numbered"]
        assert len(numbered) == 3
        posting = next(h for h in numbered if h.text.startswith("8.3"))
        # Matches the documented example: nests under the ATX H1 (level=1)
        # at its own dotted depth (2), since 2 already sorts deeper than 1.
        assert posting.level == 2

    def test_numbered_heading_forced_deeper_than_a_deep_atx_ancestor(self) -> None:
        text = (
            "### 8 Transactions\n\n"
            "Text.\n\n"
            "9 Appendix\n\n"
            "Text.\n\n"
            "10 Glossary\n\n"
            "Text.\n\n"
            "11 Index\n\n"
            "Text.\n"
        )
        idx = DocumentIndex.build(text, PATTERN, numbered_headings=True)
        numbered = [h for h in idx.headings if h.source == "numbered"]
        assert len(numbered) == 3
        # ATX ancestor is level 3; a depth-1 numbered heading must still
        # sort deeper than it (nested under it), not equal or shallower.
        assert all(h.level > 3 for h in numbered)

    def test_no_numbered_headings_without_the_flag(self) -> None:
        text = "8.1 Foo\n\nText.\n\n8.2 Bar\n\nText.\n\n8.3 Baz\n\nText.\n"
        idx = DocumentIndex.build(text, PATTERN, numbered_headings=False)
        assert idx.headings == ()


class TestTables:
    def test_table_with_explicit_caption(self) -> None:
        text = (
            "Intro paragraph.\n\n"
            "Table 1: Field definitions.\n"
            "| Code | Desc |\n"
            "| --- | --- |\n"
            "| A | Alpha |\n"
            "| B | Beta |\n\n"
            "Closing text.\n"
        )
        idx = DocumentIndex.build(text, PATTERN, numbered_headings=False)
        assert len(idx.tables) == 1
        table = idx.tables[0]
        assert table.header_cells == ("Code", "Desc")
        assert len(table.data_rows) == 2
        assert table.caption is not None
        assert table.caption.text.startswith("Table 1:")

    def test_table_caption_falls_back_to_short_preceding_line(self) -> None:
        text = "Field codes below.\n| Code | Desc |\n| --- | --- |\n| A | Alpha |\n"
        idx = DocumentIndex.build(text, PATTERN, numbered_headings=False)
        assert idx.tables[0].caption is not None
        assert idx.tables[0].caption.text == "Field codes below."

    def test_no_caption_when_preceding_line_is_a_marker(self) -> None:
        text = "--- Page 1 ---\n| Code | Desc |\n| --- | --- |\n| A | Alpha |\n"
        idx = DocumentIndex.build(text, PATTERN, numbered_headings=False)
        assert idx.tables[0].caption is None

    def test_no_caption_when_preceding_line_is_another_tables_row(self) -> None:
        text = (
            "| X | Y |\n| --- | --- |\n| 1 | 2 |\n"
            "| Code | Desc |\n| --- | --- |\n| A | Alpha |\n"
        )
        idx = DocumentIndex.build(text, PATTERN, numbered_headings=False)
        # The two tables have no blank line between them and merge into one
        # contiguous run under the current line-grouping rule; regardless,
        # neither table's caption is ever another table's row.
        for table in idx.tables:
            if table.caption is not None:
                assert not table.caption.text.strip().startswith("|")

    def test_multiple_tables_all_indexed(self) -> None:
        text = (
            "Intro.\n\n"
            "| A | B |\n| --- | --- |\n| 1 | 2 |\n\n"
            "Middle prose.\n\n"
            "| C | D |\n| --- | --- |\n| 3 | 4 |\n| 5 | 6 |\n\n"
            "| E | F |\n| --- | --- |\n| 7 | 8 |\n"
        )
        idx = DocumentIndex.build(text, PATTERN, numbered_headings=False)
        assert len(idx.tables) == 3
        assert [t.header_cells for t in idx.tables] == [
            ("A", "B"),
            ("C", "D"),
            ("E", "F"),
        ]


class TestRequirementHits:
    def test_line_leading_vs_mid_prose(self) -> None:
        text = (
            "REQ-001: The system shall do X.\n\n"
            "This references REQ-002 in passing, not leading.\n\n"
            "- REQ-003: bullet form.\n\n"
            "| REQ-004 | desc |\n"
            "| --- | --- |\n"
            "| val | val2 |\n"
        )
        idx = DocumentIndex.build(text, PATTERN, numbered_headings=False)
        leading = {hit.id: hit.line_leading for hit in idx.requirement_hits}
        assert leading["REQ-001"] is True
        assert leading["REQ-002"] is False
        assert leading["REQ-003"] is True
        assert leading["REQ-004"] is True

    def test_mid_cell_mention_is_not_line_leading(self) -> None:
        text = (
            "| Role | Notes |\n| --- | --- |\n| admin | mentions REQ-999 in passing |\n"
        )
        idx = DocumentIndex.build(text, PATTERN, numbered_headings=False)
        (hit,) = idx.requirement_hits
        assert hit.id == "REQ-999"
        assert hit.line_leading is False


class TestMarkers:
    def test_every_marker_kind_is_found_in_order(self) -> None:
        text = (
            "--- Page 1 ---\n\nBody.\n\n"
            "--- Sheet: Budget ---\n\nBody.\n\n"
            "--- Slide 2 ---\n\nBody.\n\n"
            "### Comment 1 — Alice · today\n\nBody.\n"
        )
        idx = DocumentIndex.build(text, PATTERN, numbered_headings=False)
        kinds_values = [(m.kind, m.value) for m in idx.markers]
        assert kinds_values == [
            ("page", "1"),
            ("sheet", "Budget"),
            ("slide", "2"),
            ("comment", "1"),
        ]

    def test_markers_are_sorted_by_position(self) -> None:
        text = "--- Slide 1 ---\nA\n--- Page 1 ---\nB\n"
        idx = DocumentIndex.build(text, PATTERN, numbered_headings=False)
        starts = [m.start for m in idx.markers]
        assert starts == sorted(starts)
