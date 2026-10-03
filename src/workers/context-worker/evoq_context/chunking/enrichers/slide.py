"""``SlideEnricher`` — tags every chunk with the ``--- Slide N ---`` it falls in."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from evoq_context.chunking.types import SourceDocument, StructuredChunk


class SlideEnricher:
    """Tags a chunk with the last ``--- Slide N ---`` marker at or before it."""

    name = "slide"

    def enrich(self, doc: SourceDocument, chunk: StructuredChunk) -> None:
        """Set ``metadata["slide"]``, or leave it unset before the first marker."""
        candidates = [
            marker
            for marker in doc.index.markers
            if marker.kind == "slide" and marker.start <= chunk.char_start
        ]
        if not candidates:
            return
        last = max(candidates, key=lambda marker: marker.start)
        chunk.metadata["slide"] = int(last.value)
