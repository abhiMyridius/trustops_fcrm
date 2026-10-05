"""``PageEnricher`` — tags every chunk with the PDF page it falls in.

Scope & functionality
----------------------
``metadata["page"]`` is the number of the last ``--- Page N ---`` marker at
or before ``chunk.char_start`` — computed from ``doc.index.markers``, so it
applies to a chunk produced by *any* segmenter, not just ``page_windows``.
Text before the first page marker gets no ``page`` key at all (never a
misleading ``page=0``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from evoq_context.chunking.types import SourceDocument, StructuredChunk


class PageEnricher:
    """Tags a chunk with the last ``--- Page N ---`` marker at or before it."""

    name = "page"

    def enrich(self, doc: SourceDocument, chunk: StructuredChunk) -> None:
        """Set ``metadata["page"]``, or leave it unset before the first marker."""
        candidates = [
            marker
            for marker in doc.index.markers
            if marker.kind == "page" and marker.start <= chunk.char_start
        ]
        if not candidates:
            return
        last = max(candidates, key=lambda marker: marker.start)
        chunk.metadata["page"] = int(last.value)
