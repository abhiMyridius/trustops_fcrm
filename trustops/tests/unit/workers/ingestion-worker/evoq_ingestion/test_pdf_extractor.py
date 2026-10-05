"""Tests for ``extractors/pdf_extractor.py`` and ``extractors/pdf/*``.

Uses the real corpus fixtures at ``AEO/contracts/fixtures/corpus/generated``
and their pinned expectations in ``AEO/contracts/fixtures/corpus/scenarios.yaml``
(PDF-01..09) when available, with a `pytest.mark.skipif` fallback so the
suite still runs (skipped) if the corpus hasn't been generated in a given
checkout. Also covers per-page failure isolation, table/prose de-duplication
(the audit's #1 empirically verified bug) and the ``PypdfExtractor`` fallback.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import yaml
from evoq_ingestion.core.codes import FailureCode
from evoq_ingestion.core.config import DEFAULT_REQUIREMENT_ID_PATTERN, ExtractionOptions
from evoq_ingestion.core.limits import Deadline
from evoq_ingestion.extractors.base import (
    CorruptFile,
    EncryptedFile,
    ExtractionError,
    LimitExceeded,
)
from evoq_ingestion.extractors.gate import FileGate
from evoq_ingestion.extractors.pdf_extractor import (
    PdfplumberExtractor,
    PypdfExtractor,
    ScannedPdfFailure,
)
from evoq_ingestion.model.renderer import GfmRenderer
from evoq_ingestion.model.structure import StructureBuilder

CORPUS_ROOT = Path(__file__).resolve().parent / "fixtures" / "contracts" / "corpus"
GENERATED = CORPUS_ROOT / "generated"
SCENARIOS = CORPUS_ROOT / "scenarios.yaml"

_CORPUS_AVAILABLE = SCENARIOS.exists() and GENERATED.exists()
pytestmark = pytest.mark.skipif(
    not _CORPUS_AVAILABLE, reason="corpus fixtures not generated"
)


def _load_pdf_scenarios() -> dict[str, dict[str, Any]]:
    data = yaml.safe_load(SCENARIOS.read_text())
    return {row["id"]: row for row in data["scenarios"] if row["id"].startswith("PDF-")}


SCENARIO_ROWS = _load_pdf_scenarios() if _CORPUS_AVAILABLE else {}


def _options() -> ExtractionOptions:
    return ExtractionOptions()


async def _extract(engine_cls: type, path: Path) -> Any:
    options = _options()
    gate = FileGate(options.limits)
    facts = gate.inspect(path)
    extractor = engine_cls(options)
    return await extractor.extract(path, facts, Deadline.unlimited())


def _run_extract(engine_cls: type, path: Path) -> Any:
    return asyncio.run(_extract(engine_cls, path))


def _expand_requirement_ids(expected: list[Any] | None) -> list[str] | None:
    if not expected:
        return expected
    if isinstance(expected[0], dict):
        spec = expected[0]
        width = spec.get("width")
        ids = []
        for i in range(spec["start"], spec["end"] + 1):
            num = str(i).zfill(width) if width else str(i)
            ids.append(f"{spec['prefix']}{num}")
        return ids
    return list(expected)


def _assert_canonical_expectations(
    scenario_id: str,
    doc: Any,
    rendered: Any,
    structure: Any,
    canonical: dict[str, Any],
) -> None:
    for code in canonical.get("codes", []):
        assert any(w == code or w.startswith(f"{code}:") for w in doc.warnings), (
            f"{scenario_id}: expected code {code!r} in {doc.warnings}"
        )
    for code in canonical.get("codes_absent", []):
        assert not any(w == code or w.startswith(f"{code}:") for w in doc.warnings), (
            f"{scenario_id}: forbidden code {code!r} present in {doc.warnings}"
        )
    struct_expect = canonical.get("structure") or {}
    struct_dict = structure.to_dict()
    for field, bounds in struct_expect.items():
        value = struct_dict[field]
        lo, hi = bounds
        assert lo <= value <= hi, (
            f"{scenario_id}: structure.{field}={value} not in [{lo},{hi}]"
        )
    req_expect = _expand_requirement_ids(canonical.get("requirement_ids"))
    if req_expect is not None:
        assert structure.requirement_ids == req_expect, (
            f"{scenario_id}: requirement_ids {structure.requirement_ids} != {req_expect}"
        )
    markers = canonical.get("markers") or {}
    if "page" in markers:
        lo, hi = markers["page"]
        count = rendered.markdown.count("--- Page ")
        assert lo <= count <= hi, (
            f"{scenario_id}: page markers {count} not in [{lo},{hi}]"
        )
    body_expect = canonical.get("body") or {}
    for needle in body_expect.get("contains", []):
        assert needle in rendered.markdown, f"{scenario_id}: body missing {needle!r}"
    for needle in body_expect.get("not_contains", []):
        assert needle not in rendered.markdown, (
            f"{scenario_id}: body must not contain {needle!r}"
        )
    for needle, cap in (body_expect.get("max_occurrences") or {}).items():
        actual = rendered.markdown.count(needle)
        assert actual <= cap, (
            f"{scenario_id}: {needle!r} occurs {actual} times (max {cap})"
        )
    for needle, floor in (body_expect.get("min_occurrences") or {}).items():
        actual = rendered.markdown.count(needle)
        assert actual >= floor, (
            f"{scenario_id}: {needle!r} occurs {actual} times (min {floor})"
        )


@pytest.mark.parametrize("scenario_id", sorted(SCENARIO_ROWS))
def test_pdf_scenarios_pdfplumber(scenario_id: str) -> None:
    """Every PDF-01..09 scenario's canonical expectations against ``PdfplumberExtractor``."""
    row = SCENARIO_ROWS[scenario_id]
    canonical = row["expected"]["canonical"]
    path = CORPUS_ROOT / row["file"]

    if canonical["status"] == "FAILED":
        with pytest.raises(ExtractionError) as excinfo:
            _run_extract(PdfplumberExtractor, path)
        for code in canonical.get("codes", []):
            assert code in excinfo.value.warning
        return

    doc = _run_extract(PdfplumberExtractor, path)
    rendered = GfmRenderer().render(doc)
    structure = StructureBuilder(
        requirement_id_pattern=DEFAULT_REQUIREMENT_ID_PATTERN
    ).from_model(doc, rendered)
    _assert_canonical_expectations(scenario_id, doc, rendered, structure, canonical)


def test_pdf01_headings_by_font_size() -> None:
    """PDF-01: font-size ratios alone (20/16/13 pt vs 10 pt body) infer H1/H1/H2."""
    doc = _run_extract(
        PdfplumberExtractor, GENERATED / "PDF-01_single_column_headings.pdf"
    )
    headings = doc.headings()
    assert [h.level for h in headings[:4]] == [1, 1, 2, 2]
    assert all(h.inferred and h.source == "layout:size" for h in headings)


def test_pdf02_multi_column_and_header_footer_removed() -> None:
    """PDF-02: two columns detected; the repeated header/footer never reach the body."""
    doc = _run_extract(
        PdfplumberExtractor, GENERATED / "PDF-02_multi_column_header_footer.pdf"
    )
    assert any(w.startswith("multi_column_detected:") for w in doc.warnings)
    assert any(w.startswith("header_footer_removed:") for w in doc.warnings)
    rendered = GfmRenderer().render(doc)
    assert "Distribution restricted" not in rendered.markdown
    assert "Confidential - Page" not in rendered.markdown


def test_pdf03_tables_not_duplicated_in_prose() -> None:
    """PDF-03: cropping the table bbox out of the text layer stops the #1 audited bug."""
    doc = _run_extract(PdfplumberExtractor, GENERATED / "PDF-03_tables_and_prose.pdf")
    rendered = GfmRenderer().render(doc)
    assert len(doc.tables()) == 3
    # Every service id is a table cell rendered exactly once (not once scrambled into
    # prose and once in the table) — the #1 empirically verified duplication bug.
    for i in range(1, 9):
        needle = f"svc-{i:02d}"
        count = len(
            re.findall(rf"(?<![\w-]){re.escape(needle)}(?![\w-])", rendered.markdown)
        )
        assert count == 1, (
            f"{needle!r} appears {count} times (table content duplicated?)"
        )
    # The GFM table header row itself, verbatim.
    assert "| Service | Region | Replicas | CPU (m) |" in rendered.markdown


def test_pdf04_numbered_requirements_and_hyphenation_helpers_available() -> None:
    """PDF-04: 79 pages, dotted-numbered headings, one VAULT-TSR id per page, header/footer gone."""
    doc = _run_extract(
        PdfplumberExtractor, GENERATED / "PDF-04_numbered_requirements_79p.pdf"
    )
    rendered = GfmRenderer().render(doc)
    structure = StructureBuilder(
        requirement_id_pattern=DEFAULT_REQUIREMENT_ID_PATTERN
    ).from_model(doc, rendered)
    assert structure.requirement_ids == [f"VAULT-TSR-{i}" for i in range(100, 179)]
    assert "Technical System Requirements - Rev 4" not in rendered.markdown
    sized = [h for h in doc.headings() if h.source == "layout:size"]
    assert sized  # chapter titles are large enough to be caught by size ratio
    assert doc.units.page_count == 79


def test_pdf05_scanned_pages_fail_with_named_code() -> None:
    with pytest.raises(ScannedPdfFailure) as excinfo:
        _run_extract(PdfplumberExtractor, GENERATED / "PDF-05_scanned_image_only.pdf")
    assert excinfo.value.code == FailureCode.scanned_pdf_no_ocr


def test_pdf06_encrypted_fails_gracefully_both_engines() -> None:
    path = GENERATED / "PDF-06_encrypted.pdf"
    with pytest.raises(EncryptedFile):
        _run_extract(PdfplumberExtractor, path)
    with pytest.raises(EncryptedFile):
        _run_extract(PypdfExtractor, path)


def test_pdf07_corrupt_fails_gracefully_both_engines() -> None:
    path = GENERATED / "PDF-07_corrupt_truncated.pdf"
    with pytest.raises(CorruptFile):
        _run_extract(PdfplumberExtractor, path)
    with pytest.raises(CorruptFile):
        _run_extract(PypdfExtractor, path)


def test_pdf08_page_limit_enforced_before_iterating_pages_both_engines() -> None:
    path = GENERATED / "PDF-08_page_limit_2001.pdf"
    for engine_cls in (PdfplumberExtractor, PypdfExtractor):
        with pytest.raises(LimitExceeded) as excinfo:
            _run_extract(engine_cls, path)
        assert excinfo.value.failure_code == FailureCode.page_limit_exceeded


def test_pdf09_cjk_no_whitespace_extracts_without_binary_or_scanned_flags() -> None:
    doc = _run_extract(PdfplumberExtractor, GENERATED / "PDF-09_cjk_no_whitespace.pdf")
    rendered = GfmRenderer().render(doc)
    assert doc.units.page_count == 3
    assert not any(w.startswith("scanned_pdf_no_ocr") for w in doc.warnings)
    assert not any(w.startswith("binary_content") for w in doc.warnings)
    assert "账" in rendered.markdown


# --------------------------------------------------------------- pypdf fallback


def test_pypdf_numbering_only_heading_inference() -> None:
    """pypdf has no font sizes: PDF-01's non-numbered headings are invisible to it,
    but PDF-04's dotted numbering still produces headings."""
    doc01 = _run_extract(
        PypdfExtractor, GENERATED / "PDF-01_single_column_headings.pdf"
    )
    assert doc01.headings() == []

    doc04 = _run_extract(
        PypdfExtractor, GENERATED / "PDF-04_numbered_requirements_79p.pdf"
    )
    headings = doc04.headings()
    assert headings
    assert all(h.inferred and h.source == "layout:numbered" for h in headings)
    assert headings[0].text == "1. Introduction"
    assert headings[0].level == 1


def test_pypdf_scanned_encrypted_corrupt_and_page_limit() -> None:
    with pytest.raises(ScannedPdfFailure):
        _run_extract(PypdfExtractor, GENERATED / "PDF-05_scanned_image_only.pdf")
    with pytest.raises(EncryptedFile):
        _run_extract(PypdfExtractor, GENERATED / "PDF-06_encrypted.pdf")
    with pytest.raises(CorruptFile):
        _run_extract(PypdfExtractor, GENERATED / "PDF-07_corrupt_truncated.pdf")


# --------------------------------------------------------------- resilience


def test_per_page_failure_is_isolated_pdfplumber() -> None:
    """One page raising must not fail the whole 79-page document."""
    import pdfplumber

    path = GENERATED / "PDF-04_numbered_requirements_79p.pdf"
    options = _options()
    gate = FileGate(options.limits)
    facts = gate.inspect(path)
    extractor = PdfplumberExtractor(options)

    original_open = pdfplumber.open

    def _raise(*_args: Any, **_kwargs: Any) -> str:
        raise RuntimeError("boom: page 6 extract_text exploded")

    def _patched_open(*args: Any, **kwargs: Any) -> Any:
        pdf = original_open(*args, **kwargs)
        pdf.pages[5].extract_text = _raise  # type: ignore[method-assign]
        return pdf

    with patch("pdfplumber.open", _patched_open):
        doc = asyncio.run(extractor.extract(path, facts, Deadline.unlimited()))

    page_failures = [f for f in doc.units.failed if f.kind == "page"]
    assert len(page_failures) == 1
    assert page_failures[0].ref == "6"
    assert any(w == "unit_failed:page:6" for w in doc.warnings)
    # The other 78 pages still produced content.
    assert len(doc.headings()) >= 70


def test_all_pages_failing_raises_corrupt_file() -> None:
    """If every page raises, the whole file is refused as corrupt, not silently empty."""
    import pdfplumber

    path = GENERATED / "PDF-01_single_column_headings.pdf"
    options = _options()
    gate = FileGate(options.limits)
    facts = gate.inspect(path)
    extractor = PdfplumberExtractor(options)

    original_open = pdfplumber.open

    def _raise(*_args: Any, **_kwargs: Any) -> str:
        raise RuntimeError("boom")

    def _patched_open(*args: Any, **kwargs: Any) -> Any:
        pdf = original_open(*args, **kwargs)
        for page in pdf.pages:
            page.extract_text = _raise  # type: ignore[method-assign]
        return pdf

    with patch("pdfplumber.open", _patched_open), pytest.raises(CorruptFile):
        asyncio.run(extractor.extract(path, facts, Deadline.unlimited()))


def test_flush_cache_called_per_page() -> None:
    """Memory: ``page.flush_cache()`` runs once every page has been processed."""
    import pdfplumber

    path = GENERATED / "PDF-01_single_column_headings.pdf"
    options = _options()
    gate = FileGate(options.limits)
    facts = gate.inspect(path)
    extractor = PdfplumberExtractor(options)

    with pdfplumber.open(str(path), password="") as pdf:
        n_pages = len(pdf.pages)

    flushed: list[int] = []
    original_open = pdfplumber.open

    def _patched_open(*args: Any, **kwargs: Any) -> Any:
        pdf = original_open(*args, **kwargs)
        for page in pdf.pages:
            original_flush = page.flush_cache

            def _wrapped(_orig: Any = original_flush) -> None:
                flushed.append(1)
                _orig()

            page.flush_cache = _wrapped  # type: ignore[method-assign]
        return pdf

    with patch("pdfplumber.open", _patched_open):
        asyncio.run(extractor.extract(path, facts, Deadline.unlimited()))

    assert len(flushed) == n_pages


# --------------------------------------------------------------- de-dup property


@pytest.mark.parametrize(
    "filename",
    ["PDF-03_tables_and_prose.pdf"],
)
def test_no_table_substring_leaks_into_prose(filename: str) -> None:
    """Property: no table cell's exact text also appears verbatim outside any table."""
    doc = _run_extract(PdfplumberExtractor, GENERATED / filename)
    rendered = GfmRenderer().render(doc)
    lines = rendered.markdown.split("\n")
    table_lines = {line for line in lines if line.startswith("|")}
    prose_lines = [line for line in lines if line and not line.startswith("|")]
    table_cell_tokens: set[str] = set()
    for line in table_lines:
        for cell in line.strip("|").split("|"):
            token = cell.strip()
            if token and not re.fullmatch(r"-{3,}", token):
                table_cell_tokens.add(token)
    prose_text = "\n".join(prose_lines)
    for token in table_cell_tokens:
        if len(token) < 4:
            continue  # short tokens (e.g. numbers) legitimately recur
        # Only data-shaped cells (contain a digit, a dot or a hyphen: "svc-01",
        # "q.notify.3", "2026-05") are unambiguous identifiers — a plain English
        # header word ("Queue", "Region") can coincidentally recur in the
        # corpus generator's random prose vocabulary without that being the
        # duplication bug this test guards against.
        if (
            not any(ch.isdigit() for ch in token)
            and "-" not in token
            and "." not in token
        ):
            continue
        # Word-boundary match: a data cell like "2026-05" must not appear as a
        # standalone token in prose, but may be a substring of a longer token.
        pattern = rf"(?<![\w-]){re.escape(token)}(?![\w-])"
        assert not re.search(pattern, prose_text), (
            f"table cell {token!r} leaked into prose"
        )
