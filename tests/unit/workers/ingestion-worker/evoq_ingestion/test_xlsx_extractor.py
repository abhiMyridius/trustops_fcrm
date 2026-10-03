"""Tests for ``XlsxExtractor`` and its ``extractors/xlsx/`` collaborators.

Cross-checked against ``AEO/contracts/fixtures/corpus/scenarios.yaml``
(XLSX-01..05) and the corpus fixtures under
``AEO/contracts/fixtures/corpus/generated/`` (lane L9); supplemented with
``openpyxl``-generated fixtures for cases the corpus doesn't cover: a
vertical + horizontal merge combination, a skipped hidden sheet, a 300
column sheet, and a streaming-memory check.
"""

from __future__ import annotations

import io
import tracemalloc
import zipfile
from pathlib import Path
from typing import Any

import openpyxl
import pytest
from evoq_ingestion.core.config import ExtractionOptions
from evoq_ingestion.core.limits import Limits
from evoq_ingestion.extractors.base import CorruptFile
from evoq_ingestion.extractors.gate import FileGate
from evoq_ingestion.extractors.xlsx.cells import CellFormatter
from evoq_ingestion.extractors.xlsx.header import HeaderRowHeuristic
from evoq_ingestion.extractors.xlsx_extractor import XlsxExtractor
from evoq_ingestion.model.blocks import SheetBreak, Table
from evoq_ingestion.model.document import Document
from evoq_ingestion.model.renderer import GfmRenderer, Rendered

CORPUS = (
    Path(__file__).resolve().parent / "fixtures" / "contracts" / "corpus" / "generated"
)


def _require_corpus() -> None:
    if not CORPUS.exists():
        pytest.skip(f"shared corpus not checked out at {CORPUS}")


async def _extract(
    path: Path, options: ExtractionOptions | None = None
) -> tuple[Document, Rendered]:
    options = options or ExtractionOptions()
    facts = FileGate().inspect(path)
    extractor = XlsxExtractor(options)
    doc = await extractor.extract(path, facts, options.limits.deadline())
    rendered = GfmRenderer().render(doc)
    return doc, rendered


def _pipe_row_count(line: str) -> int:
    return line.count("|") - 1


# --------------------------------------------------------------- corpus scenarios
class TestCorpusScenarios:
    """XLSX-01..05 from ``scenarios.yaml`` — the ground truth for this lane."""

    async def test_xlsx01_three_sheets_no_warnings(self) -> None:
        _require_corpus()
        doc, rendered = await _extract(CORPUS / "XLSX-01_three_sheets.xlsx")
        assert doc.warnings == []
        assert [s.name for s in doc.units.sheets] == [
            "Requirements",
            "Risks",
            "Glossary",
        ]
        for marker in ("Requirements", "Risks", "Glossary"):
            assert f"--- Sheet: {marker} ---" in rendered.markdown
        assert "| ID | Title | Priority | Owner |" in rendered.markdown

    async def test_xlsx02_formula_prefers_cache_else_formula_text(self) -> None:
        _require_corpus()
        doc, rendered = await _extract(CORPUS / "XLSX-02_formulas_no_cache.xlsx")
        assert any(
            w.startswith("formula_without_cached_value:Budget:") for w in doc.warnings
        )
        assert "=B2*1.1" in rendered.markdown  # no cache -> formula text, never blank
        assert "=AVERAGE(B2:B5)" in rendered.markdown
        assert "1000" in rendered.markdown  # B6 was zip-patched with a cached <v>
        assert (
            "=SUM(B2:B5)" not in rendered.markdown
        )  # cached value wins over the formula

    async def test_xlsx03_title_banner_then_data_and_hidden_sheet_included(
        self,
    ) -> None:
        _require_corpus()
        doc, rendered = await _extract(
            CORPUS / "XLSX-03_merged_header_empty_rows_cols.xlsx"
        )
        # the merged A1:D1 banner fails the header heuristic's density check
        assert "no_header_row:Report" in doc.warnings
        assert "hidden_sheet:Scratch" in doc.warnings
        assert "Quarterly report - FY26" in rendered.markdown
        assert "North" in rendered.markdown
        assert "Overseas" in rendered.markdown
        title_line = next(
            line
            for line in rendered.markdown.splitlines()
            if "Quarterly report" in line
        )
        assert (
            title_line.count("Quarterly report - FY26") == 1
        )  # not duplicated across the merge

    async def test_xlsx04_row_cap_streams_not_materialises(self) -> None:
        _require_corpus()
        doc, rendered = await _extract(CORPUS / "XLSX-04_rows_200001.xlsx")
        assert any(w.startswith("rows_truncated:Data:") for w in doc.warnings)
        assert "--- Sheet: Data ---" in rendered.markdown
        assert "| id | value |" in rendered.markdown

    async def test_xlsx05_dates_bools_floats_are_canonical(self) -> None:
        _require_corpus()
        doc, rendered = await _extract(CORPUS / "XLSX-05_dates_percent_iso.xlsx")
        for expected in (
            "2026-03-15",
            "2026-05-01",
            "09:30",
            "TRUE",
            "FALSE",
            "0.125",
            "1200.5",
        ):
            assert expected in rendered.markdown
        assert "12.50%" not in rendered.markdown  # raw float, never the display format
        assert "1,200.50" not in rendered.markdown  # no thousands separator


# --------------------------------------------------------------- merged ranges
class TestMergedCells:
    async def test_horizontal_and_vertical_merge_stay_aligned(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "merges.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Merged"
        ws["A1"], ws["B1"] = "Region", "Totals"
        ws.merge_cells("B1:C1")
        ws["A2"], ws["B2"], ws["C2"] = "North", 10, 20
        ws["A3"], ws["B3"], ws["C3"] = "North", 11, 21
        ws.merge_cells("A2:A3")
        wb.save(path)

        doc, rendered = await _extract(path)
        assert doc.warnings == []
        table_lines = [
            line for line in rendered.markdown.splitlines() if line.startswith("|")
        ]
        # colspan/rowspan must never grow the grid: 3 logical columns everywhere
        assert all(_pipe_row_count(line) == 3 for line in table_lines)
        assert table_lines[0] == "| Region | Totals |  |"
        # the vertically merged value is preserved on both covered rows (repeat
        # policy), never dropped and never shifting later columns out of place
        assert table_lines[2].split("|")[1].strip() == "North"
        assert table_lines[3].split("|")[1].strip() == "North"
        assert table_lines[2].split("|")[2].strip() == "10"
        assert table_lines[3].split("|")[2].strip() == "11"

    async def test_merged_scan_skipped_over_size_limit(self, tmp_path: Path) -> None:
        path = tmp_path / "merges2.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "S"
        ws["A1"], ws["B1"] = "x", "y"
        ws.merge_cells("A1:B1")  # openpyxl clears B1's value once merged - by design
        wb.save(path)

        options = ExtractionOptions(limits=Limits(xlsx_merge_scan_max_bytes=1))
        doc, _rendered = await _extract(path, options)
        assert "merged_scan_skipped:S" in doc.warnings
        # the origin cell must NOT carry a span when the merge scan was
        # skipped (it never saw the <mergeCell> ref at all)
        table = next(b for b in doc.blocks if isinstance(b, Table))
        origin = table.rows[0].cells[0]
        assert origin.colspan == 1
        assert origin.rowspan == 1


# --------------------------------------------------------------- hidden sheets
class TestHiddenSheetPolicy:
    def _build(self, path: Path) -> None:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Visible"
        ws["A1"] = "x"
        hidden = wb.create_sheet("Secret")
        hidden["A1"] = "shhh"
        hidden.sheet_state = "hidden"
        wb.save(path)

    async def test_default_include_emits_content_and_warning(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "hidden_include.xlsx"
        self._build(path)
        doc, rendered = await _extract(path)
        assert "hidden_sheet:Secret" in doc.warnings
        assert "shhh" in rendered.markdown
        assert doc.units.sheets[1].hidden is True

    async def test_skip_policy_omits_content(self, tmp_path: Path) -> None:
        path = tmp_path / "hidden_skip.xlsx"
        self._build(path)
        options = ExtractionOptions(xlsx_hidden_sheets="skip")
        doc, rendered = await _extract(path, options)
        assert "hidden_sheet_skipped:Secret" in doc.warnings
        assert "shhh" not in rendered.markdown
        assert any(isinstance(b, SheetBreak) and b.name == "Secret" for b in doc.blocks)
        assert doc.units.sheets[1].rows == 0


# --------------------------------------------------------------- caps
class TestColumnAndRowCaps:
    async def test_300_columns_truncated_at_256(self, tmp_path: Path) -> None:
        path = tmp_path / "wide.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Wide"
        ws.append([f"c{i}" for i in range(300)])
        ws.append(list(range(300)))
        wb.save(path)

        doc, _rendered = await _extract(path)
        assert "cols_truncated:Wide:256" in doc.warnings
        assert doc.units.sheets[0].cols == 256

    async def test_streaming_memory_bounded_regardless_of_row_count(
        self, tmp_path: Path
    ) -> None:
        """A 250k-row sheet capped at 50 rows must not scale with the file's
        total size — proof the row loop breaks instead of materialising the
        sheet first (a naive ``list(ws.iter_rows())`` baseline on the same
        fixture peaks past 80MB; this must stay two orders of magnitude
        under that)."""
        small_path = _build_big_sheet(tmp_path / "small.xlsx", 200)
        big_path = _build_big_sheet(tmp_path / "big.xlsx", 250_000)
        options = ExtractionOptions(limits=Limits(max_rows_per_sheet=50))

        tracemalloc.start()
        try:
            await _extract(small_path, options)  # warm up import/thread machinery
            tracemalloc.reset_peak()
            doc, _rendered = await _extract(big_path, options)
        finally:
            _current, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()

        assert any(w.startswith("rows_truncated:Data:50") for w in doc.warnings)
        assert peak < 20_000_000, (
            f"peak {peak} bytes suggests the sheet was fully materialised"
        )


# --------------------------------------------------------------- degradation
class TestPerSheetDegradation:
    async def test_one_bad_sheet_becomes_unit_failure_others_survive(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "two_sheets.xlsx"
        wb = openpyxl.Workbook()
        ws1 = wb.active
        ws1.title = "Good"
        ws1["A1"] = "ok"
        ws2 = wb.create_sheet("Bad")
        ws2["A1"] = "boom"
        wb.save(path)

        real_read_sheet = XlsxExtractor._read_sheet

        def _boom(
            self: XlsxExtractor,
            doc: Document,
            ws_f: Any,
            ws_v: Any,
            name: str,
            *rest: Any,
        ) -> Any:
            if name == "Bad":
                raise RuntimeError("simulated sheet failure")
            return real_read_sheet(self, doc, ws_f, ws_v, name, *rest)

        monkeypatch.setattr(XlsxExtractor, "_read_sheet", _boom)
        doc, rendered = await _extract(path)
        assert any(f.kind == "sheet" and f.ref == "Bad" for f in doc.units.failed)
        assert "unit_failed:sheet:Bad" in doc.warnings
        assert "ok" in rendered.markdown  # the good sheet still extracted

    async def test_every_sheet_failing_is_corrupt_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "one_sheet.xlsx"
        wb = openpyxl.Workbook()
        wb.active.title = "OnlySheet"
        wb.active["A1"] = "x"
        wb.save(path)

        def _always_boom(*_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("simulated total failure")

        monkeypatch.setattr(XlsxExtractor, "_read_sheet", _always_boom)
        with pytest.raises(CorruptFile):
            await _extract(path)


# --------------------------------------------------------------- corrupt input
class TestCorruptWorkbook:
    async def test_non_zip_bytes_raise_corrupt_file(self, tmp_path: Path) -> None:
        path = tmp_path / "not_really.xlsx"
        path.write_bytes(b"this is not a zip file at all" * 10)
        options = ExtractionOptions()
        extractor = XlsxExtractor(options)
        # bypass FileGate (which would itself refuse this) to exercise the
        # extractor's own corrupt-workbook classification directly
        facts = _fake_facts(path)
        with pytest.raises(CorruptFile):
            await extractor.extract(path, facts, options.limits.deadline())


# --------------------------------------------------------------- collaborators
class TestCellFormatter:
    def test_none_is_empty_string(self) -> None:
        assert CellFormatter.format(None) == ""

    def test_bool_is_true_false(self) -> None:
        assert CellFormatter.format(True) == "TRUE"
        assert CellFormatter.format(False) == "FALSE"

    def test_midnight_datetime_is_date_only(self) -> None:
        import datetime

        assert (
            CellFormatter.format(datetime.datetime(2026, 3, 15, 0, 0, 0))
            == "2026-03-15"
        )

    def test_non_midnight_datetime_keeps_time(self) -> None:
        import datetime

        assert (
            CellFormatter.format(datetime.datetime(2026, 3, 15, 9, 30, 0))
            == "2026-03-15 09:30:00"
        )

    def test_date_and_time_isoformat(self) -> None:
        import datetime

        assert CellFormatter.format(datetime.date(2026, 3, 15)) == "2026-03-15"
        assert CellFormatter.format(datetime.time(9, 30)) == "09:30:00"

    def test_float_uses_15g_no_separators(self) -> None:
        assert CellFormatter.format(1200.5) == "1200.5"
        assert CellFormatter.format(0.125) == "0.125"

    def test_int_and_str_passthrough(self) -> None:
        assert CellFormatter.format(42) == "42"
        assert CellFormatter.format("hello") == "hello"


class TestHeaderRowHeuristic:
    def test_dense_textual_header_with_typed_data_detected(self) -> None:
        header = ["ID", "Description", "Likelihood", "Impact"]
        data = ["RISK-01", "Something happened", 3, 3]
        assert HeaderRowHeuristic.looks_like_header(header, data) is True

    def test_dense_textual_header_with_all_text_data_by_length(self) -> None:
        header = ["ID", "Title"]
        data = ["REQ-001", "A much longer sentence describing the requirement in full."]
        assert HeaderRowHeuristic.looks_like_header(header, data) is True

    def test_sparse_banner_row_is_not_a_header(self) -> None:
        banner = ["Quarterly report - FY26", None, None, None]
        assert (
            HeaderRowHeuristic.looks_like_header(banner, [None, None, None, None])
            is False
        )

    def test_no_next_row_defaults_to_header(self) -> None:
        assert HeaderRowHeuristic.looks_like_header(["A", "B"], None) is True

    def test_empty_header_row_is_not_a_header(self) -> None:
        assert HeaderRowHeuristic.looks_like_header([], None) is False


# --------------------------------------------------------------- helpers
def _fake_facts(path: Path) -> Any:
    from evoq_ingestion.extractors.gate import FileFacts, FileKind

    return FileFacts(
        path=path,
        size=path.stat().st_size,
        sha256="0" * 64,
        ext=".xlsx",
        kind=FileKind.xlsx,
    )


def _build_big_sheet(path: Path, n_rows: int) -> Path:
    """A workbook whose ``Data`` sheet has *n_rows* rows without ``r=`` row
    attributes (valid SpreadsheetML, and the same shape lane L9's own
    XLSX-04 fixture uses) — cheap to build even for hundreds of thousands
    of rows since it never touches openpyxl's per-cell write API.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Data"
    ws.append(["id", "value"])
    buf = io.BytesIO()
    wb.save(buf)
    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<dimension ref="A1:B{n_rows + 1}"/><sheetData>'
        '<row r="1"><c r="A1" t="inlineStr"><is><t>id</t></is></c>'
        '<c r="B1" t="inlineStr"><is><t>value</t></is></c></row>'
        + "<row><c><v>1</v></c><c><v>2</v></c></row>" * n_rows
        + "</sheetData></worksheet>"
    ).encode()
    source = zipfile.ZipFile(io.BytesIO(buf.getvalue()))
    out_buf = io.BytesIO()
    with zipfile.ZipFile(out_buf, "w", zipfile.ZIP_DEFLATED) as out:
        for item in source.infolist():
            data = (
                sheet_xml
                if item.filename == "xl/worksheets/sheet1.xml"
                else source.read(item.filename)
            )
            out.writestr(item, data)
    path.write_bytes(out_buf.getvalue())
    return path
