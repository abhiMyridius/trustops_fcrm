"""``CsvExtractor`` — stdlib ``csv`` streamed over a detected encoding.

Scope & functionality
---------------------
Reuses ``core/encoding.py::EncodingDetector`` to pick the file's encoding
from its head bytes (BOM -> exact codec, UTF-16 alternation guess, strict
UTF-8, cp1252 fallback, or binary detection) — no encoding logic is
reimplemented here. A detected BOM codec is opened generically
(``utf-16``/``utf-32`` rather than the exact ``-le``/``-be`` the detector
reports) so Python's own BOM-aware incremental decoder consumes the marker
bytes instead of decoding them as a stray leading ``﻿`` character
(``utf-8-sig`` already strips its BOM either way).

The delimiter is sniffed on a 64KB, physical-line-aligned sample of the
*decoded* text (never raw bytes, so a cut never lands mid multi-byte
character); ``csv.reader`` then streams over that same sample chained with
the rest of the open handle, so quoted embedded newlines are parsed
correctly and the file is never read into memory in full. Row/column caps
are enforced by breaking the loop; ragged rows are left exactly as read —
``GfmRenderer`` already pads every row in a table to its widest row, so a
short row is padded and a long row simply gains unlabeled trailing columns.
"""

from __future__ import annotations

import csv
import itertools
from pathlib import Path
from typing import IO, Any

from evoq_ingestion.core.codes import WarningCode
from evoq_ingestion.core.encoding import EncodingDetector, EncodingResult
from evoq_ingestion.core.limits import Deadline, Limits
from evoq_ingestion.extractors.base import BaseExtractor, BinaryContent, EngineInfo
from evoq_ingestion.extractors.gate import FileFacts
from evoq_ingestion.model.blocks import Cell, Row, SheetBreak, Table
from evoq_ingestion.model.document import Document, SheetInfo

_SAMPLE_BYTES = 64 * 1024
_ROW_CHECK_INTERVAL = 5000
_SNIFF_DELIMITERS = ",;\t|"
# The detector reports the exact endianness it sniffed a BOM for, but a
# real BOM at the front of the file should be consumed by Python's generic
# (BOM-sniffing) codec name, not decoded as a literal character.
_BOM_GENERIC_CODEC = {
    "utf-16-le": "utf-16",
    "utf-16-be": "utf-16",
    "utf-32-le": "utf-32",
    "utf-32-be": "utf-32",
}


class CsvExtractor(BaseExtractor):
    """Stdlib ``csv`` engine: one ``--- Sheet: {stem} ---`` marker + one table."""

    info = EngineInfo("csv", "2")
    supports = frozenset({".csv"})

    def _parse(self, path: Path, facts: FileFacts, deadline: Deadline) -> Document:
        """Detect encoding, sniff the dialect, then stream rows into one ``Table``."""
        doc = Document(title=path.stem)
        limits = self.options.limits
        sheet_name = path.stem

        encoding_result = _detect_encoding(path)
        if encoding_result.is_binary:
            raise BinaryContent(f"{path.name} contains binary data")
        for warning in encoding_result.warnings:
            doc.add_warning(warning)
        codec = _BOM_GENERIC_CODEC.get(encoding_result.encoding, encoding_result.encoding)

        with open(path, encoding=codec, errors="replace", newline="") as handle:
            rows, rows_truncated, cols_truncated = self._stream_rows(
                doc, handle, limits, deadline
            )

        doc.add(SheetBreak(name=sheet_name))
        if not rows:
            doc.units.sheets.append(SheetInfo(name=sheet_name, rows=0, cols=0, hidden=False))
            return doc

        if rows_truncated:
            doc.add_warning(WarningCode.rows_truncated, str(limits.max_csv_rows))
        if cols_truncated:
            doc.add_warning(WarningCode.cols_truncated, str(limits.max_cols_per_sheet))

        doc.add(_build_table(rows, sheet_name))
        doc.units.sheets.append(
            SheetInfo(
                name=sheet_name,
                rows=max(len(rows) - 1, 0),
                cols=max((len(r) for r in rows), default=0),
                hidden=False,
            )
        )
        return doc

    @staticmethod
    def _stream_rows(
        doc: Document, handle: IO[str], limits: Limits, deadline: Deadline
    ) -> tuple[list[list[str]], bool, bool]:
        sample_lines, dialect, sniff_failed = _sniff_dialect(handle)
        if sniff_failed:
            doc.add_warning(WarningCode.sniff_fallback)
        reader = csv.reader(itertools.chain(sample_lines, handle), dialect)
        max_rows = limits.max_csv_rows
        max_cols = limits.max_cols_per_sheet
        rows: list[list[str]] = []
        rows_truncated = False
        cols_truncated = False
        for row_idx, row in enumerate(reader, start=1):
            if row_idx > max_rows:
                rows_truncated = True
                break
            if row_idx % _ROW_CHECK_INTERVAL == 0:
                deadline.check()
            if len(row) > max_cols:
                row = row[:max_cols]
                cols_truncated = True
            rows.append(row)
        return rows, rows_truncated, cols_truncated


def _detect_encoding(path: Path) -> EncodingResult:
    with open(path, "rb") as handle:  # noqa: PTH123 - one bounded head read
        head = handle.read(_SAMPLE_BYTES)
    return EncodingDetector(sample_bytes=_SAMPLE_BYTES).detect(head)


def _sniff_dialect(handle: IO[str]) -> tuple[list[str], Any, bool]:  # noqa: ANN401 - csv.Dialect duck type
    """A line-bounded 64KB sample, the sniffed dialect, and whether it fell back."""
    sample_lines: list[str] = []
    sample_len = 0
    while sample_len < _SAMPLE_BYTES:
        line = handle.readline()
        if not line:
            break
        sample_lines.append(line)
        sample_len += len(line)
    sample_text = "".join(sample_lines)
    try:
        return sample_lines, csv.Sniffer().sniff(sample_text, delimiters=_SNIFF_DELIMITERS), False
    except csv.Error:
        return sample_lines, csv.excel, True


def _build_table(rows: list[list[str]], sheet_name: str) -> Table:
    table_rows = [
        Row(cells=tuple(Cell(text=text, header=(i == 0)) for text in row))
        for i, row in enumerate(rows)
    ]
    return Table(rows=tuple(table_rows), header_rows=1, name=sheet_name)
