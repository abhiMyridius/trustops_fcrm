"""Tests for ``DocxExtractor`` — every fidelity fix from the ingestion audit §3.2.

Corpus-driven cases (``DOCX-01``..``DOCX-06`` under
``AEO/contracts/fixtures/corpus/generated/``, cross-checked against
``scenarios.yaml``) cover headings/lists, merged cells, headers/footers/
footnotes/text boxes, tracked changes, numbered-heading inference and the
extension-mismatch failure path. Hand-built fixtures (raw XML patched via
``zipfile``/``lxml`` where python-docx's high-level API cannot create the
construct — footnotes and tracked changes) cover comments, nested tables,
hyperlinks, images, locale heading aliases, list numbering/nesting,
per-table degradation and corrupt/encrypted classification.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any

import docx
import pytest
import yaml
from docx.enum.style import WD_STYLE_TYPE
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from evoq_ingestion.core.config import ExtractionOptions
from evoq_ingestion.core.limits import Deadline
from evoq_ingestion.extractors.base import CorruptFile, EncryptedFile
from evoq_ingestion.extractors.docx_extractor import DocxExtractor
from evoq_ingestion.extractors.gate import FileFacts, FileGate, FileKind
from evoq_ingestion.model.renderer import GfmRenderer
from lxml import etree

W_NS_URI = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = "{" + W_NS_URI + "}"
PR = "{http://schemas.openxmlformats.org/package/2006/relationships}"
CT = "{http://schemas.openxmlformats.org/package/2006/content-types}"

CORPUS_DIR = (
    Path(__file__).resolve().parent / "fixtures" / "contracts" / "corpus" / "generated"
)
SCENARIOS_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "contracts"
    / "corpus"
    / "scenarios.yaml"
)


# --------------------------------------------------------------------- helpers
async def _extract(path: Path, options: ExtractionOptions | None = None):
    options = options or ExtractionOptions()
    extractor = DocxExtractor(options)
    facts = FileGate().inspect(path)
    return await extractor.extract(path, facts, Deadline.unlimited())


def _fake_facts(path: Path, kind: FileKind = FileKind.docx) -> FileFacts:
    return FileFacts(
        path=path, size=path.stat().st_size, sha256="x", ext=".docx", kind=kind
    )


def _load_scenarios() -> dict[str, dict[str, Any]]:
    if not SCENARIOS_PATH.exists():
        return {}
    data = yaml.safe_load(SCENARIOS_PATH.read_text())
    scenarios = data.get("scenarios", data)
    return {
        s["id"]: s
        for s in scenarios
        if isinstance(s, dict) and str(s.get("id", "")).startswith("DOCX")
    }


def _read_zip_parts(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path, "r") as zin:
        return {name: zin.read(name) for name in zin.namelist()}


def _write_zip_parts(path: Path, parts: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, content in parts.items():
            zout.writestr(name, content)


def _xml(data: bytes) -> etree._Element:
    return etree.fromstring(data)


def _tostring(root: etree._Element) -> bytes:
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _add_footnotes_part(path: Path, *, footnote_id: int, footnote_text: str) -> None:
    """Patch a saved ``.docx`` to add ``word/footnotes.xml`` + a matching ``footnoteReference``."""
    parts = _read_zip_parts(path)

    footnotes_xml = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:footnotes xmlns:w="{W[1:-1]}">'
        f'<w:footnote w:type="separator" w:id="-1"><w:p><w:r><w:separator/></w:r></w:p></w:footnote>'
        f'<w:footnote w:type="continuationSeparator" w:id="0">'
        f"<w:p><w:r><w:continuationSeparator/></w:r></w:p></w:footnote>"
        f'<w:footnote w:id="{footnote_id}"><w:p><w:r><w:t>{footnote_text}</w:t></w:r></w:p></w:footnote>'
        f"</w:footnotes>"
    ).encode()
    parts["word/footnotes.xml"] = footnotes_xml

    doc_root = _xml(parts["word/document.xml"])
    body = doc_root.find(f"{W}body")
    first_p = body.find(f"{W}p")
    run = etree.SubElement(first_p, f"{W}r")
    ref = etree.SubElement(run, f"{W}footnoteReference")
    ref.set(f"{W}id", str(footnote_id))
    parts["word/document.xml"] = _tostring(doc_root)

    rels_root = _xml(parts["word/_rels/document.xml.rels"])
    rel = etree.SubElement(rels_root, f"{PR}Relationship")
    rel.set("Id", "rIdFootnotesTest")
    rel.set("Type", RT.FOOTNOTES)
    rel.set("Target", "footnotes.xml")
    parts["word/_rels/document.xml.rels"] = _tostring(rels_root)

    ct_root = _xml(parts["[Content_Types].xml"])
    override = etree.SubElement(ct_root, f"{CT}Override")
    override.set("PartName", "/word/footnotes.xml")
    override.set(
        "ContentType",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml",
    )
    parts["[Content_Types].xml"] = _tostring(ct_root)

    _write_zip_parts(path, parts)


def _patch_tracked_changes(path: Path, *, ins_text: str, del_text: str) -> None:
    """Wrap the last paragraph's two runs in ``w:ins``/``w:del`` (``w:delText`` for the deletion)."""
    parts = _read_zip_parts(path)
    doc_root = _xml(parts["word/document.xml"])
    body = doc_root.find(f"{W}body")
    target_p = body.findall(f"{W}p")[-1]
    runs = target_p.findall(f"{W}r")
    ins_run, del_run = runs[0], runs[1]
    assert (ins_run.find(f"{W}t").text or "") == ins_text
    assert (del_run.find(f"{W}t").text or "") == del_text

    ins_el = etree.Element(f"{W}ins")
    ins_el.set(f"{W}id", "101")
    ins_el.set(f"{W}author", "Tester")
    ins_el.set(f"{W}date", "2024-01-01T00:00:00Z")
    target_p.replace(ins_run, ins_el)
    ins_el.append(ins_run)

    for t_el in del_run.findall(f"{W}t"):
        t_el.tag = f"{W}delText"
    del_el = etree.Element(f"{W}del")
    del_el.set(f"{W}id", "102")
    del_el.set(f"{W}author", "Tester")
    del_el.set(f"{W}date", "2024-01-01T00:00:00Z")
    target_p.replace(del_run, del_el)
    del_el.append(del_run)

    parts["word/document.xml"] = _tostring(doc_root)
    _write_zip_parts(path, parts)


# ============================================================== corpus-driven
class TestCorpusFixtures:
    """Cross-checked against ``scenarios.yaml``'s ``DOCX-01``..``DOCX-06`` rows."""

    @pytest.fixture(autouse=True)
    def _skip_if_missing(self) -> None:
        if not CORPUS_DIR.exists():
            pytest.skip("lane L9 corpus not present")

    def _scenario(self, doc_id: str) -> dict[str, Any]:
        scenarios = _load_scenarios()
        if doc_id not in scenarios:
            pytest.skip(f"{doc_id} not in scenarios.yaml")
        return scenarios[doc_id]

    @pytest.mark.asyncio
    async def test_docx01_headings_lists(self) -> None:
        scenario = self._scenario("DOCX-01")
        path = CORPUS_DIR / "DOCX-01_headings_lists.docx"
        doc = await _extract(path)
        rendered = GfmRenderer().render(doc)
        assert doc.units.failed == []
        lo, hi = scenario["expected"]["canonical"]["structure"]["heading_count"]
        assert lo <= len(rendered.heading_lines) <= hi
        for snippet in scenario["expected"]["canonical"]["body"]["contains"]:
            assert snippet in rendered.markdown
        assert "tracked_changes_present" not in doc.warnings

    @pytest.mark.asyncio
    async def test_docx02_merged_cells_model_and_render(self) -> None:
        path = CORPUS_DIR / "DOCX-02_merged_cells.docx"
        doc = await _extract(path)
        tables = doc.tables()
        assert len(tables) == 1
        table = tables[0]

        # Horizontal merge (gridSpan): one Cell, colspan=4, not four cells.
        header_row = table.rows[0]
        assert len(header_row.cells) == 1
        assert header_row.cells[0].colspan == 4
        assert header_row.cells[0].text == "Quarterly totals"

        # Vertical merge: exactly one Cell object carries the text + rowspan=2;
        # the covered row has NO cell for that column at all.
        merged_cells = [
            c for row in table.rows for c in row.cells if c.text == "Region A"
        ]
        assert len(merged_cells) == 1
        assert merged_cells[0].rowspan == 2

        # Rendered with a non-repeating policy, the value appears exactly once.
        blank_render = GfmRenderer(rowspan_policy="blank").render(doc)
        assert blank_render.markdown.count("Region A") == 1
        # Horizontal merge never duplicates regardless of rowspan policy.
        assert blank_render.markdown.count("Quarterly totals") == 1

        default_render = GfmRenderer().render(doc)
        assert "| Region | Q1 | Q2 | Q3 |" in default_render.markdown

    @pytest.mark.asyncio
    async def test_docx03_headers_footers_footnotes_textboxes(self) -> None:
        scenario = self._scenario("DOCX-03")
        path = CORPUS_DIR / "DOCX-03_header_footer_footnote_textbox.docx"
        doc = await _extract(path)
        rendered = GfmRenderer().render(doc)
        for text, max_count in scenario["expected"]["canonical"]["body"][
            "max_occurrences"
        ].items():
            assert text in rendered.markdown
            assert rendered.markdown.count(text) == max_count

    @pytest.mark.asyncio
    async def test_docx04_tracked_changes(self) -> None:
        scenario = self._scenario("DOCX-04")
        path = CORPUS_DIR / "DOCX-04_tracked_changes.docx"
        doc = await _extract(path)
        rendered = GfmRenderer().render(doc)
        assert "tracked_changes_present" in doc.warnings
        for snippet in scenario["expected"]["canonical"]["body"]["contains"]:
            assert snippet in rendered.markdown
        for snippet in scenario["expected"]["canonical"]["body"]["not_contains"]:
            assert snippet not in rendered.markdown

    @pytest.mark.asyncio
    async def test_docx04_tracked_changes_with_deletions_included(self) -> None:
        path = CORPUS_DIR / "DOCX-04_tracked_changes.docx"
        doc = await _extract(path, ExtractionOptions(docx_include_deletions=True))
        rendered = GfmRenderer().render(doc)
        assert (
            "~~DELETED-ECHO~~" in rendered.markdown
            or "DELETED-ECHO" in rendered.markdown
        )

    @pytest.mark.asyncio
    async def test_docx05_numbered_heading_inference(self) -> None:
        scenario = self._scenario("DOCX-05")
        path = CORPUS_DIR / "DOCX-05_numbered_headings_inferred.docx"
        doc = await _extract(path)
        rendered = GfmRenderer().render(doc)
        assert any(w.startswith("inferred_headings") for w in doc.warnings)
        for snippet in scenario["expected"]["canonical"]["body"]["contains"]:
            assert snippet in rendered.markdown

    def test_docx06_extension_mismatch_at_the_gate(self) -> None:
        path = CORPUS_DIR / "DOCX-06_zip_renamed.docx"
        from evoq_ingestion.extractors.base import ExtensionMismatch

        with pytest.raises(ExtensionMismatch):
            FileGate().inspect(path)


# ============================================================ hand-built cases
class TestHeadingResolution:
    @pytest.mark.asyncio
    async def test_locale_heading_alias_by_name(self, tmp_path: Path) -> None:
        d = docx.Document()
        style = d.styles.add_style("berschrift1", WD_STYLE_TYPE.PARAGRAPH)
        style.name = (
            "berschrift 1"  # German "Überschrift 1" with the diacritic stripped
        )
        d.add_paragraph("German heading", style=style)
        path = tmp_path / "locale.docx"
        d.save(path)

        doc = await _extract(path)
        headings = doc.headings()
        assert len(headings) == 1
        assert headings[0].level == 1
        assert headings[0].source == "style"
        assert not headings[0].inferred

    @pytest.mark.asyncio
    async def test_title_and_subtitle_map_to_h1_h2(self, tmp_path: Path) -> None:
        d = docx.Document()
        d.add_paragraph("Doc Title", style="Title")
        d.add_paragraph("Doc Subtitle", style="Subtitle")
        path = tmp_path / "title.docx"
        d.save(path)

        doc = await _extract(path)
        headings = doc.headings()
        assert headings[0].level == 1
        assert headings[1].level == 2

    @pytest.mark.asyncio
    async def test_outline_level_without_named_heading_style(
        self, tmp_path: Path
    ) -> None:
        d = docx.Document()
        style = d.styles.add_style("CustomSection", WD_STYLE_TYPE.PARAGRAPH)
        style.element.get_or_add_pPr()
        from docx.oxml.ns import qn as _qn

        outline = etree.SubElement(
            style.element.find(_qn("w:pPr")), _qn("w:outlineLvl")
        )
        outline.set(_qn("w:val"), "1")  # outlineLvl 1 -> heading level 2
        d.add_paragraph("Custom section title", style=style)
        path = tmp_path / "outline.docx"
        d.save(path)

        doc = await _extract(path)
        headings = doc.headings()
        assert len(headings) == 1
        assert headings[0].level == 2
        assert headings[0].source == "outline"

    @pytest.mark.asyncio
    async def test_bold_short_fallback_heuristic(self, tmp_path: Path) -> None:
        d = docx.Document()
        p = d.add_paragraph()
        run = p.add_run("Short Bold Heading")
        run.bold = True
        d.add_paragraph(
            "A longer regular paragraph that should not be treated as a heading at all."
        )
        path = tmp_path / "bold_short.docx"
        d.save(path)

        doc = await _extract(path)
        headings = doc.headings()
        assert len(headings) == 1
        assert headings[0].inferred is True
        assert headings[0].source == "bold_short"
        assert any(w == "inferred_headings:bold_short" for w in doc.warnings)

    @pytest.mark.asyncio
    async def test_bold_short_disabled_by_option(self, tmp_path: Path) -> None:
        d = docx.Document()
        p = d.add_paragraph()
        run = p.add_run("Short Bold Heading")
        run.bold = True
        path = tmp_path / "bold_short_off.docx"
        d.save(path)

        doc = await _extract(path, ExtractionOptions(docx_infer_headings=False))
        assert doc.headings() == []


class TestListNumbering:
    @pytest.mark.asyncio
    async def test_ordered_list_counts_and_continues_across_a_break(
        self, tmp_path: Path
    ) -> None:
        # Word semantics: paragraphs sharing the *same* numId are one counting
        # sequence even when interrupted by a non-list paragraph — restarting
        # requires a distinct numId (see the next test), not just a gap.
        d = docx.Document()
        d.add_paragraph("Item one", style="List Number")
        d.add_paragraph("Item two", style="List Number")
        d.add_paragraph("Item three", style="List Number")
        d.add_paragraph("Not a list paragraph")
        d.add_paragraph("Continues four", style="List Number")
        d.add_paragraph("Continues five", style="List Number")
        path = tmp_path / "ordered.docx"
        d.save(path)

        doc = await _extract(path)
        rendered = GfmRenderer().render(doc)
        assert "1. Item one" in rendered.markdown
        assert "2. Item two" in rendered.markdown
        assert "3. Item three" in rendered.markdown
        assert "4. Continues four" in rendered.markdown
        assert "5. Continues five" in rendered.markdown

    @pytest.mark.asyncio
    async def test_independent_lists_count_separately(self, tmp_path: Path) -> None:
        from docx.oxml.ns import qn as _qn

        d = docx.Document()
        first = d.add_paragraph("List A item one", style="List Number")
        d.add_paragraph("List A item two", style="List Number")
        # A second, independent *decimal* list needs its own numId bound to
        # the same abstract (decimal) definition "List Number" uses.
        p = d.add_paragraph("List B item one")
        style_num_id = (
            first.style.element.find(_qn("w:pPr"))
            .find(_qn("w:numPr"))
            .find(_qn("w:numId"))
            .get(_qn("w:val"))
        )
        num_part = d.part.part_related_by(RT.NUMBERING)
        root = num_part.element
        source_num = next(
            n
            for n in root.findall(f"{{{W_NS_URI}}}num")
            if n.get(f"{{{W_NS_URI}}}numId") == style_num_id
        )
        abstract_id = source_num.find(f"{{{W_NS_URI}}}abstractNumId").get(
            f"{{{W_NS_URI}}}val"
        )
        new_num = etree.SubElement(root, f"{{{W_NS_URI}}}num")
        new_num.set(f"{{{W_NS_URI}}}numId", "9001")
        abstract_ref = etree.SubElement(new_num, f"{{{W_NS_URI}}}abstractNumId")
        abstract_ref.set(f"{{{W_NS_URI}}}val", abstract_id)
        p_pr = p._p.get_or_add_pPr()
        num_pr = etree.SubElement(p_pr, _qn("w:numPr"))
        num_id_el = etree.SubElement(num_pr, _qn("w:numId"))
        num_id_el.set(_qn("w:val"), "9001")
        path = tmp_path / "independent_lists.docx"
        d.save(path)

        doc = await _extract(path)
        rendered = GfmRenderer().render(doc)
        assert "1. List A item one" in rendered.markdown
        assert "2. List A item two" in rendered.markdown
        assert "1. List B item one" in rendered.markdown

    @pytest.mark.asyncio
    async def test_bullet_list_is_unordered(self, tmp_path: Path) -> None:
        d = docx.Document()
        d.add_paragraph("Bullet A", style="List Bullet")
        d.add_paragraph("Bullet B", style="List Bullet")
        path = tmp_path / "bullets.docx"
        d.save(path)

        doc = await _extract(path)
        rendered = GfmRenderer().render(doc)
        assert "- Bullet A" in rendered.markdown
        assert "- Bullet B" in rendered.markdown

    @pytest.mark.asyncio
    async def test_nested_list_levels_build_a_tree(self, tmp_path: Path) -> None:
        from docx.oxml.ns import qn as _qn

        d = docx.Document()
        d.add_paragraph("Top A", style="List Bullet")
        p2 = d.add_paragraph("Nested A1", style="List Bullet")
        # "List Bullet" binds its numId only via the *style's* pPr; give this
        # paragraph its own direct w:numPr (same numId) with ilvl=1 so it
        # nests under the previous item instead of inheriting ilvl 0.
        style_num_id = (
            p2.style.element.find(_qn("w:pPr"))
            .find(_qn("w:numPr"))
            .find(_qn("w:numId"))
            .get(_qn("w:val"))
        )
        p_pr = p2._p.get_or_add_pPr()
        num_pr = etree.SubElement(p_pr, _qn("w:numPr"))
        ilvl = etree.SubElement(num_pr, _qn("w:ilvl"))
        ilvl.set(_qn("w:val"), "1")
        num_id_el = etree.SubElement(num_pr, _qn("w:numId"))
        num_id_el.set(_qn("w:val"), style_num_id)
        d.add_paragraph("Top B", style="List Bullet")
        path = tmp_path / "nested.docx"
        d.save(path)

        doc = await _extract(path)
        from evoq_ingestion.model.blocks import ListBlock

        list_blocks = [b for b in doc.blocks if isinstance(b, ListBlock)]
        assert len(list_blocks) == 1
        top = list_blocks[0]
        assert len(top.items) == 2
        assert top.items[0].text == "Top A"
        assert len(top.items[0].children) == 1
        nested = top.items[0].children[0]
        assert isinstance(nested, ListBlock)
        assert nested.items[0].text == "Nested A1"


class TestInlineContent:
    @pytest.mark.asyncio
    async def test_hyperlink_renders_as_markdown_link(self, tmp_path: Path) -> None:
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn as _qn

        d = docx.Document()
        p = d.add_paragraph()
        rid = d.part.relate_to("https://example.com/x", RT.HYPERLINK, is_external=True)
        hyperlink = OxmlElement("w:hyperlink")
        hyperlink.set(_qn("r:id"), rid)
        run = OxmlElement("w:r")
        t = OxmlElement("w:t")
        t.text = "Example Link"
        run.append(t)
        hyperlink.append(run)
        p._p.append(hyperlink)
        path = tmp_path / "hyperlink.docx"
        d.save(path)

        doc = await _extract(path)
        rendered = GfmRenderer().render(doc)
        assert "[Example Link](https://example.com/x)" in rendered.markdown

    @pytest.mark.asyncio
    async def test_image_becomes_imageref_and_attachment(self, tmp_path: Path) -> None:
        import io

        pytest.importorskip("PIL")
        from PIL import Image

        d = docx.Document()
        p = d.add_paragraph()
        run = p.add_run()
        buf = io.BytesIO()
        Image.new("RGB", (2, 2)).save(buf, format="PNG")
        buf.seek(0)
        run.add_picture(buf, width=100)
        path = tmp_path / "image.docx"
        d.save(path)

        doc = await _extract(path)
        images = doc.image_refs()
        assert len(images) == 1
        assert images[0].target.startswith("attachment:")
        assert images[0].target.endswith(".png")
        assert len(doc.attachments) == 1
        assert doc.attachments[0].url == images[0].target

    @pytest.mark.asyncio
    async def test_comment_becomes_note(self, tmp_path: Path) -> None:
        d = docx.Document()
        p = d.add_paragraph("Commented text here")
        d.add_comment(p.runs[0], text="reviewer note", author="Ada")
        path = tmp_path / "comment.docx"
        d.save(path)

        doc = await _extract(path)
        rendered = GfmRenderer().render(doc)
        assert "reviewer note" in rendered.markdown
        assert "Ada" in rendered.markdown

    @pytest.mark.asyncio
    async def test_nested_table_inside_cell(self, tmp_path: Path) -> None:
        d = docx.Document()
        outer = d.add_table(rows=1, cols=1)
        cell = outer.cell(0, 0)
        cell.text = "outer cell text"
        inner = cell.add_table(rows=1, cols=2)
        inner.cell(0, 0).text = "inner A"
        inner.cell(0, 1).text = "inner B"
        path = tmp_path / "nested_table.docx"
        d.save(path)

        doc = await _extract(path)
        tables = doc.tables()
        assert len(tables) == 1
        top_cell = tables[0].rows[0].cells[0]
        assert top_cell.text == "outer cell text"
        assert len(top_cell.nested) == 1
        assert top_cell.nested[0].rows[0].cells[0].text == "inner A"

        rendered = GfmRenderer().render(doc)
        assert "inner A" in rendered.markdown
        assert "inner B" in rendered.markdown
        assert rendered.markdown.count("inner A") == 1


class TestFootnotes:
    @pytest.mark.asyncio
    async def test_footnote_reference_and_body(self, tmp_path: Path) -> None:
        d = docx.Document()
        d.add_paragraph("Body text with a reference.")
        path = tmp_path / "footnote.docx"
        d.save(path)
        _add_footnotes_part(
            path, footnote_id=1, footnote_text="This is the footnote body."
        )

        doc = await _extract(path)
        rendered = GfmRenderer().render(doc)
        assert "[^1]" in rendered.markdown
        assert "This is the footnote body." in rendered.markdown
        assert rendered.markdown.count("This is the footnote body.") == 1

    @pytest.mark.asyncio
    async def test_separator_ids_are_skipped(self, tmp_path: Path) -> None:
        d = docx.Document()
        d.add_paragraph("Body text.")
        path = tmp_path / "footnote_sep.docx"
        d.save(path)
        _add_footnotes_part(path, footnote_id=1, footnote_text="Real footnote text.")

        doc = await _extract(path)
        from evoq_ingestion.model.blocks import Note

        footnote_notes = [
            b for b in doc.blocks if isinstance(b, Note) and b.kind == "footnote"
        ]
        assert len(footnote_notes) == 1
        assert footnote_notes[0].ref == "1"


class TestTrackedChanges:
    @pytest.mark.asyncio
    async def test_insertions_kept_deletions_dropped_by_default(
        self, tmp_path: Path
    ) -> None:
        d = docx.Document()
        p = d.add_paragraph()
        p.add_run("INSERTED-TEXT")
        p.add_run("DELETED-TEXT")
        path = tmp_path / "tracked.docx"
        d.save(path)
        _patch_tracked_changes(path, ins_text="INSERTED-TEXT", del_text="DELETED-TEXT")

        doc = await _extract(path)
        rendered = GfmRenderer().render(doc)
        assert "INSERTED-TEXT" in rendered.markdown
        assert "DELETED-TEXT" not in rendered.markdown
        assert "tracked_changes_present" in doc.warnings

    @pytest.mark.asyncio
    async def test_deletions_kept_when_option_enabled(self, tmp_path: Path) -> None:
        d = docx.Document()
        p = d.add_paragraph()
        p.add_run("INSERTED-TEXT")
        p.add_run("DELETED-TEXT")
        path = tmp_path / "tracked_included.docx"
        d.save(path)
        _patch_tracked_changes(path, ins_text="INSERTED-TEXT", del_text="DELETED-TEXT")

        doc = await _extract(path, ExtractionOptions(docx_include_deletions=True))
        rendered = GfmRenderer().render(doc)
        assert "~~DELETED-TEXT~~" in rendered.markdown
        assert "INSERTED-TEXT" in rendered.markdown


class TestPerUnitDegradation:
    @pytest.mark.asyncio
    async def test_one_bad_table_does_not_fail_the_document(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        d = docx.Document()
        d.add_paragraph("Intro")
        t1 = d.add_table(rows=1, cols=1)
        t1.cell(0, 0).text = "bad table"
        d.add_paragraph("Middle")
        t2 = d.add_table(rows=1, cols=1)
        t2.cell(0, 0).text = "good table"
        path = tmp_path / "degraded.docx"
        d.save(path)

        import evoq_ingestion.extractors.docx_extractor as mod

        original = mod.build_table
        calls = {"n": 0}

        def flaky(tbl_elem, part, *, include_deletions):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ValueError("boom")
            return original(tbl_elem, part, include_deletions=include_deletions)

        monkeypatch.setattr(mod, "build_table", flaky)
        doc = await _extract(path)

        assert len(doc.tables()) == 1
        assert doc.tables()[0].rows[0].cells[0].text == "good table"
        assert len(doc.units.failed) == 1
        assert doc.units.failed[0].kind == "table"
        assert "unit_failed:table:1" in doc.warnings


class TestCorruptAndEncrypted:
    @pytest.mark.asyncio
    async def test_corrupt_zip_raises_corrupt_file(self, tmp_path: Path) -> None:
        path = tmp_path / "corrupt.docx"
        path.write_bytes(b"not a real docx file at all, just garbage bytes")
        extractor = DocxExtractor(ExtractionOptions())
        with pytest.raises(CorruptFile):
            await extractor.extract(path, _fake_facts(path), Deadline.unlimited())

    @pytest.mark.asyncio
    async def test_ole_signature_raises_encrypted_file(self, tmp_path: Path) -> None:
        path = tmp_path / "encrypted.docx"
        path.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64)
        extractor = DocxExtractor(ExtractionOptions())
        with pytest.raises(EncryptedFile):
            await extractor.extract(path, _fake_facts(path), Deadline.unlimited())


class TestEngineIdentity:
    def test_engine_info(self) -> None:
        assert DocxExtractor.info.name == "python-docx"
        assert DocxExtractor.info.version == "2"
        assert DocxExtractor.supports == frozenset({".docx"})

    @pytest.mark.asyncio
    async def test_unsupported_extension_raises(self, tmp_path: Path) -> None:
        from evoq_ingestion.extractors.base import UnsupportedFormat

        path = tmp_path / "not_docx.txt"
        path.write_text("hello")
        extractor = DocxExtractor(ExtractionOptions())
        facts = FileFacts(
            path=path,
            size=path.stat().st_size,
            sha256="x",
            ext=".txt",
            kind=FileKind.text,
        )
        with pytest.raises(UnsupportedFormat):
            await extractor.extract(path, facts, Deadline.unlimited())
