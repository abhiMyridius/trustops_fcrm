"""Tests for ``model/structure.py`` — counts derived from the model, not re-scanned text."""

from __future__ import annotations

from evoq_ingestion.model.blocks import (
    Cell,
    CodeBlock,
    Heading,
    ImageRef,
    ListBlock,
    ListItem,
    Paragraph,
    Raw,
    Row,
    Table,
)
from evoq_ingestion.model.document import Document, SheetInfo, UnitFailure, Units
from evoq_ingestion.model.renderer import GfmRenderer
from evoq_ingestion.model.structure import StructureBuilder


def _structure(doc: Document, **kwargs):
    rendered = GfmRenderer().render(doc)
    return StructureBuilder(**kwargs).from_model(doc, rendered)


class TestBasicCounts:
    def test_heading_count_and_lines(self):
        doc = Document(blocks=[Heading(1, "A"), Paragraph("body"), Heading(2, "B")])
        s = _structure(doc)
        assert s.heading_count == 2
        assert [h["level"] for h in s.headings] == [1, 2]
        assert s.headings[0]["text"] == "A"

    def test_table_count_ignores_blank_table(self):
        blank = Table(rows=(Row((Cell(""), Cell(""))),), header_rows=0)
        real = Table(rows=(Row((Cell("a"), Cell("b"))),), header_rows=0)
        doc = Document(blocks=[blank, real])
        s = _structure(doc)
        assert s.table_count == 1

    def test_list_item_count_includes_nested(self):
        nested = ListBlock(items=(ListItem("child"),))
        doc = Document(
            blocks=[ListBlock(items=(ListItem("parent", children=(nested,)),))]
        )
        s = _structure(doc)
        assert s.list_item_count == 2

    def test_code_block_count(self):
        doc = Document(blocks=[CodeBlock("x = 1", lang="python"), CodeBlock("y = 2")])
        s = _structure(doc)
        assert s.code_block_count == 2

    def test_image_count_from_blocks(self):
        doc = Document(blocks=[ImageRef("alt1", "a.png"), ImageRef("alt2", "b.png")])
        s = _structure(doc)
        assert s.image_count == 2


class TestUnitFactsWin:
    def test_page_count_from_units_not_markers(self):
        units = Units(page_count=79)
        doc = Document(blocks=[Paragraph("no page markers at all")], units=units)
        s = _structure(doc)
        assert s.page_count == 79

    def test_sheet_info_passthrough(self):
        units = Units(
            sheets=[
                SheetInfo("Data", rows=10, cols=3),
                SheetInfo("Hidden", rows=1, cols=1, hidden=True),
            ]
        )
        doc = Document(units=units)
        s = _structure(doc)
        assert s.sheet_count == 2
        assert s.sheets[1]["hidden"] is True

    def test_slide_count_from_units(self):
        doc = Document(units=Units(slide_count=6))
        s = _structure(doc)
        assert s.slide_count == 6

    def test_unit_failures_not_counted_as_structure_but_recorded_on_units(self):
        doc = Document(units=Units(page_count=3))
        doc.fail_unit("page", 2, "unit_failed", "page 2 could not be parsed")
        expected = UnitFailure("page", "2", "unit_failed", "page 2 could not be parsed")
        assert doc.units.failed == [expected]
        assert "unit_failed:page:2" in doc.warnings


class TestRawFallback:
    def test_raw_block_counts_come_from_markdown_scan(self):
        raw = Raw(
            "# Heading\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n- item one\n- item two\n"
        )
        doc = Document(blocks=[raw])
        s = _structure(doc)
        assert s.heading_count == 1
        assert s.table_count == 1
        assert s.list_item_count == 2

    def test_raw_mixed_with_real_blocks_accumulates(self):
        doc = Document(blocks=[Heading(1, "Real"), Raw("- a\n- b\n")])
        s = _structure(doc)
        assert s.heading_count == 1
        assert s.list_item_count == 2


class TestRequirementIds:
    def test_requirement_ids_extracted_from_rendered_body(self):
        doc = Document(blocks=[Paragraph("See REQ-001 and REQ-002.")])
        s = _structure(doc)
        assert s.requirement_ids == ["REQ-001", "REQ-002"]

    def test_custom_pattern(self):
        doc = Document(blocks=[Paragraph("ID:123 ID:456")])
        s = _structure(doc, requirement_id_pattern=r"ID:(\d+)")
        assert s.requirement_ids == ["123", "456"]

    def test_max_headings_cap(self):
        doc = Document(blocks=[Heading(1, f"H{i}") for i in range(5)])
        s = _structure(doc, max_headings=2)
        assert len(s.headings) == 2
        assert s.heading_count == 5


class TestToDict:
    def test_to_dict_has_every_contract_key(self):
        doc = Document(blocks=[Heading(1, "T")])
        s = _structure(doc)
        d = s.to_dict()
        expected_keys = {
            "headings",
            "heading_count",
            "page_count",
            "sheet_count",
            "slide_count",
            "sheets",
            "table_count",
            "list_item_count",
            "code_block_count",
            "image_count",
            "requirement_ids",
            "word_count",
            "char_count",
            "pages",  # TrustOps addition: page:line anchors (UPSTREAM.md)
        }
        assert set(d) == expected_keys

    def test_word_and_char_counts(self):
        doc = Document(blocks=[Paragraph("four little words")])
        s = _structure(doc)
        assert s.word_count == 3
        assert s.char_count == len("four little words") + 1  # trailing newline
