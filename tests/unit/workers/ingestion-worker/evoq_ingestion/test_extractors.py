"""Tests for extractors/registry.py — markers, structure, UnsupportedFormat,
and the in-process engine chain (the MCP engine was pruned on the TrustOps
clone — UPSTREAM.md), against the current ``BaseExtractor``
protocol (``__init__(options)``, ``extract(path, facts, deadline)``).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from evoq_ingestion.core.config import ExtractionOptions
from evoq_ingestion.extractors.base import (
    BaseExtractor,
    EngineInfo,
    ExtractedDocument,
    ExtractionError,
    UnsupportedFormat,
)
from evoq_ingestion.extractors.csv_extractor import CsvExtractor
from evoq_ingestion.extractors.registry import ExtractorRegistry, run_extraction
from evoq_ingestion.extractors.text_extractor import TextExtractor
from evoq_ingestion.model.blocks import Paragraph
from evoq_ingestion.model.document import Document

pytest_plugins = ()


def _options(**kw) -> ExtractionOptions:
    base = {
        "engine_mode": "auto",
        "allowed_extensions": frozenset({".txt", ".md", ".csv", ".pdf", ".docx"}),
    }
    base.update(kw)
    return ExtractionOptions(**base)


class _FakeExtractor(BaseExtractor):
    """A minimal ``BaseExtractor`` whose ``_parse`` is overridden per test."""

    info = EngineInfo("fake", "1")
    supports = frozenset({".fake"})

    def _parse(self, path, facts, deadline):  # noqa: ARG002
        doc = Document(title=path.stem)
        doc.add(Paragraph(text="fake body"))
        return doc


class TestTextExtractorViaRegistry:
    @pytest.mark.asyncio
    async def test_txt_roundtrip(self, tmp_path: Path):
        f = tmp_path / "a.txt"
        f.write_text("Hello REQ-001 world.")
        results = await run_extraction([f], _options())
        doc = results[f]
        assert isinstance(doc, ExtractedDocument)
        assert doc.body_markdown == "Hello REQ-001 world.\n"
        assert doc.structure["requirement_ids"] == ["REQ-001"]
        assert doc.extraction["engine"] == "text"

    @pytest.mark.asyncio
    async def test_unsupported_extension_not_on_allow_list(self, tmp_path: Path):
        f = tmp_path / "a.bin"
        f.write_bytes(b"\x00\x01")
        results = await run_extraction(
            [f], _options(allowed_extensions=frozenset({".txt"}))
        )
        assert isinstance(results[f], UnsupportedFormat)


class TestCsvExtractorViaRegistry:
    @pytest.mark.asyncio
    async def test_sheet_marker_and_table(self, tmp_path: Path):
        f = tmp_path / "data.csv"
        f.write_text("a,b\n1,2\n3,4\n")
        results = await run_extraction([f], _options())
        doc = results[f]
        assert isinstance(doc, ExtractedDocument)
        assert "--- Sheet: data ---" in doc.body_markdown
        assert "| a | b |" in doc.body_markdown
        assert doc.structure["sheet_count"] == 1

    @pytest.mark.asyncio
    async def test_rows_truncated_warning(self, tmp_path: Path):
        f = tmp_path / "big.csv"
        rows = ["h1,h2"] + [f"{i},{i}" for i in range(10)]
        f.write_text("\n".join(rows))
        opts = _options()
        opts.csv_max_rows = 5
        results = await run_extraction([f], opts)
        doc = results[f]
        assert isinstance(doc, ExtractedDocument)
        assert any(w.startswith("rows_truncated:") for w in doc.extraction["warnings"])


class TestExtractorConstruction:
    def test_extractor_constructed_with_options(self):
        opts = _options()
        extractor = CsvExtractor(opts)
        assert extractor.options is opts
        assert extractor.name == "csv"
        assert extractor.version() == "2"

    @pytest.mark.asyncio
    async def test_accepts_and_supports(self, tmp_path: Path):
        opts = _options()
        extractor = TextExtractor(opts)
        assert extractor.accepts(Path("a.txt"))
        assert extractor.accepts(Path("a.md"))
        assert not extractor.accepts(Path("a.pdf"))


class TestEngineChainInprocessOnly:
    """Replaces upstream's ``TestEngineChainAuto``: with the MCP loader pruned,
    ``auto`` and ``inprocess`` both use the in-process chain only."""

    @pytest.mark.asyncio
    async def test_inprocess_failure_stays_a_failure(self, tmp_path: Path):
        f = tmp_path / "a.fake"
        f.write_text("hello")

        class BrokenFake(_FakeExtractor):
            def _parse(self, path, facts, deadline):  # noqa: ARG002
                raise RuntimeError("simulated failure")

        opts = _options(engine_mode="auto", allowed_extensions=frozenset({".fake"}))
        registry = ExtractorRegistry(
            options=opts,
            by_ext={".fake": BrokenFake(opts)},
            pdf_primary=BrokenFake(opts),
            pdf_fallback=None,
        )
        results = await run_extraction([f], opts, registry=registry)
        assert isinstance(results[f], ExtractionError)

    @pytest.mark.asyncio
    async def test_auto_extension_without_engine_is_unsupported(self, tmp_path: Path):
        """``.doc`` has no in-process engine; upstream's auto mode rescued it
        via MCP — now it is an honest UNSUPPORTED row."""
        f = tmp_path / "a.doc"
        f.write_bytes(b"legacy word doc bytes")
        opts = _options(engine_mode="auto", allowed_extensions=frozenset({".doc"}))
        results = await run_extraction([f], opts)
        assert isinstance(results[f], UnsupportedFormat)

    @pytest.mark.asyncio
    async def test_registry_has_no_mcp_attribute(self):
        registry = ExtractorRegistry.from_options(_options(engine_mode="auto"))
        assert not hasattr(registry, "mcp")


class TestUnsupportedExtension:
    @pytest.mark.asyncio
    async def test_not_on_allow_list_is_unsupported(self, tmp_path: Path):
        f = tmp_path / "a.png"
        f.write_bytes(b"\x89PNG")
        results = await run_extraction(
            [f], _options(allowed_extensions=frozenset({".txt"}))
        )
        assert isinstance(results[f], UnsupportedFormat)

    @pytest.mark.asyncio
    async def test_extraction_error_carries_a_failure_code(self, tmp_path: Path):
        f = tmp_path / "a.png"
        f.write_bytes(b"\x89PNG")
        results = await run_extraction(
            [f], _options(allowed_extensions=frozenset({".txt"}))
        )
        outcome = results[f]
        assert isinstance(outcome, ExtractionError)
        assert outcome.code == "unsupported_extension"
