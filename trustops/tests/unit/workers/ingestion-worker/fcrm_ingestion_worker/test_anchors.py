"""Page:line anchors: synthetic Markdown edge cases and a real 3-page PDF."""

from __future__ import annotations

from pathlib import Path

import pytest
from evoq_ingestion import extract_file
from fcrm_ingestion_worker.anchors import (
    CitationOutOfRangeError,
    compute_page_line_offsets,
    read_pages,
    resolve_citation,
)

MULTI_PAGE = (
    "--- Page 1 ---\n"
    "\n"
    "alpha\n"
    "beta\n"
    "\n"
    "--- Page 2 ---\n"
    "\n"
    "gamma\n"
    "\n"
    "--- Page 3 ---\n"
    "\n"
    "delta\n"
    "epsilon\n"
)


def test_compute_page_line_offsets_multi_page() -> None:
    assert compute_page_line_offsets(MULTI_PAGE) == {1: (3, 4), 2: (8, 8), 3: (12, 13)}


def test_compute_page_line_offsets_no_markers_is_implicit_page_one() -> None:
    assert compute_page_line_offsets("# Title\n\nbody\n\n") == {1: (1, 3)}


def test_compute_page_line_offsets_empty_body_has_no_pages() -> None:
    assert compute_page_line_offsets("") == {}


def test_compute_page_line_offsets_ignores_marker_inside_code_fence() -> None:
    md = "--- Page 1 ---\n\n```\n--- Page 9 ---\n```\n"
    assert compute_page_line_offsets(md) == {1: (3, 5)}


def test_compute_page_line_offsets_empty_page_is_zero_length() -> None:
    md = "--- Page 1 ---\n\n--- Page 2 ---\n\nonly\n"
    offsets = compute_page_line_offsets(md)
    assert offsets == {1: (2, 1), 2: (5, 5)}
    with pytest.raises(CitationOutOfRangeError):
        resolve_citation(md, 1, 1, 1)


def test_resolve_citation_returns_page_relative_lines() -> None:
    assert resolve_citation(MULTI_PAGE, 3, 1, 2) == "delta\nepsilon"
    assert resolve_citation(MULTI_PAGE, 1, 2, 2) == "beta"
    assert resolve_citation(MULTI_PAGE, 2, 1, 1) == "gamma"


@pytest.mark.parametrize(
    ("page", "start", "end"), [(4, 1, 1), (2, 1, 9), (1, 0, 1), (1, 3, 2)]
)
def test_resolve_citation_out_of_range_raises(page: int, start: int, end: int) -> None:
    with pytest.raises(CitationOutOfRangeError):
        resolve_citation(MULTI_PAGE, page, start, end)


def test_read_pages_keeps_markers_for_the_slice() -> None:
    sliced = read_pages(MULTI_PAGE, 2, 3)
    assert sliced.startswith("--- Page 2 ---")
    assert "alpha" not in sliced
    assert sliced.endswith("epsilon")


async def test_compute_page_line_offsets_real_three_page_pdf(
    three_page_pdf: Path,
) -> None:
    doc = await extract_file(three_page_pdf)
    offsets = compute_page_line_offsets(doc.body_markdown)
    assert sorted(offsets) == [1, 2, 3]
    # The engine's own structure.pages (GfmRenderer, RENDERER_VERSION 3) agrees.
    engine_pages = {
        p["n"]: (p["start_line"], p["end_line"]) for p in doc.structure["pages"]
    }
    assert engine_pages == offsets
    page3 = resolve_citation(doc.body_markdown, 3, 1, offsets[3][1] - offsets[3][0] + 1)
    assert "Agent due diligence" in page3
    assert "Remesas" not in page3
    line_one = resolve_citation(doc.body_markdown, 2, 1, 1)
    assert line_one.strip()
    assert line_one in doc.body_markdown.split("--- Page 2 ---")[1]
