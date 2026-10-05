"""Compatibility shim: ``scan_markdown_structure`` over ``core.markdown_scan``.

Scope & functionality
---------------------
The HTML and ADF renderers still derive their ``structure`` dict by
re-scanning the Markdown they produced. Until they emit ``model.Document``
blocks (lanes I6/I7) this shim keeps their call site working while the
regex grammar itself lives in exactly one place —
``evoq_ingestion/core/markdown_scan.py``. Delete this module when the last
importer is gone.
"""

from __future__ import annotations

from typing import Any

from evoq_ingestion.core.markdown_scan import scan_markdown


def scan_markdown_structure(markdown: str, *, image_count: int = 0) -> dict[str, Any]:
    """Derive heading/table/list/code/image counts from rendered Markdown."""
    scan = scan_markdown(markdown)
    return {
        "headings": [h.to_dict() for h in scan.headings[:500]],
        "heading_count": scan.heading_count,
        "page_count": None,
        "sheet_count": None,
        "slide_count": None,
        "table_count": scan.table_block_count,
        "list_item_count": scan.list_item_count,
        "code_block_count": scan.code_block_count,
        "image_count": image_count or scan.image_count,
    }
