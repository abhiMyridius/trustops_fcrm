"""``TableEnricher`` — table metadata plus a correctness backstop.

Scope & functionality
----------------------
For a chunk overlapping one of ``doc.index.tables``: sets ``columns`` (the
header cells, ``join_list_metadata``-joined), and — when the chunk
overlaps exactly one table — ``row_range`` (1-based, e.g. ``"12-18"``) and
``row_count`` for the data rows the chunk actually covers. A chunk
overlapping more than one (small) table gets the first table's ``columns``
only, no ``row_range``/``row_count`` (there is no single row range to
report).

This is not just an annotator: if the chunk's text does not already start
with that table's header + separator line — a segmenter may have forgotten
it when windowing a table — the header/separator is **prepended** via
``injected_prefix`` (never mutating ``char_start``/``char_end``, so the
offset invariant holds).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from evoq_context.vector import contract

if TYPE_CHECKING:
    from evoq_context.chunking.index import TableBlock
    from evoq_context.chunking.types import SourceDocument, StructuredChunk


def _overlapping_tables(doc: SourceDocument, chunk: StructuredChunk) -> list[TableBlock]:
    return [
        table
        for table in doc.index.tables
        if table.start < chunk.char_end and table.end > chunk.char_start
    ]


def _overlapping_row_numbers(table: TableBlock, chunk: StructuredChunk) -> list[int]:
    return [
        i + 1
        for i, row in enumerate(table.data_rows)
        if row.start < chunk.char_end and row.end > chunk.char_start
    ]


class TableEnricher:
    """Tags a chunk with table metadata and repairs a missing header (see module docstring)."""

    name = "table"

    def enrich(self, doc: SourceDocument, chunk: StructuredChunk) -> None:
        """Set ``columns``/``row_range``/``row_count`` and backstop the header."""
        tables = _overlapping_tables(doc, chunk)
        if not tables:
            return
        primary = tables[0]
        chunk.metadata["columns"] = contract.join_list_metadata(list(primary.header_cells))
        if len(tables) == 1:
            rows = _overlapping_row_numbers(primary, chunk)
            if rows:
                chunk.metadata["row_range"] = f"{rows[0]}-{rows[-1]}"
                chunk.metadata["row_count"] = len(rows)
        self._ensure_header_leads(chunk, primary)

    @staticmethod
    def _ensure_header_leads(chunk: StructuredChunk, table: TableBlock) -> None:
        # "Missing" means the header doesn't appear anywhere in the chunk's
        # rendered text at all — not just "doesn't lead it". A chunk that
        # legitimately contains prose *before* the table (a paragraph_windows
        # window spanning a small, non-table-heavy document, say) already
        # carries the header in its body, right before the data rows; only a
        # chunk that is *purely* a table window with the header genuinely
        # dropped (a segmenter forgot it, or ``batch_rows`` repeats it per
        # batch and this window skipped that) needs the backstop.
        if table.header_line.strip() in chunk.text:
            return
        header_block = table.header_line
        if table.sep_line:
            header_block += "\n" + table.sep_line
        prefix = header_block + "\n"
        chunk.text = prefix + chunk.text
        chunk.injected_prefix = prefix + chunk.injected_prefix
