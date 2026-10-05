r"""Build a model ``Table`` from raw ``w:tbl`` XML — the merged-cell fix.

Scope & functionality
----------------------
Works directly on the ``w:tbl``/``w:tr``/``w:tc`` XML rather than
python-docx's high-level ``Table.rows``/``Row.cells`` (which *expands*
merges by repeating the same cell object at every covered position — the
duplication the audit found). Two passes over the raw rows:

1. compute each vertically-merged run's total ``rowspan`` by walking
   ``w:vMerge`` top-to-bottom per column (``val="restart"`` opens a run,
   a bare ``<w:vMerge/>`` or ``val="continue"`` extends it);
2. build ``Row``/``Cell`` objects, emitting the origin cell once with its
   final ``rowspan`` and **omitting** a ``Cell`` entirely for every column
   a run covers below the origin — ``GfmRenderer``'s own pending-column
   fill is what puts the (repeated/blank/marker) text back for those rows,
   so the source text is never duplicated in the model.

``w:gridSpan`` becomes ``Cell.colspan`` directly (one cell, not N).
``w:tblHeader``-flagged leading rows become ``Table.header_rows`` (falls
back to ``1`` when the table has none, matching every other engine's
"first row is the header" default). A nested ``w:tbl`` inside a ``w:tc``
becomes a recursively-built ``Cell.nested`` entry; the cell's own text
comes only from its ``w:p`` children.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from docx.oxml.ns import qn

from evoq_ingestion.extractors.docx.parts import InlineCollector, paragraph_inline_text
from evoq_ingestion.model.blocks import Cell, Row, Table

if TYPE_CHECKING:
    from docx.oxml.xmlchemy import BaseOxmlElement


def build_table(
    tbl_elem: BaseOxmlElement, part: object, *, include_deletions: bool
) -> tuple[Table, bool]:
    """Build a ``Table`` from *tbl_elem*; returns ``(table, tracked_change_seen)``."""
    tr_elements = tbl_elem.findall(qn("w:tr"))
    rowspans = _compute_rowspans(tr_elements)
    tracked_change_seen = False
    rows: list[Row] = []
    for row_idx, tr in enumerate(tr_elements):
        cells: list[Cell] = []
        col = 0
        for tc_idx, tc in enumerate(tr.findall(qn("w:tc"))):
            colspan = _gridspan(tc)
            kind = _vmerge_kind(tc)
            if kind == "continue" and (row_idx, tc_idx) not in rowspans:
                col += colspan
                continue  # covered by an origin above; renderer fills it in
            text, tracked = _cell_text(tc, part, include_deletions=include_deletions)
            tracked_change_seen = tracked_change_seen or tracked
            nested = tuple(
                build_table(nested_tbl, part, include_deletions=include_deletions)[0]
                for nested_tbl in tc.findall(qn("w:tbl"))
            )
            cells.append(
                Cell(
                    text=text,
                    rowspan=rowspans.get((row_idx, tc_idx), 1),
                    colspan=colspan,
                    header=_is_header_row(tr),
                    nested=nested,
                )
            )
            col += colspan
        rows.append(Row(cells=tuple(cells)))
    header_rows = _leading_header_row_count(tr_elements)
    caption = _table_caption(tbl_elem)
    return Table(rows=tuple(rows), header_rows=header_rows, caption=caption), tracked_change_seen


# ------------------------------------------------------------------ rowspans
def _compute_rowspans(tr_elements: list[BaseOxmlElement]) -> dict[tuple[int, int], int]:
    origin_at_col: dict[int, tuple[int, int]] = {}
    counts: dict[tuple[int, int], int] = {}
    for row_idx, tr in enumerate(tr_elements):
        col = 0
        seen_cols_this_row: set[int] = set()
        for tc_idx, tc in enumerate(tr.findall(qn("w:tc"))):
            colspan = _gridspan(tc)
            kind = _vmerge_kind(tc)
            seen_cols_this_row.add(col)
            if kind == "restart":
                origin_at_col[col] = (row_idx, tc_idx)
                counts[(row_idx, tc_idx)] = 1
            elif kind == "continue":
                origin = origin_at_col.get(col)
                if origin is not None:
                    counts[origin] += 1
                else:
                    # malformed: a continuation with no restart above; treat as its own origin
                    counts[(row_idx, tc_idx)] = 1
            else:
                origin_at_col.pop(col, None)
            col += colspan
        # a column with an active merge that this row never visits (colspan shift) ends the run
        for stale_col in [c for c in origin_at_col if c not in seen_cols_this_row]:
            origin_at_col.pop(stale_col, None)
    return counts


# --------------------------------------------------------------------- cells
def _cell_text(tc: BaseOxmlElement, part: object, *, include_deletions: bool) -> tuple[str, bool]:
    collector = InlineCollector(part=part, include_deletions=include_deletions)
    lines = [paragraph_inline_text(p_elem, collector) for p_elem in tc.findall(qn("w:p"))]
    text = "\n".join(line for line in lines if line)
    return text, collector.tracked_change_seen


def _gridspan(tc: BaseOxmlElement) -> int:
    tc_pr = tc.find(qn("w:tcPr"))
    if tc_pr is None:
        return 1
    span = tc_pr.find(qn("w:gridSpan"))
    if span is None:
        return 1
    try:
        return max(1, int(span.get(qn("w:val"))))
    except (TypeError, ValueError):
        return 1


def _vmerge_kind(tc: BaseOxmlElement) -> str | None:
    tc_pr = tc.find(qn("w:tcPr"))
    if tc_pr is None:
        return None
    vmerge = tc_pr.find(qn("w:vMerge"))
    if vmerge is None:
        return None
    val = vmerge.get(qn("w:val"))
    return "restart" if val == "restart" else "continue"


def _is_header_row(tr: BaseOxmlElement) -> bool:
    tr_pr = tr.find(qn("w:trPr"))
    if tr_pr is None:
        return False
    flag = tr_pr.find(qn("w:tblHeader"))
    if flag is None:
        return False
    val = flag.get(qn("w:val"))
    return val not in ("0", "false", "off")


def _leading_header_row_count(tr_elements: list[BaseOxmlElement]) -> int:
    count = 0
    for tr in tr_elements:
        if not _is_header_row(tr):
            break
        count += 1
    return count or (1 if tr_elements else 0)


def _table_caption(tbl_elem: BaseOxmlElement) -> str | None:
    tbl_pr = tbl_elem.find(qn("w:tblPr"))
    if tbl_pr is None:
        return None
    caption = tbl_pr.find(qn("w:tblCaption"))
    if caption is None:
        return None
    val = caption.get(qn("w:val"))
    return val or None
