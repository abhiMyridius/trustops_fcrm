"""``CommentThreadSegmenter`` — header + comment windows, 1-comment overlap.

Scope & functionality
----------------------
A header segment (everything before the first ``### Comment N — ...``
marker) plus one segment per batch of consecutive comments, batches sized
to ``budget.target`` tokens. When a batch holds more than one comment, the
next batch starts at that batch's *last* comment — a deliberate 1-comment
overlap between adjacent windows — matching the pre-existing
``comment_thread`` strategy's semantics exactly (a batch of exactly one
oversized comment still advances, since there is no neighbour to overlap
with). Segments legitimately overlap here (unlike every other segmenter),
so a comment can be read once with its neighbour on either side.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from evoq_context.chunking.segmenters.base import window_segment
from evoq_context.chunking.segmenters.paragraph import ParagraphSegmenter
from evoq_context.chunking.tokens import get_estimator

if TYPE_CHECKING:
    from evoq_context.chunking.budget import ChunkBudget
    from evoq_context.chunking.types import Segment, SourceDocument


class CommentThreadSegmenter:
    """Header segment + overlapping comment-batch windows (``"comment_thread"``)."""

    name = "comment_thread"

    def segment(self, doc: SourceDocument, budget: ChunkBudget) -> list[Segment]:
        """Header segment + one overlapping window per batch of comments."""
        markers = sorted(
            (m for m in doc.index.markers if m.kind == "comment"), key=lambda m: m.start
        )
        if not markers:
            return ParagraphSegmenter().segment(doc, budget)

        segments: list[Segment] = []
        header = window_segment(doc.text, 0, markers[0].start, "comment_window")
        if header is not None:
            segments.append(header)

        est = get_estimator()
        n = len(markers)
        i = 0
        while i < n:
            # Comment ``i`` is always included (even alone, even oversized);
            # greedily extend while the running batch still fits the target.
            last = i
            j = i + 1
            while j < n:
                candidate_end = markers[j + 1].start if j + 1 < n else len(doc.text)
                candidate_text = doc.text[markers[i].start : candidate_end]
                if est.count(candidate_text) > budget.target:
                    break
                last = j
                j += 1
            end = markers[last + 1].start if last + 1 < n else len(doc.text)
            seg = window_segment(doc.text, markers[i].start, end, "comment_window")
            if seg is not None:
                segments.append(seg)
            if last >= n - 1:
                break
            # 1-comment overlap: the next batch starts at this batch's last
            # comment, but only when the batch held more than one comment —
            # a single oversized comment with no neighbour must still advance.
            i = last if last > i else last + 1
        return segments
