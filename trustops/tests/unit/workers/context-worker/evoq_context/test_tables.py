"""Tests for ``chunking.tables.batch_rows``.

Covers: header + separator repeated on every batch, ``max_rows``/token
budget batching, caption on the first batch only, every table (not just
"the largest") batching independently, and a row wider than ``hard_max``
being column-sliced into readable header-subset/value-subset batches
rather than shredded.
"""

from __future__ import annotations

import re

from evoq_context.chunking.budget import ChunkBudget
from evoq_context.chunking.index import DocumentIndex
from evoq_context.chunking.tables import batch_rows
from evoq_context.chunking.tokens import HeuristicEstimator
from evoq_context.core.limits import DEFAULT_REQUIREMENT_ID_PATTERN

PATTERN = re.compile(DEFAULT_REQUIREMENT_ID_PATTERN)
EST = HeuristicEstimator()


def _single_table(text: str):
    idx = DocumentIndex.build(text, PATTERN, numbered_headings=False)
    assert len(idx.tables) == 1
    return idx.tables[0]


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


class TestBatching:
    def test_header_and_separator_repeated_on_every_batch(self) -> None:
        rows = "\n".join(f"| r{i} | v{i} |" for i in range(10))
        text = f"| Code | Val |\n| --- | --- |\n{rows}\n"
        table = _single_table(text)
        budget = ChunkBudget(target=1000, overlap=0, min=1, hard_max=2000)
        batches = batch_rows(table, budget, max_rows=4, estimator=EST)
        assert len(batches) == 3
        assert [b.row_count for b in batches] == [4, 4, 2]
        for batch in batches:
            lines = batch.text.splitlines()
            assert lines[0] == "| Code | Val |"
            assert lines[1].startswith("| ---")

    def test_max_rows_caps_batch_size(self) -> None:
        rows = "\n".join(f"| r{i} | v{i} |" for i in range(5))
        text = f"| A | B |\n| --- | --- |\n{rows}\n"
        table = _single_table(text)
        budget = ChunkBudget(target=100_000, overlap=0, min=1, hard_max=200_000)
        batches = batch_rows(table, budget, max_rows=2, estimator=EST)
        assert [b.row_count for b in batches] == [2, 2, 1]
        assert [b.rows_range for b in batches] == ["1-2", "3-4", "5-5"]

    def test_caption_prepended_to_first_batch_only(self) -> None:
        rows = "\n".join(f"| r{i} | v{i} |" for i in range(6))
        text = f"Table 1: Sample.\n| Code | Val |\n| --- | --- |\n{rows}\n"
        table = _single_table(text)
        budget = ChunkBudget(target=1000, overlap=0, min=1, hard_max=2000)
        batches = batch_rows(table, budget, max_rows=3, estimator=EST)
        assert len(batches) == 2
        assert batches[0].text.startswith("Table 1: Sample.")
        assert "Table 1" not in batches[1].text

    def test_multiple_tables_batch_independently(self) -> None:
        text = (
            "Intro.\n\n"
            "| A | B |\n| --- | --- |\n| 1 | 2 |\n\n"
            "Middle.\n\n"
            "| C | D |\n| --- | --- |\n| 3 | 4 |\n| 5 | 6 |\n\n"
            "| E | F |\n| --- | --- |\n| 7 | 8 |\n"
        )
        idx = DocumentIndex.build(text, PATTERN, numbered_headings=False)
        assert len(idx.tables) == 3
        budget = ChunkBudget(target=1000, overlap=0, min=1, hard_max=2000)
        for table in idx.tables:
            batches = batch_rows(table, budget, max_rows=500, estimator=EST)
            assert len(batches) == 1
            assert batches[0].row_count == len(table.data_rows)
            assert batches[0].columns == table.header_cells

    def test_header_only_table_produces_one_empty_batch(self) -> None:
        text = "| A | B |\n| --- | --- |\n"
        table = _single_table(text)
        budget = ChunkBudget(target=1000, overlap=0, min=1, hard_max=2000)
        batches = batch_rows(table, budget, max_rows=500, estimator=EST)
        assert len(batches) == 1
        assert batches[0].row_count == 0


class TestWideRowColumnSlicing:
    def test_row_wider_than_hard_max_is_column_sliced_and_readable(self) -> None:
        n_cols = 20
        header = "| " + " | ".join(f"Col{i}" for i in range(n_cols)) + " |"
        sep = "| " + " | ".join(["---"] * n_cols) + " |"
        row = (
            "| "
            + " | ".join(f"value_{i}_padding_text_here" for i in range(n_cols))
            + " |"
        )
        text = f"{header}\n{sep}\n{row}\n"
        table = _single_table(text)
        budget = ChunkBudget(target=40, overlap=0, min=1, hard_max=40)
        batches = batch_rows(table, budget, max_rows=500, estimator=EST)

        assert len(batches) > 1
        seen_columns: list[str] = []
        for batch in batches:
            assert batch.wide_split is True
            assert batch.row_count == 1
            lines = batch.text.splitlines()
            header_cells = _cells(lines[0])
            value_cells = _cells(lines[-1])
            # Exact header-subset <-> value-subset alignment: readable, not
            # a bare fragment with no header context.
            assert header_cells == list(batch.columns)
            assert len(header_cells) == len(value_cells)
            for h, v in zip(header_cells, value_cells, strict=True):
                idx_str = h.removeprefix("Col")
                assert v == f"value_{idx_str}_padding_text_here"
            seen_columns.extend(header_cells)
        # Every original column shows up exactly once across all slices.
        assert seen_columns == [f"Col{i}" for i in range(n_cols)]

    def test_normal_rows_are_never_column_sliced(self) -> None:
        text = "| A | B |\n| --- | --- |\n| 1 | 2 |\n"
        table = _single_table(text)
        budget = ChunkBudget(target=1000, overlap=0, min=1, hard_max=2000)
        batches = batch_rows(table, budget, max_rows=500, estimator=EST)
        assert len(batches) == 1
        assert batches[0].wide_split is False
