"""Tests for ``model/renderer.py`` — every ``GfmRenderer`` policy."""

from __future__ import annotations

from evoq_ingestion.model.blocks import (
    Cell,
    CodeBlock,
    Heading,
    HorizontalRule,
    ImageRef,
    ListBlock,
    ListItem,
    Note,
    NoteKind,
    PageBreak,
    Paragraph,
    Quote,
    Raw,
    Row,
    SheetBreak,
    SlideBreak,
    Table,
)
from evoq_ingestion.model.document import Document
from evoq_ingestion.model.renderer import GfmRenderer, RowspanPolicy


def _render(*blocks):
    doc = Document(blocks=list(blocks))
    return GfmRenderer().render(doc)


class TestHeadingsAndParagraphs:
    def test_heading_levels(self):
        rendered = _render(Heading(1, "Title"), Heading(3, "Sub"))
        assert "# Title" in rendered.markdown
        assert "### Sub" in rendered.markdown
        assert [h.level for h in rendered.heading_lines] == [1, 3]

    def test_paragraph_roundtrip(self):
        rendered = _render(Paragraph("Hello world."))
        assert rendered.markdown == "Hello world.\n"

    def test_heading_line_numbers(self):
        rendered = _render(Paragraph("intro"), Heading(2, "Section"))
        line = rendered.heading_lines[0]
        lines = rendered.markdown.split("\n")
        assert lines[line.line - 1] == "## Section"


class TestColspanPadding:
    def test_colspan_pads_empty_cells(self):
        table = Table(
            rows=(
                Row((Cell("Total", colspan=3, header=True),)),
                Row((Cell("1"), Cell("2"), Cell("3"))),
            ),
            header_rows=1,
        )
        lines, _ = GfmRenderer().render_table(table)
        assert lines[0] == "| Total |  |  |"
        assert lines[1] == "|---|---|---|"
        assert lines[2] == "| 1 | 2 | 3 |"


class TestRowspanPolicies:
    def _table(self):
        return Table(
            rows=(
                Row((Cell("R", rowspan=2, header=True), Cell("a", header=True))),
                Row((Cell("b"),)),
            ),
            header_rows=1,
        )

    def test_repeat_default(self):
        lines, _ = GfmRenderer(rowspan_policy=RowspanPolicy.repeat).render_table(
            self._table()
        )
        assert lines[2] == "| R | b |"

    def test_blank_policy(self):
        lines, _ = GfmRenderer(rowspan_policy=RowspanPolicy.blank).render_table(
            self._table()
        )
        assert lines[2] == "|  | b |"

    def test_marker_policy(self):
        lines, _ = GfmRenderer(rowspan_policy=RowspanPolicy.marker).render_table(
            self._table()
        )
        assert lines[2] == f"| {GfmRenderer.ROWSPAN_MARKER} | b |"


class TestNestedTableFlattening:
    def test_nested_table_flattened_with_warning(self):
        inner = Table(
            rows=(Row((Cell("N1"), Cell("N2"))), Row((Cell("N3"), Cell("N4")))),
            header_rows=0,
        )
        outer = Table(
            rows=(
                Row((Cell("Outer1", header=True), Cell("Outer2", header=True))),
                Row((Cell("A"), Cell("", nested=(inner,)))),
            ),
            header_rows=1,
        )
        lines, warnings = GfmRenderer().render_table(outer)
        assert "nested_table_flattened" in warnings
        assert "N1 / N2" in lines[2]
        assert "N3 / N4" in lines[2]

    def test_nested_flattening_in_full_render(self):
        inner = Table(rows=(Row((Cell("X"), Cell("Y"))),), header_rows=0)
        outer = Table(
            rows=(Row((Cell("", nested=(inner,)), Cell("B"))),), header_rows=0
        )
        rendered = _render(outer)
        assert "nested_table_flattened" in rendered.warnings
        assert "X / Y" in rendered.markdown


class TestCellEscaping:
    def test_pipe_escaped(self):
        table = Table(rows=(Row((Cell("a | b"),)),), header_rows=0)
        lines, _ = GfmRenderer().render_table(table)
        assert "a \\| b" in lines[2]

    def test_newline_becomes_br(self):
        table = Table(rows=(Row((Cell("line1\nline2"),)),), header_rows=0)
        lines, _ = GfmRenderer().render_table(table)
        assert "line1<br>line2" in lines[2]


class TestBlankRowsDropped:
    def test_all_blank_row_dropped(self):
        table = Table(
            rows=(
                Row((Cell("a"), Cell("b"))),
                Row((Cell(""), Cell(""))),
                Row((Cell("c"), Cell("d"))),
            ),
            header_rows=1,
        )
        lines, _ = GfmRenderer().render_table(table)
        # header + separator + 1 data row (blank row dropped)
        assert len(lines) == 3
        assert "c" in lines[2] and "d" in lines[2]


class TestSyntheticHeader:
    def test_header_rows_zero_yields_synthetic_header(self):
        table = Table(rows=(Row((Cell("1"), Cell("2"))),), header_rows=0)
        lines, _ = GfmRenderer().render_table(table)
        assert lines[0] == "|  |  |"
        assert lines[1] == "|---|---|"
        assert lines[2] == "| 1 | 2 |"


class TestMarkers:
    def test_page_marker_exact(self):
        rendered = _render(PageBreak(1))
        assert rendered.markdown == "--- Page 1 ---\n"

    def test_sheet_marker_exact(self):
        rendered = _render(SheetBreak("Data"))
        assert rendered.markdown == "--- Sheet: Data ---\n"

    def test_slide_marker_exact(self):
        rendered = _render(SlideBreak(3))
        assert rendered.markdown == "--- Slide 3 ---\n"


class TestListNesting:
    def test_bullet_list(self):
        block = ListBlock(items=(ListItem("one"), ListItem("two")))
        rendered = _render(block)
        assert rendered.markdown == "- one\n- two\n"

    def test_ordered_list_numbering(self):
        block = ListBlock(items=(ListItem("a"), ListItem("b")), ordered=True, start=1)
        rendered = _render(block)
        assert "1. a" in rendered.markdown
        assert "2. b" in rendered.markdown

    def test_nested_list_indented(self):
        child = ListBlock(items=(ListItem("child"),))
        parent = ListBlock(items=(ListItem("parent", children=(child,)),))
        rendered = _render(parent)
        lines = rendered.markdown.split("\n")
        assert lines[0] == "- parent"
        assert lines[1].strip() == "- child"
        assert lines[1].startswith("  ")

    def test_task_list_checkbox(self):
        block = ListBlock(
            items=(ListItem("done", checked=True), ListItem("todo", checked=False))
        )
        rendered = _render(block)
        assert "- [x] done" in rendered.markdown
        assert "- [ ] todo" in rendered.markdown


class TestFencesAndCode:
    def test_code_block_language(self):
        rendered = _render(CodeBlock("print(1)", lang="python"))
        assert rendered.markdown == "```python\nprint(1)\n```\n"

    def test_code_block_containing_fence_widens_outer_fence(self):
        rendered = _render(CodeBlock("```\nnested\n```", lang=""))
        lines = rendered.markdown.split("\n")
        assert lines[0] == "````"
        assert lines[-2] == "````"


class TestEscapingProseThatLooksLikeSyntax:
    def test_prose_atx_like_escaped(self):
        rendered = _render(Paragraph("# not a heading"))
        assert rendered.markdown == "\\# not a heading\n"

    def test_prose_list_marker_escaped(self):
        rendered = _render(Paragraph("- not a list item"))
        assert rendered.markdown == "\\- not a list item\n"

    def test_prose_pipe_row_escaped(self):
        rendered = _render(Paragraph("| looks | like | a | table |"))
        assert rendered.markdown.startswith("\\|")

    def test_prose_page_marker_escaped(self):
        rendered = _render(Paragraph("--- Page 1 ---"))
        assert rendered.markdown == "\\--- Page 1 ---\n"
        # Re-scanning must not invent a page marker.
        from evoq_ingestion.core.markdown_scan import scan_markdown

        assert scan_markdown(rendered.markdown).page_numbers == set()

    def test_real_heading_not_escaped(self):
        rendered = _render(Heading(1, "Real Heading"))
        assert rendered.markdown == "# Real Heading\n"


class TestQuoteAndNotes:
    def test_quote_with_label(self):
        rendered = _render(Quote(children=(Paragraph("body text"),), label="Warning"))
        assert "> **Warning**" in rendered.markdown
        assert "> body text" in rendered.markdown

    def test_note_kinds_render(self):
        rendered = _render(Note(NoteKind.footer, "Page footer text"))
        assert "**Footer:**" in rendered.markdown
        assert "Page footer text" in rendered.markdown

    def test_horizontal_rule(self):
        rendered = _render(HorizontalRule())
        assert rendered.markdown == "---\n"


class TestImageAndRaw:
    def test_image_ref(self):
        rendered = _render(ImageRef(alt="a diagram", target="img/1.png"))
        assert rendered.markdown == "![a diagram](img/1.png)\n"

    def test_raw_passthrough(self):
        rendered = _render(Raw("# Already Markdown\n\nBody."))
        assert rendered.markdown == "# Already Markdown\n\nBody.\n"
        assert rendered.heading_lines[0].text == "Already Markdown"


class TestEmptyDocument:
    def test_no_blocks_yields_empty_string(self):
        rendered = _render()
        assert rendered.markdown == ""
        assert rendered.heading_lines == ()
