"""``HeaderRowHeuristic`` — decide whether a sheet's first row is a header.

Scope & functionality
---------------------
The first non-empty row of a sheet qualifies as a header when it is densely
populated (>=60% non-empty), mostly text (>=60% of the non-empty cells are
strings), and its "shape" visibly differs from the row that follows: a
straight type change in some column (numbers/dates/booleans where the
header had a string), or — for a data row that is *also* text-heavy — a
sharp drop in value length in some column (a short label token such as
``"ID"`` followed by a long sentence or a longer identifier such as
``"REQ-001"``). A title banner (e.g. ``"Quarterly report - FY26"`` alone in
a merged ``A1:D1``) never reaches the second or third check: its own
density already disqualifies it. When no row qualifies, the caller sets
``Table.header_rows = 0`` and records ``no_header_row:{sheet}``.
"""

from __future__ import annotations

from datetime import date, datetime
from datetime import time as dt_time
from typing import Any

_NON_EMPTY_THRESHOLD = 0.6
_STRING_THRESHOLD = 0.6
_SHORT_LABEL_MAX_CHARS = 20
_SHORT_VS_LONG_RATIO = 0.6


def _is_blank(value: Any) -> bool:  # noqa: ANN401 - raw cell values are untyped
    return value is None or value == ""


def _kind(value: Any) -> str:  # noqa: ANN401 - raw cell values are untyped
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, datetime | date | dt_time):
        return "date"
    return "string"


def _density_and_string_ratio(row: list[Any]) -> tuple[float, float, list[Any]]:
    """``(non_empty_ratio, string_ratio_of_non_empty, non_empty_values)``."""
    if not row:
        return 0.0, 0.0, []
    non_empty = [v for v in row if not _is_blank(v)]
    if not non_empty:
        return 0.0, 0.0, []
    string_ratio = sum(1 for v in non_empty if isinstance(v, str)) / len(non_empty)
    return len(non_empty) / len(row), string_ratio, non_empty


def _column_differs(header_value: Any, next_value: Any) -> bool:  # noqa: ANN401
    if _is_blank(header_value) or _is_blank(next_value):
        return False
    if _kind(header_value) != _kind(next_value):
        return True
    if not (isinstance(header_value, str) and isinstance(next_value, str)):
        return False
    return (
        len(header_value) <= _SHORT_LABEL_MAX_CHARS
        and len(header_value) < len(next_value) * _SHORT_VS_LONG_RATIO
    )


def _profile_differs(header_row: list[Any], next_row: list[Any]) -> bool:
    return any(_column_differs(h, n) for h, n in zip(header_row, next_row, strict=False))


class HeaderRowHeuristic:
    """Stateless classifier for whether a row reads as a sheet's header."""

    @staticmethod
    def looks_like_header(header_row: list[Any], next_row: list[Any] | None) -> bool:
        """True when *header_row* is dense/textual and, if given, differs from *next_row*."""
        density, string_ratio, non_empty = _density_and_string_ratio(header_row)
        if not non_empty:
            return False
        if density < _NON_EMPTY_THRESHOLD or string_ratio < _STRING_THRESHOLD:
            return False
        if next_row is None:
            return True
        _, _, next_non_empty = _density_and_string_ratio(next_row)
        if not next_non_empty:
            return True
        return _profile_differs(header_row, next_row)
