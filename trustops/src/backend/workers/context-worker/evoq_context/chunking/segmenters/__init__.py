"""The seven segmenters and the ``SEGMENTERS`` registry the selector picks from.

Scope & functionality
----------------------
Exactly one ``Segmenter`` runs per document — the profiler/selector (lane
C-D) picks a strategy name and looks it up here, in ``SEGMENTERS: Mapping[
str, type[Segmenter]]`` (a name -> **class** registry, not instances —
callers instantiate, e.g. ``SEGMENTERS[decision.strategy]()``). Every
segmenter below builds its units with ``text_ops.split_units`` (fenced
code and GFM tables atomic, list items grouped, structural
markers/headings as their own hard-boundary units), so no unit from any of
the seven ever spans a page/sheet/slide/comment marker, whichever strategy
fires — see each module's docstring for its specific boundary rule.

All seven concrete segmenters are implemented here:
``heading_sections``, ``page_windows``, ``slide_units``, ``table_aware``,
``paragraph_windows``, ``comment_thread``, ``requirement_units``.
"""

from __future__ import annotations

from collections.abc import Mapping

from evoq_context.chunking.segmenters.base import Segmenter, window_segment
from evoq_context.chunking.segmenters.comment_thread import CommentThreadSegmenter
from evoq_context.chunking.segmenters.heading import HeadingSegmenter
from evoq_context.chunking.segmenters.page import PageSegmenter
from evoq_context.chunking.segmenters.paragraph import ParagraphSegmenter
from evoq_context.chunking.segmenters.requirement import RequirementSegmenter
from evoq_context.chunking.segmenters.sheet_table import SheetTableSegmenter
from evoq_context.chunking.segmenters.slide import SlideSegmenter

#: Strategy name -> segmenter **class**. The profiler/selector (lane C-D)
#: looks up a ``StrategyDecision.strategy`` here and instantiates it.
SEGMENTERS: Mapping[str, type[Segmenter]] = {
    HeadingSegmenter.name: HeadingSegmenter,
    PageSegmenter.name: PageSegmenter,
    SlideSegmenter.name: SlideSegmenter,
    SheetTableSegmenter.name: SheetTableSegmenter,
    ParagraphSegmenter.name: ParagraphSegmenter,
    CommentThreadSegmenter.name: CommentThreadSegmenter,
    RequirementSegmenter.name: RequirementSegmenter,
}

__all__ = [
    "SEGMENTERS",
    "Segmenter",
    "HeadingSegmenter",
    "PageSegmenter",
    "SlideSegmenter",
    "SheetTableSegmenter",
    "ParagraphSegmenter",
    "CommentThreadSegmenter",
    "RequirementSegmenter",
    "window_segment",
]
