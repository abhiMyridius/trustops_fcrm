"""Compatibility shim: text-derived ``structure`` for the pre-model extractors.

Scope & functionality
---------------------
The in-process extractors that still emit Markdown directly (pdf/docx/xlsx/
csv/pptx/text before lanes I2–I5) derive their ``structure`` dict here by
scanning that Markdown with the one shared grammar
(``core.markdown_scan`` via ``renderers._common``) and the one shared
requirement-id pattern (``core.config.DEFAULT_REQUIREMENT_ID_PATTERN``).
New extractors build a ``model.Document`` and use
``model.structure.StructureBuilder`` instead; delete this module when the
last importer is gone.
"""

from __future__ import annotations

from typing import Any

from evoq_ingestion.core.config import DEFAULT_REQUIREMENT_ID_PATTERN
from evoq_ingestion.core.profile import extract_requirement_ids
from evoq_ingestion.renderers._common import scan_markdown_structure


def build_structure(
    markdown: str,
    *,
    page_count: int | None = None,
    sheet_count: int | None = None,
    slide_count: int | None = None,
    sheets: list[dict[str, Any]] | None = None,
    requirement_id_pattern: str = DEFAULT_REQUIREMENT_ID_PATTERN,
    structure_max_headings: int = 500,
    structure_max_requirement_ids: int = 500,
) -> dict[str, Any]:
    """Build the contract ``structure`` dict for an extracted Markdown body."""
    base = scan_markdown_structure(markdown, image_count=markdown.count("!["))
    requirement_ids = extract_requirement_ids(
        markdown, requirement_id_pattern, limit=structure_max_requirement_ids
    )
    return {
        "headings": base["headings"][:structure_max_headings],
        "heading_count": base["heading_count"],
        "page_count": page_count if page_count is not None else base["page_count"],
        "sheet_count": sheet_count if sheet_count is not None else base["sheet_count"],
        "slide_count": slide_count if slide_count is not None else base["slide_count"],
        "sheets": sheets or [],
        "table_count": base["table_count"],
        "list_item_count": base["list_item_count"],
        "code_block_count": base["code_block_count"],
        "image_count": base["image_count"],
        "requirement_ids": requirement_ids,
        "word_count": len(markdown.split()),
        "char_count": len(markdown),
    }
