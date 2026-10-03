"""Tests for ``CsvExtractor``.

Cross-checked against ``AEO/contracts/fixtures/corpus/scenarios.yaml``
(CSV-01..04) and the corpus fixtures under
``AEO/contracts/fixtures/corpus/generated/`` (lane L9); supplemented with
hand-built fixtures for cases the corpus doesn't cover: cp1252, a 4KB
single-quoted field, and a streaming-memory check.
"""

from __future__ import annotations

import tracemalloc
from pathlib import Path

import pytest
from evoq_ingestion.core.config import ExtractionOptions
from evoq_ingestion.core.limits import Limits
from evoq_ingestion.extractors.base import BinaryContent
from evoq_ingestion.extractors.csv_extractor import CsvExtractor
from evoq_ingestion.extractors.gate import FileGate
from evoq_ingestion.model.blocks import Table
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
    extractor = CsvExtractor(options)
    doc = await extractor.extract(path, facts, options.limits.deadline())
    rendered = GfmRenderer().render(doc)
    return doc, rendered


def _table_of(doc: Document) -> Table:
    return next(b for b in doc.blocks if isinstance(b, Table))


# --------------------------------------------------------------- corpus scenarios
class TestCorpusScenarios:
    """CSV-01..04 from ``scenarios.yaml`` — the ground truth for this lane."""

    async def test_csv01_bom_utf8_header_not_corrupted(self) -> None:
        _require_corpus()
        doc, rendered = await _extract(CORPUS / "CSV-01_bom_utf8.csv")
        assert doc.warnings == []
        assert "--- Sheet: CSV-01_bom_utf8 ---" in rendered.markdown
        assert "| id | title | owner | status |" in rendered.markdown
        assert "﻿" not in rendered.markdown
        first_cell = _table_of(doc).rows[0].cells[0].text
        assert first_cell == "id"  # not "﻿id"

    async def test_csv02_semicolon_sniffed_quoted_newlines_survive(self) -> None:
        _require_corpus()
        doc, rendered = await _extract(CORPUS / "CSV-02_semicolon_quoted_newlines.csv")
        assert "sniff_fallback" not in doc.warnings
        assert "Retry policy; exponential backoff" in rendered.markdown
        assert "line one" in rendered.markdown and "line two" in rendered.markdown
        # 9 logical rows: each rendered on one physical GFM line despite the
        # embedded newline (the renderer turns it into <br>, not a row split)
        pipe_lines = [
            line for line in rendered.markdown.splitlines() if line.startswith("| CFG-")
        ]
        assert len(pipe_lines) == 9

    async def test_csv03_utf16_bom_decoded_no_fallback(self) -> None:
        _require_corpus()
        doc, rendered = await _extract(CORPUS / "CSV-03_utf16.csv")
        assert "encoding_fallback:cp1252" not in doc.warnings
        assert "binary_content" not in doc.warnings
        assert "| id | name | city |" in rendered.markdown
        assert "Señora Muñoz" in rendered.markdown
        assert "山田 太郎" in rendered.markdown
        assert "Köln" in rendered.markdown

    async def test_csv04_ragged_rows_padded_into_one_table(self) -> None:
        _require_corpus()
        doc, rendered = await _extract(CORPUS / "CSV-04_ragged_rows.csv")
        assert "| a | b | c | d |" in rendered.markdown
        pipe_lines = [
            line for line in rendered.markdown.splitlines() if line.startswith("|")
        ]
        assert len(pipe_lines) == 7  # header + separator + 5 data rows
        table = _table_of(doc)
        # ragged input kept as-read: extractor never pads/truncates rows itself
        assert [len(r.cells) for r in table.rows] == [4, 3, 4, 5, 2, 4]


# --------------------------------------------------------------- encoding
class TestEncoding:
    async def test_cp1252_fallback_warns_and_decodes(self, tmp_path: Path) -> None:
        path = tmp_path / "cp1252.csv"
        path.write_bytes("name,note\r\nCafé,naïve résumé\r\n".encode("cp1252"))
        doc, rendered = await _extract(path)
        assert "encoding_fallback:cp1252" in doc.warnings
        assert "Café" in rendered.markdown
        assert "naïve résumé" in rendered.markdown

    async def test_binary_content_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "binary.csv"
        path.write_bytes(bytes(range(256)) * 8)
        with pytest.raises(BinaryContent):
            await _extract(path)

    async def test_4kb_single_quoted_field(self, tmp_path: Path) -> None:
        path = tmp_path / "one_big_field.csv"
        big_value = "x" * 4000 + ",still,inside,the,quotes"
        path.write_text(f'header\n"{big_value}"\n', encoding="utf-8", newline="")
        doc, _rendered = await _extract(path)
        table = _table_of(doc)
        assert len(table.rows) == 2
        assert table.rows[1].cells[0].text == big_value


# --------------------------------------------------------------- delimiter sniff
class TestDelimiterSniff:
    async def test_ambiguous_sample_falls_back_to_excel_dialect(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "ragged.csv"
        path.write_text("a,b,c\n1,2\n3,4,5,6\n7\n", encoding="utf-8", newline="")
        doc, rendered = await _extract(path)
        assert "sniff_fallback" in doc.warnings
        assert "| a | b | c |" in rendered.markdown


# --------------------------------------------------------------- streaming
class TestStreamingMemory:
    async def test_row_cap_streams_not_materialises(self, tmp_path: Path) -> None:
        """A 250k-row CSV capped at 50 rows must not scale with file size —
        proof the ``csv.reader`` loop breaks instead of reading the whole
        file first (a naive ``path.read_text()`` + full parse on the same
        fixture peaks around 29MB; this must stay well under that)."""
        small_path = _write_csv(tmp_path / "small.csv", 20_000)
        big_path = _write_csv(tmp_path / "big.csv", 250_000)
        options = ExtractionOptions(limits=Limits(max_csv_rows=50))

        tracemalloc.start()
        try:
            await _extract(small_path, options)  # warm up import/thread machinery
            tracemalloc.reset_peak()
            doc, _rendered = await _extract(big_path, options)
        finally:
            _current, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()

        assert "rows_truncated:50" in doc.warnings
        assert peak < 8_000_000, (
            f"peak {peak} bytes suggests the file was fully materialised"
        )


def _write_csv(path: Path, n_rows: int) -> Path:
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write("id,value\r\n")
        handle.write(("1,2\r\n") * n_rows)
    return path
