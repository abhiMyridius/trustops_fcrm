"""``SheetEnricher`` — tags every chunk with the ``--- Sheet: X ---`` it falls in."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from evoq_context.chunking.types import SourceDocument, StructuredChunk


class SheetEnricher:
    """Tags a chunk with the last ``--- Sheet: X ---`` marker at or before it."""

    name = "sheet"

    def enrich(self, doc: SourceDocument, chunk: StructuredChunk) -> None:
        """Set ``metadata["sheet"]``, or leave it unset before the first marker."""
        candidates = [
            marker
            for marker in doc.index.markers
            if marker.kind == "sheet" and marker.start <= chunk.char_start
        ]
        if not candidates:
            return
        last = max(candidates, key=lambda marker: marker.start)
        chunk.metadata["sheet"] = last.value
