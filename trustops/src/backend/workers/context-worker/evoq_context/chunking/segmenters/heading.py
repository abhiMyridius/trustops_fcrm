"""``HeadingSegmenter`` — one section per heading, ATX and numbered alike.

Scope & functionality
----------------------
One ``Segment(parent_kind="section")`` per heading in ``doc.index.headings``
(already ATX + numbered, correctly nested — see ``chunking/index.py``),
spanning from that heading's line to the next heading's line (any level;
sub-headings get their own section, same as before); a preamble segment for
any text before the first heading, titled ``doc.title`` when set. Every
segment's ``heading_line`` is the heading's *actual* source line
(``doc.text[heading.start:heading.end]`` — ``"### Deep Dive"``, never a
hardcoded ``"## "``), which the pipeline's packer injects as a prefix on
every child chunk of that section — the fix for the old code's
level-blind ``f"## {heading}"``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from evoq_context.chunking.segmenters.base import window_segment

if TYPE_CHECKING:
    from evoq_context.chunking.budget import ChunkBudget
    from evoq_context.chunking.types import Segment, SourceDocument


class HeadingSegmenter:
    """One ``Segment`` per heading (``"heading_sections"``)."""

    name = "heading_sections"

    def segment(self, doc: SourceDocument, budget: ChunkBudget) -> list[Segment]:
        """One section per heading, plus a title-prefixed preamble section."""
        headings = sorted(doc.index.headings, key=lambda h: h.start)
        if not headings:
            seg = window_segment(doc.text, 0, len(doc.text), "section", heading_line=doc.title)
            return [seg] if seg is not None else []

        segments: list[Segment] = []
        if headings[0].start > 0:
            preamble = window_segment(
                doc.text, 0, headings[0].start, "section", heading_line=doc.title
            )
            if preamble is not None:
                segments.append(preamble)

        for i, heading in enumerate(headings):
            end = headings[i + 1].start if i + 1 < len(headings) else len(doc.text)
            heading_line = doc.text[heading.start : heading.end]
            seg = window_segment(doc.text, heading.start, end, "section", heading_line=heading_line)
            if seg is not None:
                segments.append(seg)
        return segments
