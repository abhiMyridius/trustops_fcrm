"""``StructureBuilder`` — the contract ``structure`` dict, derived from the model.

Scope & functionality
---------------------
Implements ``structure`` from ``AEO/contracts/canonical_document.md``
(``headings``, ``heading_count``, ``page_count``, ``sheet_count``,
``slide_count``, ``sheets``, ``table_count``, ``list_item_count``,
``code_block_count``, ``image_count``, ``requirement_ids``, ``word_count``,
``char_count``, plus the TrustOps ``pages`` page:line anchors).
Every count comes from the ``Document`` blocks and its
``Units`` facts — never from re-scanning the rendered text — with one
exception: ``Raw`` passthrough blocks (``.md``/``.txt``) have no blocks to
count, so they fall back to ``core/markdown_scan.py``. Heading line numbers
come from the renderer (``Rendered.heading_lines``); requirement ids are
scanned over the rendered body with the one shared pattern.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from evoq_ingestion.core.config import DEFAULT_REQUIREMENT_ID_PATTERN
from evoq_ingestion.core.markdown_scan import scan_markdown
from evoq_ingestion.core.profile import extract_requirement_ids
from evoq_ingestion.model.blocks import Block, CodeBlock, ListBlock, Quote, Raw, Table
from evoq_ingestion.model.document import Document
from evoq_ingestion.model.renderer import Rendered


@dataclass(slots=True)
class Structure:
    """Typed ``structure`` (contract §Metadata); ``to_dict`` is the row shape."""

    headings: list[dict[str, int | str]] = field(default_factory=list)
    heading_count: int = 0
    page_count: int | None = None
    sheet_count: int | None = None
    slide_count: int | None = None
    sheets: list[dict[str, str | int | bool]] = field(default_factory=list)
    table_count: int = 0
    list_item_count: int = 0
    code_block_count: int = 0
    image_count: int = 0
    requirement_ids: list[str] = field(default_factory=list)
    word_count: int = 0
    char_count: int = 0
    pages: list[dict[str, int]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Every contract key, always present."""
        return {
            "headings": list(self.headings),
            "heading_count": self.heading_count,
            "page_count": self.page_count,
            "sheet_count": self.sheet_count,
            "slide_count": self.slide_count,
            "sheets": list(self.sheets),
            "table_count": self.table_count,
            "list_item_count": self.list_item_count,
            "code_block_count": self.code_block_count,
            "image_count": self.image_count,
            "requirement_ids": list(self.requirement_ids),
            "word_count": self.word_count,
            "char_count": self.char_count,
            "pages": list(self.pages),
        }


class StructureBuilder:
    """Build ``Structure`` from a ``Document`` and its ``Rendered`` body."""

    def __init__(
        self,
        *,
        requirement_id_pattern: str = DEFAULT_REQUIREMENT_ID_PATTERN,
        max_headings: int = 500,
        max_requirement_ids: int = 500,
    ) -> None:
        """Configure the requirement-id pattern and the two heading/id caps."""
        self._pattern = requirement_id_pattern
        self._max_headings = max_headings
        self._max_requirement_ids = max_requirement_ids

    def from_model(self, doc: Document, rendered: Rendered) -> Structure:
        """Counts from blocks/units; ``Raw`` blocks fall back to the markdown scanner."""
        units = doc.units
        table_count = sum(1 for t in doc.tables() if _renders(t))
        # `ListBlock.item_count()` already recurses into nested list items, so
        # only *root* list blocks (top level, or nested inside a Quote — never
        # inside another list item) are summed here to avoid double-counting.
        list_item_count = sum(b.item_count() for b in _iter_root_lists(doc.blocks))
        code_block_count = sum(1 for b in doc.iter_blocks() if isinstance(b, CodeBlock))
        image_count = len(doc.image_refs())

        for raw in (b for b in doc.blocks if isinstance(b, Raw)):
            scan = scan_markdown(raw.markdown)
            table_count += scan.table_block_count
            list_item_count += scan.list_item_count
            code_block_count += scan.code_block_count
            image_count += scan.image_count

        page_breaks = len(doc.page_breaks())
        sheet_breaks = len(doc.sheet_breaks())
        slide_breaks = len(doc.slide_breaks())
        return Structure(
            headings=[h.to_dict() for h in rendered.heading_lines[: self._max_headings]],
            heading_count=len(rendered.heading_lines),
            page_count=units.page_count if units.page_count is not None else (page_breaks or None),
            sheet_count=units.sheet_count if units.sheets else (sheet_breaks or None),
            slide_count=(
                units.slide_count if units.slide_count is not None else (slide_breaks or None)
            ),
            sheets=[s.to_dict() for s in units.sheets],
            table_count=table_count,
            list_item_count=list_item_count,
            code_block_count=code_block_count,
            image_count=max(image_count, units.image_count),
            requirement_ids=extract_requirement_ids(
                rendered.markdown, self._pattern, limit=self._max_requirement_ids
            ),
            word_count=len(rendered.markdown.split()),
            char_count=len(rendered.markdown),
            pages=[p.to_dict() for p in rendered.page_lines],
        )


def _renders(table: Table) -> bool:
    return any(not row.is_blank() for row in table.rows)


def _iter_root_lists(blocks: list[Block] | tuple[Block, ...]) -> list[ListBlock]:
    """Every ``ListBlock`` not nested inside another list item's children."""
    found: list[ListBlock] = []
    for block in blocks:
        if isinstance(block, ListBlock):
            found.append(block)
        elif isinstance(block, Quote):
            found.extend(_iter_root_lists(block.children))
    return found
