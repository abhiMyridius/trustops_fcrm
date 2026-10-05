"""Row-batching for GFM tables: header/separator repeated, captions once, wide rows sliced.

Scope & functionality
----------------------
``batch_rows`` is the single place a table's data rows are turned into
chunk-ready text. It fixes the two worst table defects the audit found:

* **Every** table gets batched (the caller decides which tables to route
  here — ``SheetTableSegmenter``/``RequirementSegmenter`` route *all* of
  them, not just "the largest contiguous block").
* A single data row whose rendered width exceeds ``budget.hard_max`` on its
  own is **column-sliced** into two or more readable sub-tables (a header
  subset over a value subset), never shredded into header-less fragments.

Batches are built from ``budget.target``/``max_rows`` (whichever binds
first); the header and separator line are repeated on every batch so each
one is a self-contained, readable GFM table; the table's caption (when one
was found) is prepended to the first batch only. This is used by both
``SheetTableSegmenter``/``RequirementSegmenter`` (this lane) and the
enricher/pipeline lane's ``TableEnricher``/packer when it turns a table
segment into ``StructuredChunk``s.
"""

from __future__ import annotations

from dataclasses import dataclass

from evoq_context.chunking.budget import ChunkBudget
from evoq_context.chunking.index import TableBlock
from evoq_context.chunking.tokens import TokenEstimator


@dataclass(frozen=True, slots=True)
class TableBatch:
    """One chunk-ready slice of a table: header + separator + a run of rows.

    ``rows_range`` is 1-based, ``"{first}-{last}"`` (data rows only,
    ``header_line``/``sep_line`` excluded from the count). ``columns`` is
    the header-cell subset this batch actually carries — the full header
    for a normal batch, a narrower subset for a ``wide_split`` batch.
    """

    rows_range: str
    text: str
    columns: tuple[str, ...]
    row_count: int
    wide_split: bool = False


def _render_row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _render_sep(n: int) -> str:
    return "| " + " | ".join(["---"] * max(n, 1)) + " |"


def _assemble(
    header_cells: list[str],
    sep_line: str | None,
    row_texts: list[str],
    caption: str | None,
) -> str:
    lines: list[str] = []
    if caption:
        lines.append(caption)
        lines.append("")
    lines.append(_render_row(header_cells))
    lines.append(sep_line if sep_line else _render_sep(len(header_cells)))
    lines.extend(row_texts)
    return "\n".join(lines)


def _caption_text(table: TableBlock) -> str | None:
    return table.caption.text if table.caption is not None else None


def batch_rows(
    table: TableBlock,
    budget: ChunkBudget,
    max_rows: int,
    estimator: TokenEstimator,
) -> list[TableBatch]:
    """Batch ``table``'s data rows by token budget *and* ``max_rows``.

    Header + separator are repeated on every batch; the caption (if any) is
    prepended to the first batch only. A row that alone (with header
    repeated) exceeds ``budget.hard_max`` is column-sliced instead of being
    emitted as one oversized batch — see :func:`_column_slice`.
    """
    header_cells = list(table.header_cells)
    n_rows = len(table.data_rows)
    caption = _caption_text(table)

    if n_rows == 0:
        text = _assemble(header_cells, table.sep_line, [], caption)
        return [TableBatch(rows_range="0-0", text=text, columns=tuple(header_cells), row_count=0)]

    max_rows = max(max_rows, 1)
    batches: list[TableBatch] = []
    idx = 0
    is_first_batch = True
    while idx < n_rows:
        candidate_caption = caption if is_first_batch else None
        # How many *additional* rows (beyond the mandatory first row at
        # `idx`) could still fit under `max_rows`/`n_rows`.
        max_extra = min(max_rows, n_rows - idx) - 1

        # Binary search (not a linear per-row scan) for the largest number
        # of extra rows whose *whole accumulated batch text* still tokenizes
        # to <= budget.target. Token count of a growing prefix is
        # monotonically non-decreasing in practice (BPE re-tokenization at
        # a `\n`-bounded row boundary never shrinks the running total), so
        # this finds exactly the same boundary a linear scan would — just
        # in O(log max_rows) `estimator.count` calls instead of O(max_rows).
        # A single big table (e.g. a 200,000-row sheet, batched at
        # `max_rows` rows/batch) previously re-tokenized the *entire*
        # growing batch text once per row added, which is O(max_rows) work
        # per row and made a whole-sheet run effectively hang (minutes of
        # 100% CPU in tiktoken's regex pretokenizer, never finishing in
        # practice) — found running the shared corpus's XLSX-04 (200,001
        # rows) end-to-end during final integration; no unit test exercised
        # a table anywhere near this size.
        def _fits(k: int, _idx: int = idx, _caption: str | None = candidate_caption) -> bool:
            rows = [_render_row(list(table.data_rows[_idx + i].cells)) for i in range(k + 1)]
            text = _assemble(header_cells, table.sep_line, rows, _caption)
            return estimator.count(text) <= budget.target

        lo, hi, extra = 0, max_extra, 0
        while lo <= hi:
            mid = (lo + hi) // 2
            if mid == 0 or _fits(mid):
                extra = mid
                lo = mid + 1
            else:
                hi = mid - 1

        end = idx + extra + 1
        row_texts = [_render_row(list(table.data_rows[i].cells)) for i in range(idx, end)]

        batch_caption = candidate_caption
        text = _assemble(header_cells, table.sep_line, row_texts, batch_caption)
        if len(row_texts) == 1 and estimator.count(text) > budget.hard_max:
            batches.extend(
                _column_slice(
                    header_cells,
                    table.data_rows[idx].cells,
                    idx,
                    estimator,
                    budget,
                    batch_caption,
                )
            )
        else:
            batches.append(
                TableBatch(
                    rows_range=f"{idx + 1}-{end}",
                    text=text,
                    columns=tuple(header_cells),
                    row_count=end - idx,
                )
            )
        is_first_batch = False
        idx = end
    return batches


def _column_slice(
    header_cells: list[str],
    row_cells: tuple[str, ...],
    row_idx0: int,
    estimator: TokenEstimator,
    budget: ChunkBudget,
    caption: str | None,
) -> list[TableBatch]:
    """Split one over-``hard_max`` row into readable header-subset batches.

    Each output batch is its own tiny, self-contained GFM table: a subset
    of the header cells over the matching subset of that row's values —
    never a bare fragment of the row with no header context.
    """
    n = min(len(header_cells), len(row_cells))
    headers = header_cells[:n]
    values = list(row_cells[:n])
    batches: list[TableBatch] = []
    start = 0
    first = True
    while start < n:
        end = start + 1
        while end < n:
            seg_headers = headers[start : end + 1]
            seg_values = values[start : end + 1]
            seg_caption = caption if first else None
            text = _assemble(seg_headers, None, [_render_row(seg_values)], seg_caption)
            if estimator.count(text) > budget.hard_max:
                break
            end += 1
        seg_headers = headers[start:end]
        seg_values = values[start:end]
        seg_caption = caption if first else None
        text = _assemble(seg_headers, None, [_render_row(seg_values)], seg_caption)
        batches.append(
            TableBatch(
                rows_range=f"{row_idx0 + 1}-{row_idx0 + 1}",
                text=text,
                columns=tuple(seg_headers),
                row_count=1,
                wide_split=True,
            )
        )
        first = False
        start = end
    return batches
