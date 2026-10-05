"""``RequirementSegmenter`` — only a *line-leading* id starts a new block.

Scope & functionality
----------------------
The old ``requirement_units``/``_requirement_table_blocks`` path let *any*
requirement-id match — even one merely mentioned mid-sentence — start a
new block, and a single 3-row table containing one match could hijack the
entire document (28 of 29 prose requirements silently lost their tag). This
segmenter only ever starts a new block at a ``ReqHit`` that is
``line_leading`` (the id is the first token of its paragraph, list item, or
table row): a block runs from one such hit's containing unit to the next
one's, and a mid-prose mention never starts a block — it just stays inside
whatever block is already open.

A ``TableBlock`` that has a line-leading id in any of its data rows becomes
its own block, on its own (never merged with neighbouring prose, since it
is row-batched downstream via ``chunking.tables.batch_rows``, the same
handoff ``SheetTableSegmenter`` uses); a table with no line-leading id is
just ordinary content of whatever block it falls inside. Small adjacent
prose blocks coalesce toward ``budget.target`` (a quick heuristic estimator
— the real, configured tokenizer re-measures everything downstream in the
packer, so this coalescing only needs to be a reasonable approximation).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from evoq_context.chunking.segmenters.base import window_segment
from evoq_context.chunking.text_ops import split_units
from evoq_context.chunking.tokens import get_estimator

if TYPE_CHECKING:
    from evoq_context.chunking.budget import ChunkBudget
    from evoq_context.chunking.types import Segment, SourceDocument


def _table_range_at(table_ranges: list[tuple[int, int]], pos: int) -> tuple[int, int] | None:
    for start, end in table_ranges:
        if start <= pos < end:
            return start, end
    return None


class RequirementSegmenter:
    """One block per line-leading requirement id (``"requirement_units"``)."""

    name = "requirement_units"

    def segment(self, doc: SourceDocument, budget: ChunkBudget) -> list[Segment]:
        """One block per line-leading requirement id; mid-prose mentions never start one."""
        text = doc.text
        leading_hits = sorted(
            (h for h in doc.index.requirement_hits if h.line_leading), key=lambda h: h.start
        )
        if not leading_hits:
            seg = window_segment(text, 0, len(text), "window")
            return [seg] if seg is not None else []

        leading_starts = {h.start for h in leading_hits}
        qualifying_tables = sorted(
            (
                t
                for t in doc.index.tables
                if any(t.start <= h.start < t.end for h in leading_hits)
            ),
            key=lambda t: t.start,
        )
        table_ranges = [(t.start, t.end) for t in qualifying_tables]

        blocks = self._build_blocks(text, leading_starts, table_ranges)
        merged = self._coalesce(text, blocks, table_ranges, budget)

        segments: list[Segment] = []
        for start, end in merged:
            seg = window_segment(text, start, end, "requirement_block")
            if seg is not None:
                segments.append(seg)
        return segments

    @staticmethod
    def _build_blocks(
        text: str,
        leading_starts: set[int],
        table_ranges: list[tuple[int, int]],
    ) -> list[tuple[int, int]]:
        units = split_units(text)
        blocks: list[tuple[int, int]] = []
        cur_start: int | None = None
        cur_end = 0
        i, n = 0, len(units)
        while i < n:
            unit = units[i]
            table_range = _table_range_at(table_ranges, unit.start)
            if table_range is not None:
                if cur_start is not None:
                    blocks.append((cur_start, cur_end))
                    cur_start = None
                blocks.append(table_range)
                while i < n and units[i].start < table_range[1]:
                    i += 1
                continue
            if unit.start in leading_starts:
                if cur_start is not None:
                    blocks.append((cur_start, cur_end))
                cur_start, cur_end = unit.start, unit.end
            else:
                if cur_start is None:
                    cur_start = unit.start
                cur_end = unit.end
            i += 1
        if cur_start is not None:
            blocks.append((cur_start, cur_end))
        blocks.sort(key=lambda b: b[0])
        return blocks

    @staticmethod
    def _coalesce(
        text: str,
        blocks: list[tuple[int, int]],
        table_ranges: list[tuple[int, int]],
        budget: ChunkBudget,
    ) -> list[tuple[int, int]]:
        est = get_estimator()
        merged: list[tuple[int, int]] = []
        for start, end in blocks:
            is_table = _table_range_at(table_ranges, start) is not None
            if merged and not is_table:
                p_start, p_end = merged[-1]
                if _table_range_at(table_ranges, p_start) is None:
                    candidate = text[p_start:end]
                    if est.count(candidate) <= budget.target:
                        merged[-1] = (p_start, end)
                        continue
            merged.append((start, end))
        return merged
