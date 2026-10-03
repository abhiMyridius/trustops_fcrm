"""Tests for ``extractors/registry.py`` dispatch — PDF-engine fallback,
unsupported extension without attempting extraction, per-file timeout, and
the MCP loader never being constructed for ``engine_mode="inprocess"``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from evoq_ingestion.core.config import ExtractionOptions
from evoq_ingestion.core.limits import Limits
from evoq_ingestion.extractors.base import (
    BaseExtractor,
    CorruptFile,
    EncryptedFile,
    EngineInfo,
    ExtractedDocument,
    ExtractionError,
    ExtractionFailed,
    ExtractionTimeout,
    LimitExceeded,
    UnsupportedFormat,
)
from evoq_ingestion.extractors.registry import ExtractorRegistry, run_extraction
from evoq_ingestion.model.blocks import Paragraph
from evoq_ingestion.model.document import Document


def _options(**kw) -> ExtractionOptions:
    base: dict = {}
    base.update(kw)
    return ExtractionOptions(**base)


def _doc(text: str = "body") -> Document:
    d = Document(title="t")
    d.add(Paragraph(text=text))
    return d


class _FakePdfEngine(BaseExtractor):
    supports = frozenset({".pdf"})

    def __init__(
        self, options, *, name: str, raises: Exception | None = None, sleep: float = 0.0
    ) -> None:
        super().__init__(options)
        self.info = EngineInfo(name, "1")
        self._raises = raises
        self._sleep = sleep
        self.called = False

    def _parse(self, path, facts, deadline):  # noqa: ARG002
        self.called = True
        if self._sleep:
            import time

            time.sleep(self._sleep)
        if self._raises is not None:
            raise self._raises
        return _doc(f"from {self.info.name}")


def _registry_with_pdf(
    primary: BaseExtractor, fallback: BaseExtractor | None, options: ExtractionOptions
) -> ExtractorRegistry:
    return ExtractorRegistry(
        options=options, by_ext={}, pdf_primary=primary, pdf_fallback=fallback
    )


@pytest.mark.asyncio
class TestPdfEngineFallback:
    async def test_genuine_engine_failure_falls_back_and_marks_fallback_used(
        self, tmp_path: Path
    ) -> None:
        f = tmp_path / "a.pdf"
        f.write_bytes(b"%PDF-1.4 not a real pdf but pdfplumber-only failure")
        opts = _options(pdf_engine="pdfplumber", allowed_extensions=frozenset({".pdf"}))
        primary = _FakePdfEngine(
            opts, name="pdfplumber", raises=ExtractionFailed("pdfplumber choked")
        )
        fallback = _FakePdfEngine(opts, name="pypdf")
        registry = _registry_with_pdf(primary, fallback, opts)

        results = await run_extraction([f], opts, registry=registry)
        doc = results[f]
        assert isinstance(doc, ExtractedDocument)
        assert primary.called
        assert fallback.called
        assert doc.extraction["engine"] == "pypdf"
        assert doc.extraction["fallback_used"] is True
        assert any(
            w.startswith("fallback:pdfplumber") for w in doc.extraction["warnings"]
        )

    @pytest.mark.parametrize(
        "error", [EncryptedFile("nope"), CorruptFile("nope"), LimitExceeded("too big")]
    )
    async def test_real_file_property_never_falls_back(
        self, tmp_path: Path, error: ExtractionError
    ) -> None:
        f = tmp_path / "a.pdf"
        f.write_bytes(b"%PDF-1.4")
        opts = _options(pdf_engine="pdfplumber", allowed_extensions=frozenset({".pdf"}))
        primary = _FakePdfEngine(opts, name="pdfplumber", raises=error)
        fallback = _FakePdfEngine(opts, name="pypdf")
        registry = _registry_with_pdf(primary, fallback, opts)

        results = await run_extraction([f], opts, registry=registry)
        outcome = results[f]
        assert outcome is error or isinstance(outcome, type(error))
        assert not fallback.called

    async def test_pypdf_only_mode_never_falls_back(self, tmp_path: Path) -> None:
        f = tmp_path / "a.pdf"
        f.write_bytes(b"%PDF-1.4")
        opts = _options(pdf_engine="pypdf", allowed_extensions=frozenset({".pdf"}))
        primary = _FakePdfEngine(opts, name="pypdf", raises=ExtractionFailed("boom"))
        registry = _registry_with_pdf(primary, None, opts)

        results = await run_extraction([f], opts, registry=registry)
        outcome = results[f]
        assert isinstance(outcome, ExtractionFailed)

    async def test_pypdf_only_mode_succeeds_without_a_fallback_engine(
        self, tmp_path: Path
    ) -> None:
        f = tmp_path / "a.pdf"
        f.write_bytes(b"%PDF-1.4")
        opts = _options(pdf_engine="pypdf", allowed_extensions=frozenset({".pdf"}))
        primary = _FakePdfEngine(opts, name="pypdf")
        registry = _registry_with_pdf(primary, None, opts)

        results = await run_extraction([f], opts, registry=registry)
        doc = results[f]
        assert isinstance(doc, ExtractedDocument)
        assert doc.extraction["fallback_used"] is False
        assert doc.extraction["engine"] == "pypdf"


class TestPdfEngineSelection:
    def test_from_options_selects_pdfplumber_primary_by_default(self) -> None:
        registry = ExtractorRegistry.from_options(_options())
        assert registry.pdf_primary.name == "pdfplumber"
        assert registry.pdf_fallback is not None
        assert registry.pdf_fallback.name == "pypdf"

    def test_from_options_pypdf_mode_has_no_fallback(self) -> None:
        registry = ExtractorRegistry.from_options(_options(pdf_engine="pypdf"))
        assert registry.pdf_primary.name == "pypdf"
        assert registry.pdf_fallback is None


@pytest.mark.asyncio
class TestUnsupportedExtension:
    async def test_unsupported_extension_never_reaches_an_engine(
        self, tmp_path: Path
    ) -> None:
        f = tmp_path / "a.xyz"
        f.write_text("whatever")
        opts = _options(allowed_extensions=frozenset({".txt"}))
        results = await run_extraction([f], opts)
        outcome = results[f]
        assert isinstance(outcome, UnsupportedFormat)
        assert outcome.code == "unsupported_extension"

    async def test_no_registered_engine_for_extension_is_unsupported(
        self, tmp_path: Path
    ) -> None:
        f = tmp_path / "a.doc"
        f.write_text("legacy word doc")
        opts = _options(engine_mode="inprocess", allowed_extensions=frozenset({".doc"}))
        results = await run_extraction([f], opts)
        assert isinstance(results[f], UnsupportedFormat)


@pytest.mark.asyncio
class TestPerFileTimeout:
    async def test_slow_engine_produces_extraction_timeout(
        self, tmp_path: Path
    ) -> None:
        f = tmp_path / "a.pdf"
        f.write_bytes(b"%PDF-1.4")
        opts = _options(
            pdf_engine="pypdf",
            allowed_extensions=frozenset({".pdf"}),
            limits=Limits(per_file_timeout_seconds=0.05),
        )
        primary = _FakePdfEngine(opts, name="pypdf", sleep=0.5)
        registry = _registry_with_pdf(primary, None, opts)

        results = await run_extraction(
            [f], opts, registry=registry, per_file_timeout=0.05
        )
        outcome = results[f]
        assert isinstance(outcome, ExtractionTimeout)
        assert outcome.code == "extraction_timeout"

    async def test_timeout_does_not_affect_other_files(self, tmp_path: Path) -> None:
        slow = tmp_path / "slow.pdf"
        slow.write_bytes(b"%PDF-1.4")
        opts = _options(
            pdf_engine="pypdf",
            allowed_extensions=frozenset({".pdf", ".txt"}),
            limits=Limits(per_file_timeout_seconds=0.05),
        )
        fast = tmp_path / "fast.txt"
        fast.write_text("hello")

        primary = _FakePdfEngine(opts, name="pypdf", sleep=0.5)
        registry = _registry_with_pdf(primary, None, opts)
        # Register a real text extractor for .txt so it can succeed normally.
        from evoq_ingestion.extractors.text_extractor import TextExtractor

        registry._by_ext[".txt"] = TextExtractor(opts)

        results = await asyncio.wait_for(
            run_extraction(
                [slow, fast], opts, registry=registry, per_file_timeout=0.05
            ),
            timeout=5,
        )
        assert isinstance(results[slow], ExtractionTimeout)
        assert isinstance(results[fast], ExtractedDocument)
