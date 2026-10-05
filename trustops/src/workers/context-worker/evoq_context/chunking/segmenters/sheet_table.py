"""``SheetTableSegmenter`` — every table gets its own segment (the #1 fix).

Scope & functionality
----------------------
The old ``table_aware`` strategy found the single *largest contiguous run*
of GFM table rows and dumped every other table into surrounding prose with
no header repetition, no ``columns``, no ``row_range``. This segmenter
walks **every** ``TableBlock`` in ``doc.index.tables`` — none are dropped —
and produces one ``Segment`` per table plus ``window`` segments for the
prose between them.

When ``--- Sheet: X ---`` markers are present, segmentation is scoped
per-sheet first (so every chunk in that region can be tagged
``sheet=X`` by ``SheetEnricher``, including a sheet that turns out to be
pure prose — the old code never populated ``sheet`` on that path); within
each sheet the same "one segment per table + window segments between"
split runs, since a spreadsheet sheet is usually rendered as exactly one
table but is not guaranteed to be.

Each table ``Segment`` carries exactly one atomic ``kind="table"`` unit
spanning the whole table (via ``split_units``); a ``Segment`` is only ever
an exact slice of the source, so the actual header-repeated, per-batch
rendering that ``chunking.tables.batch_rows`` produces is applied
downstream, in the pipeline's "pack children" stage (lane C-C), once it
sees a table segment/unit and looks up the matching ``TableBlock`` by
character range. This segmenter's contract with that stage is: a segment
whose ``.units`` is exactly one ``kind="table"`` span is a table segment to
route through ``batch_rows``; everything else is packed by the ordinary
token budget packer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from evoq_context.chunking.segmenters.base import window_segment
from evoq_context.chunking.types import ParentKind

if TYPE_CHECKING:
    from evoq_context.chunking.budget import ChunkBudget
    from evoq_context.chunking.index import TableBlock
    from evoq_context.chunking.types import Segment, SourceDocument


def _tables_in(doc: SourceDocument, start: int, end: int) -> list[TableBlock]:
    return sorted(
        (t for t in doc.index.tables if t.start >= start and t.end <= end),
        key=lambda t: t.start,
    )


def _segment_region(
    doc: SourceDocument,
    start: int,
    end: int,
    *,
    table_kind: ParentKind,
    prose_kind: ParentKind,
) -> list[Segment]:
    """Split ``[start, end)`` into table segments (every table) + prose windows between."""
    tables = _tables_in(doc, start, end)
    if not tables:
        seg = window_segment(doc.text, start, end, prose_kind)
        return [seg] if seg is not None else []

    segments: list[Segment] = []
    cursor = start
    for table in tables:
        if table.start > cursor:
            pre = window_segment(doc.text, cursor, table.start, prose_kind)
            if pre is not None:
                segments.append(pre)
        seg = window_segment(doc.text, table.start, table.end, table_kind)
        if seg is not None:
            segments.append(seg)
        cursor = table.end
    if cursor < end:
        tail = window_segment(doc.text, cursor, end, prose_kind)
        if tail is not None:
            segments.append(tail)
    return segments


class SheetTableSegmenter:
    """One segment per sheet marker, or per table block otherwise (``"table_aware"``)."""

    name = "table_aware"

    def segment(self, doc: SourceDocument, budget: ChunkBudget) -> list[Segment]:
        """One segment per sheet marker, else one per table block plus prose windows."""
        sheets = sorted(
            (m for m in doc.index.markers if m.kind == "sheet"), key=lambda m: m.start
        )
        if not sheets:
            # No sheet markers: every table block gets its own segment; prose
            # between tables is plain "window" content, not sheet-tagged.
            return _segment_region(doc, 0, len(doc.text), table_kind="sheet", prose_kind="window")

        segments: list[Segment] = []
        if sheets[0].start > 0:
            segments.extend(
                _segment_region(doc, 0, sheets[0].start, table_kind="sheet", prose_kind="window")
            )
        for i, marker in enumerate(sheets):
            end = sheets[i + 1].start if i + 1 < len(sheets) else len(doc.text)
            segments.extend(
                _segment_region(doc, marker.start, end, table_kind="sheet", prose_kind="sheet")
            )
        return segments
