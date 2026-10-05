"""``RequirementIdEnricher`` — cross-strategy requirement-id tagging.

Scope & functionality
----------------------
``metadata["requirement_ids"]`` is the sorted, comma-joined set of every
requirement id (``doc.index.requirement_hits``) whose match starts inside
``[chunk.char_start, chunk.char_end)`` — computed from the document-global
index, so a ``page_windows`` chunk containing a line-leading requirement id
now gets tagged, which it never did before (the old ``requirement_units``
strategy was the only place ids were ever recorded).

**Block inheritance**: when a requirement block gets windowed (split into
multiple chunks by the packer/guard), every window should carry the FULL
block's ids, not just the ids literally inside that window's text slice.
Rather than depending on which segmenter produced the chunk, this derives
blocks directly from the index: a block runs from one line-leading hit to
the next *structural boundary* — the next line-leading hit, heading, or
marker, whichever comes first (or document end, if none) — so a single
line-leading id near the end of a section/page never bleeds into unrelated
content past that boundary. A chunk that overlaps a block at all inherits
every id in that block — a superset of "ids whose start falls in range"
that works for *any* segmenter's output, not only ``RequirementSegmenter``'s.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from evoq_context.vector import contract

if TYPE_CHECKING:
    from evoq_context.chunking.types import SourceDocument, StructuredChunk


class RequirementIdEnricher:
    """Tags a chunk with every requirement id in range, block-inheritance-aware."""

    name = "requirement"

    def enrich(self, doc: SourceDocument, chunk: StructuredChunk) -> None:
        """Set ``metadata["requirement_ids"]`` (see module docstring for the rule)."""
        hits = doc.index.requirement_hits
        if not hits:
            return
        ids: set[str] = {
            hit.id for hit in hits if chunk.char_start <= hit.start < chunk.char_end
        }
        ids |= self._block_ids_overlapping(doc, chunk)
        if ids:
            chunk.metadata["requirement_ids"] = contract.join_list_metadata(sorted(ids))

    @staticmethod
    def _block_ids_overlapping(doc: SourceDocument, chunk: StructuredChunk) -> set[str]:
        hits = doc.index.requirement_hits
        leaders = sorted((h for h in hits if h.line_leading), key=lambda h: h.start)
        if not leaders:
            return set()
        # A block ends at the next line-leading id OR the next heading/marker
        # (whichever is closer) — never bleeds past a structural boundary
        # even when no other requirement id follows it in the document.
        boundary_starts = sorted(
            {h.start for h in leaders}
            | {heading.start for heading in doc.index.headings}
            | {marker.start for marker in doc.index.markers}
        )
        out: set[str] = set()
        for leader in leaders:
            block_start = leader.start
            block_end = next(
                (b for b in boundary_starts if b > block_start), len(doc.text)
            )
            if chunk.char_start < block_end and chunk.char_end > block_start:
                out.update(h.id for h in hits if block_start <= h.start < block_end)
        return out
