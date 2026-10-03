"""``ParagraphSegmenter`` — the default, marker-bounded prose windows.

Scope & functionality
----------------------
One ``Segment(parent_kind="window")`` per region bounded by a structural
marker (page/sheet/slide/comment) — the whole document when there are none.
This is the fallback every other segmenter delegates to, and the segment
the pipeline's ``TopicBoundaryRefiner`` (lane C-C) further cuts into
topic-aligned sub-segments; this segmenter only needs to produce the
top-level window(s), not detect topics itself.
"""

from __future__ import annotations

from itertools import pairwise
from typing import TYPE_CHECKING

from evoq_context.chunking.segmenters.base import window_segment

if TYPE_CHECKING:
    from evoq_context.chunking.budget import ChunkBudget
    from evoq_context.chunking.types import Segment, SourceDocument


class ParagraphSegmenter:
    """One ``window`` ``Segment`` per marker-bounded region (``"paragraph_windows"``)."""

    name = "paragraph_windows"

    def segment(self, doc: SourceDocument, budget: ChunkBudget) -> list[Segment]:
        """One ``window`` segment per marker-bounded region."""
        bounds = sorted({m.start for m in doc.index.markers})
        points = sorted({0, *bounds, len(doc.text)})
        segments: list[Segment] = []
        for start, end in pairwise(points):
            seg = window_segment(doc.text, start, end, "window")
            if seg is not None:
                segments.append(seg)
        return segments
