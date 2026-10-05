"""Timing/memory budget assertions (plan §1.6's ``test_perf.py``).

Scope & functionality
---------------------
Three budgets, each marked ``@pytest.mark.perf`` (and, for the two PDF
cases, also ``@pytest.mark.slow`` — they generate and parse a real 500-page
PDF): a ~1 MB text ``compute_profile`` pass under 0.5s, a 500-page PDF
extracted via ``PypdfExtractor`` under 15s, and the same PDF via
``PdfplumberExtractor`` under 120s. The PDF is generated on the fly with
``reportlab`` (``pytest.importorskip`` — not a runtime dependency, dev-only,
see ``requirements-dev.txt``) into ``tmp_path``, never committed; the shared
corpus has no fixture at this real-content scale (``PDF-08`` is 2001 pages
but built to be refused by the page-count *limit* before any page is read,
so it is not a page-parsing timing fixture).

``@pytest.mark.slow``/``@pytest.mark.perf`` are excluded from the default
``make test``/``make check`` run (see the ``Makefile``) — run them
explicitly with ``make test-perf`` or ``pytest -m perf``.

Streaming memory budgets for XLSX/CSV (``tracemalloc`` peak assertions) are
**not** duplicated here — see
``test_xlsx_extractor.py::TestStreamingMemory``/``test_csv_extractor.py::TestStreamingMemory``,
which already cover the row/column-cap streaming invariant at unit scale
and run unmarked (fast) as part of the default suite.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
from evoq_ingestion.core.config import ExtractionOptions
from evoq_ingestion.core.limits import Deadline
from evoq_ingestion.core.profile import compute_profile
from evoq_ingestion.extractors.gate import FileGate
from evoq_ingestion.extractors.pdf_extractor import PdfplumberExtractor, PypdfExtractor

reportlab_canvas = pytest.importorskip(
    "reportlab.pdfgen.canvas",
    reason="reportlab not installed — see requirements-dev.txt",
)

pytestmark = pytest.mark.perf


def _make_large_pdf(path: Path, n_pages: int = 500) -> None:
    """A real (non-degenerate) ``n_pages``-page PDF: each page carries several
    lines of distinct text so both engines do genuine per-page work, not a
    fast-path bail-out."""
    c = reportlab_canvas.Canvas(str(path), pagesize=(612, 792))
    for page in range(1, n_pages + 1):
        c.setFont("Helvetica-Bold", 14)
        c.drawString(72, 740, f"Section {page}: Performance Fixture Page")
        c.setFont("Helvetica", 10)
        for line in range(20):
            c.drawString(
                72,
                700 - line * 20,
                f"Page {page} body line {line}: the quick brown fox jumps over the lazy dog.",
            )
        c.showPage()
    c.save()


@pytest.fixture(scope="module")
def large_pdf(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("perf") / "large_500p.pdf"
    _make_large_pdf(path)
    return path


def test_profile_computation_under_budget() -> None:
    """A ~1 MB Markdown-ish text profile pass must complete well under 0.5s."""
    paragraph = (
        "## Section heading\n\nRequirement REQ-{n:04d} states that the system shall "
        "process events within budget. The quick brown fox jumps over the lazy dog. "
        "- item one\n- item two\n\n"
    )
    chunks = []
    size = 0
    n = 0
    target = 1_000_000
    while size < target:
        text = paragraph.format(n=n)
        chunks.append(text)
        size += len(text)
        n += 1
    text = "".join(chunks)
    assert len(text) >= target

    start = time.perf_counter()
    profile = compute_profile(text, structure={}, raw_format="md")
    elapsed = time.perf_counter() - start

    assert profile["char_count"] > 0
    assert elapsed < 0.5, (
        f"compute_profile took {elapsed:.3f}s over ~1MB of text (budget 0.5s)"
    )


async def _extract_with(engine_cls: type, path: Path) -> object:
    options = ExtractionOptions()
    gate = FileGate(options.limits)
    facts = gate.inspect(path)
    extractor = engine_cls(options)
    return await extractor.extract(path, facts, Deadline.unlimited())


@pytest.mark.slow
def test_pypdf_500_pages_under_budget(large_pdf: Path) -> None:
    start = time.perf_counter()
    doc = asyncio.run(_extract_with(PypdfExtractor, large_pdf))
    elapsed = time.perf_counter() - start

    assert doc.units.page_count == 500
    assert elapsed < 15.0, (
        f"PypdfExtractor took {elapsed:.2f}s over 500 pages (budget 15s)"
    )


@pytest.mark.slow
def test_pdfplumber_500_pages_under_budget(large_pdf: Path) -> None:
    start = time.perf_counter()
    doc = asyncio.run(_extract_with(PdfplumberExtractor, large_pdf))
    elapsed = time.perf_counter() - start

    assert doc.units.page_count == 500
    assert elapsed < 120.0, (
        f"PdfplumberExtractor took {elapsed:.2f}s over 500 pages (budget 120s)"
    )
