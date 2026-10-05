"""``Enricher`` — the structural-metadata pass that runs on every chunk.

Scope & functionality
----------------------
An ``Enricher`` mutates ``chunk.metadata`` in place using ``doc.index``
(the one-pass ``DocumentIndex``) and the chunk's own ``char_start``/
``char_end`` — never the segmenter's internal state. This is the core fix
the whole lane exists to make: today, picking a segmentation *strategy*
forfeits every other strategy's metadata (a ``page_windows`` chunk never
got ``requirement_ids``; a ``requirement_units`` chunk never got
``page``/``section_path``). Because every enricher in ``DEFAULT_ENRICHERS``
runs on *every* chunk regardless of which segmenter produced it, that
coupling is gone: metadata is a property of *where in the document* a
chunk sits, not of *how* it was cut.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from evoq_context.chunking.types import SourceDocument, StructuredChunk


@runtime_checkable
class Enricher(Protocol):
    """One independent, idempotent metadata pass over a single chunk."""

    name: str

    def enrich(self, doc: SourceDocument, chunk: StructuredChunk) -> None:
        """Mutate ``chunk.metadata`` using ``doc.index`` and the chunk's offsets."""
        ...
