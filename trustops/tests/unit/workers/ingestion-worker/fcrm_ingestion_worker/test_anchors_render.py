""":func:`fcrm_ingestion_worker.anchors.render_anchored`: the page:line copy
agents cite. The lock tests hold its output to the grammar ``fcrm_tools
.markdown`` writes and reads (H3 does not depend on that package), and to the
numbering ``resolve_citation`` uses, so a citation made against one resolves
in the other.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fcrm_ingestion_worker.anchors import (
    compute_page_line_offsets,
    render_anchored,
    resolve_citation,
)
from fcrm_ingestion_worker.jobs.ingest_document import IngestDocumentPayload, handle
from fcrm_tools import markdown as tools_markdown
from fcrm_tools.markdown import parse_pages, render_pages

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


def test_it_writes_a_header_then_a_block_per_page_with_numbered_lines() -> None:
    rendered = render_anchored(MULTI_PAGE, doc_id="doc_1")

    assert rendered == (
        "# doc_1 (3 pages)\n"
        "## Page 1\n"
        "p.1 l.1: alpha\n"
        "p.1 l.2: beta\n"
        "## Page 2\n"
        "p.2 l.1: gamma\n"
        "## Page 3\n"
        "p.3 l.1: delta\n"
        "p.3 l.2: epsilon\n"
    )


def test_every_anchored_line_matches_the_tools_grammar() -> None:
    rendered = render_anchored(MULTI_PAGE, doc_id="doc_1")
    anchored = [ln for ln in rendered.splitlines() if ln.startswith("p.")]
    assert anchored
    assert all(tools_markdown._ANCHOR.match(line) for line in anchored)


def test_the_tools_reader_gets_back_exactly_the_pages_the_offsets_name() -> None:
    parsed = parse_pages(render_anchored(MULTI_PAGE, doc_id="doc_1"))
    offsets = compute_page_line_offsets(MULTI_PAGE)
    body = MULTI_PAGE.split("\n")
    assert sorted(parsed) == sorted(offsets)
    for page, (start, end) in offsets.items():
        assert parsed[page] == body[start - 1 : end]


def test_it_is_what_the_tools_writer_writes_for_the_same_pages() -> None:
    offsets = compute_page_line_offsets(MULTI_PAGE)
    body = MULTI_PAGE.split("\n")
    pages = {n: body[s - 1 : e] for n, (s, e) in offsets.items()}
    assert render_anchored(MULTI_PAGE, doc_id="doc_1") == render_pages(
        "doc_1", len(pages), pages
    )


@pytest.mark.parametrize(("page", "line"), [(1, 2), (2, 1), (3, 2)])
def test_a_line_number_here_resolves_to_the_same_text_as_a_citation(
    page: int, line: int
) -> None:
    anchored_text = parse_pages(render_anchored(MULTI_PAGE, doc_id="d"))[page][line - 1]
    assert anchored_text == resolve_citation(MULTI_PAGE, page, line, line)


def test_a_body_without_page_markers_is_one_implicit_page() -> None:
    rendered = render_anchored("# Title\n\nbody\n", doc_id="note")
    assert rendered.splitlines()[:3] == [
        "# note (1 pages)",
        "## Page 1",
        "p.1 l.1: # Title",
    ]


def test_an_empty_body_has_a_header_and_no_pages() -> None:
    assert render_anchored("", doc_id="empty") == "# empty (0 pages)\n"


def test_an_empty_page_keeps_its_heading_and_no_lines() -> None:
    md = "--- Page 1 ---\n\n--- Page 2 ---\n\ntext\n"
    rendered = render_anchored(md, doc_id="d")
    assert "## Page 1\n## Page 2\np.2 l.1: text\n" in rendered


def test_a_blank_line_inside_a_page_keeps_its_number_and_the_anchor() -> None:
    md = "--- Page 1 ---\n\nfirst\n\nthird\n"
    parsed = parse_pages(render_anchored(md, doc_id="d"))
    assert parsed == {1: ["first", "", "third"]}


@pytest.mark.parametrize(
    "separator", ["\r", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", " ", " "]
)
def test_a_character_that_splitlines_would_cut_a_line_at_becomes_a_space(
    separator: str,
) -> None:
    md = f"--- Page 1 ---\n\nbefore{separator}after\nnext\n"

    parsed = parse_pages(render_anchored(md, doc_id="d"))

    assert parsed == {1: ["before after", "next"]}


async def test_an_anchored_pdf_page_resolves_every_cited_line(
    three_page_pdf: Path,
) -> None:
    result = await handle(
        IngestDocumentPayload(
            case_id="FCRM-2026-0151", doc_id="doc_1", path=str(three_page_pdf)
        )
    )
    parsed = parse_pages(render_anchored(result.markdown, doc_id="doc_1"))
    for page, (start, end) in result.pages.items():
        for line in range(1, end - start + 2):
            assert parsed[page][line - 1] == resolve_citation(
                result.markdown, page, line, line
            )
