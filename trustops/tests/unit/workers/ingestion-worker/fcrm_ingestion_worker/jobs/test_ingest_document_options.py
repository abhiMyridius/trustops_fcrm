"""The PDF options the host passes to the ingest job (``PDF_ENGINE`` and
``PDF_MIN_CHARS_PER_PAGE``): what reaches the engine, and that a real PDF still
extracts with the other engine."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from evoq_ingestion import ExtractionOptions, extract_file
from fcrm_ingestion_worker.jobs import ingest_document
from fcrm_ingestion_worker.jobs.ingest_document import IngestDocumentPayload, handle


def _payload(path: Path) -> IngestDocumentPayload:
    return IngestDocumentPayload(
        case_id="FCRM-2026-0151", doc_id="DOC-1", path=str(path)
    )


@pytest.fixture
def seen_options(monkeypatch: pytest.MonkeyPatch) -> list[ExtractionOptions]:
    """Records the options each extraction is called with, then runs it."""
    seen: list[ExtractionOptions] = []

    async def spy(path: Path, *, options: ExtractionOptions, **kwargs: Any) -> Any:
        seen.append(options)
        return await extract_file(path, options=options, **kwargs)

    monkeypatch.setattr(ingest_document, "extract_file", spy)
    return seen


async def test_the_pypdf_engine_reads_the_same_pdf(
    three_page_pdf: Path, seen_options: list[ExtractionOptions]
) -> None:
    result = await handle(_payload(three_page_pdf), pdf_engine="pypdf")

    assert result.status == "COMPLETE"
    assert result.page_count == 3
    assert seen_options[0].pdf_engine == "pypdf"


async def test_the_minimum_characters_reach_the_engine(
    three_page_pdf: Path, seen_options: list[ExtractionOptions]
) -> None:
    await handle(_payload(three_page_pdf), pdf_min_chars_per_page=7)
    assert seen_options[0].pdf_min_chars_per_page == 7


async def test_without_options_the_engine_defaults_apply(
    three_page_pdf: Path, seen_options: list[ExtractionOptions]
) -> None:
    result = await handle(_payload(three_page_pdf))

    assert result.status == "COMPLETE"
    defaults = ExtractionOptions()
    assert seen_options[0].pdf_engine == defaults.pdf_engine == "pdfplumber"
    assert seen_options[0].pdf_min_chars_per_page == defaults.pdf_min_chars_per_page
    assert seen_options[0].engine_mode == "inprocess"
