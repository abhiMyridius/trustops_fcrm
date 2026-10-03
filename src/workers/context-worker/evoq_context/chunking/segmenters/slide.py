"""``SlideSegmenter`` — one segment per ``--- Slide N ---`` marker.

Scope & functionality
----------------------
One ``Segment(parent_kind="slide")`` per slide marker (plus a preamble
segment for any content before the first one, and a fallback to
``ParagraphSegmenter`` when there are no slide markers at all). A table
inside a slide is an atomic ``kind="table"`` unit via ``split_units`` — it
is never split mid-table by this segmenter; when it is larger than the
target it is row-batched downstream via ``chunking.tables.batch_rows``
(the pipeline detects an atomic table unit and routes it through
``batch_rows`` using the matching ``doc.index.tables`` entry, the same
handoff ``SheetTableSegmenter`` uses).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from evoq_context.chunking.segmenters.base import window_segment
from evoq_context.chunking.segmenters.paragraph import ParagraphSegmenter

if TYPE_CHECKING:
    from evoq_context.chunking.budget import ChunkBudget
    from evoq_context.chunking.types import Segment, SourceDocument


class SlideSegmenter:
    """One ``Segment`` per slide marker (``"slide_units"``)."""

    name = "slide_units"

    def segment(self, doc: SourceDocument, budget: ChunkBudget) -> list[Segment]:
        """One segment per slide marker, or a paragraph-window fallback without any."""
        slides = sorted(
            (m for m in doc.index.markers if m.kind == "slide"), key=lambda m: m.start
        )
        if not slides:
            return ParagraphSegmenter().segment(doc, budget)

        segments: list[Segment] = []
        if slides[0].start > 0:
            preamble = window_segment(doc.text, 0, slides[0].start, "slide")
            if preamble is not None:
                segments.append(preamble)
        for i, marker in enumerate(slides):
            end = slides[i + 1].start if i + 1 < len(slides) else len(doc.text)
            seg = window_segment(doc.text, marker.start, end, "slide")
            if seg is not None:
                segments.append(seg)
        return segments
