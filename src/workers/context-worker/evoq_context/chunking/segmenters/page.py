"""``PageSegmenter`` — one segment per ``--- Page N ---`` marker.

Scope & functionality
----------------------
One ``Segment(parent_kind="page")`` per page marker (plus a preamble
segment for any content before the first one). When the document has no
page markers at all, this delegates to ``HeadingSegmenter`` when the
document has headings, else ``ParagraphSegmenter`` — the resulting
``Segment``s are exactly that sub-segmenter's, unchanged; the pipeline's
Finalizer (lane C-C) is what still stamps ``type="pdf"`` on the produced
chunks so downstream metadata stays consistent for a ``.pdf`` source with
no in-body page markers. This segmenter never sets ``type`` itself.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from evoq_context.chunking.segmenters.base import window_segment
from evoq_context.chunking.segmenters.heading import HeadingSegmenter
from evoq_context.chunking.segmenters.paragraph import ParagraphSegmenter

if TYPE_CHECKING:
    from evoq_context.chunking.budget import ChunkBudget
    from evoq_context.chunking.types import Segment, SourceDocument


class PageSegmenter:
    """One ``Segment`` per page marker (``"page_windows"``)."""

    name = "page_windows"

    def segment(self, doc: SourceDocument, budget: ChunkBudget) -> list[Segment]:
        """One segment per page marker, or a heading/paragraph fallback without any."""
        pages = sorted(
            (m for m in doc.index.markers if m.kind == "page"), key=lambda m: m.start
        )
        if not pages:
            if doc.index.headings:
                return HeadingSegmenter().segment(doc, budget)
            return ParagraphSegmenter().segment(doc, budget)

        segments: list[Segment] = []
        if pages[0].start > 0:
            preamble = window_segment(doc.text, 0, pages[0].start, "page")
            if preamble is not None:
                segments.append(preamble)
        for i, marker in enumerate(pages):
            end = pages[i + 1].start if i + 1 < len(pages) else len(doc.text)
            seg = window_segment(doc.text, marker.start, end, "page")
            if seg is not None:
                segments.append(seg)
        return segments
