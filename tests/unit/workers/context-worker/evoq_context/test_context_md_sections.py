"""The five context-bundle sections appended to context.md (plan §1.3/§3.7)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from evoq_context.core.config import ContextConfig
from evoq_context.core.contracts import ContextBuildRequest
from evoq_context.core.models import ExtractedItem
from evoq_context.pipeline.build import build_context


def _build(
    dummy_config: ContextConfig, items: list[ExtractedItem], project: str = "proj"
):
    req = ContextBuildRequest(
        project=project, goal_prompt="Understand the system", extracted_items=items
    )
    return asyncio.run(build_context(req, dummy_config))


def test_all_five_sections_present_with_canonical_headers(
    dummy_config: ContextConfig,
) -> None:
    items = [
        ExtractedItem(
            source_type="confluence",
            source_id="SRC1",
            title="Requirements",
            body="# Requirements\n\n"
            + "\n\n".join(
                f"REQ-{i:03d}: The system shall do thing {i}." for i in range(1, 8)
            ),
            metadata={"url": "https://wiki/x"},
        ),
    ]
    result = _build(dummy_config, items)
    assert result.status == "success"
    body = Path(result.context_location["context_md"]).read_text(encoding="utf-8")

    assert "## Document map" in body
    assert (
        "| doc_id | title | source_type | url | source_version | chunk_strategy | chunk_count | sections |"
        in body
    )
    assert "## Requirements index" in body
    assert (
        "| req_id | statement | doc_id | section_path | page | url | chunk_id |" in body
    )
    assert "## Glossary / entities" in body
    assert "| term | kind | definition | doc_ids |" in body
    assert "## Retrieval hints" in body


def test_document_map_rows_reflect_chunk_strategy_and_count(
    dummy_config: ContextConfig,
) -> None:
    items = [
        ExtractedItem(
            source_type="file",
            source_id="F1",
            title="Deck.pptx",
            body="--- Slide 1 ---\nHello\n--- Slide 2 ---\nWorld",
            metadata={"source_path": "Deck.pptx"},
        ),
    ]
    result = _build(dummy_config, items)
    body = Path(result.context_location["context_md"]).read_text(encoding="utf-8")
    assert "slide_units" in body
    assert "file:F1" in body


def test_requirements_index_sorted_by_req_id_and_has_chunk_ids(
    dummy_config: ContextConfig,
) -> None:
    items = [
        ExtractedItem(
            source_type="confluence",
            source_id="SRC1",
            title="Reqs",
            body="# Reqs\n\n"
            + "\n\n".join(
                f"REQ-{i:03d}: Statement {i}." for i in (3, 1, 2, 9, 5, 6, 7, 8)
            ),
        ),
    ]
    result = _build(dummy_config, items)
    body = Path(result.context_location["context_md"]).read_text(encoding="utf-8")

    lines = [ln for ln in body.splitlines() if ln.startswith("| REQ-")]
    req_ids = [ln.split("|")[1].strip() for ln in lines]
    assert req_ids == sorted(req_ids)
    assert len(req_ids) >= 8
    for ln in lines:
        cells = [c.strip() for c in ln.split("|")]
        chunk_id_cell = cells[-2]
        assert chunk_id_cell  # every requirement row cites a chunk_id


def test_pinned_skeleton_headings_unchanged(dummy_config: ContextConfig) -> None:
    items = [
        ExtractedItem(
            source_type="file", source_id="F1", title="Doc", body="Some text."
        )
    ]
    result = _build(dummy_config, items)
    body = Path(result.context_location["context_md"]).read_text(encoding="utf-8")
    for heading in (
        "# proj — Project context v2",
        "## Scope and source coverage",
        "## Contextual goals",
        "## What was given (direct evidence)",
        "## What was found (synthesized findings)",
        "## What was inferred",
        "## Conflicts and ambiguities",
        "## Missing information",
        "## Operational implications",
        "## Traceability index",
    ):
        assert heading in body
