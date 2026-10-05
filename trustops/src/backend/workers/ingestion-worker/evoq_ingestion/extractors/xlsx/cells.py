"""``CellFormatter`` — canonical text for one XLSX cell value.

Scope & functionality
---------------------
Turns an ``openpyxl`` cell value (or a formula's cached value / formula-text
fallback) into the exact string ``Cell.text`` should carry: ISO dates/times
(day-only for a midnight ``datetime``, full ``HH:MM:SS`` otherwise),
``TRUE``/``FALSE`` for booleans, ``.15g`` for floats (the raw numeric value —
never a thousands separator, never a percent sign; number *display* format
is not display), and ``str(value)`` for everything else (ints, formula
text, already-decoded strings).
"""

from __future__ import annotations

from datetime import date, datetime
from datetime import time as dt_time
from typing import Any

_MIDNIGHT = dt_time(0, 0, 0)


class CellFormatter:
    """Stateless conversion from a raw cell value to its canonical text."""

    @staticmethod
    def format(value: Any) -> str:  # noqa: ANN401 - openpyxl cell values are untyped
        """Render *value* as the cell's canonical text; ``None`` becomes ``""``."""
        if value is None:
            return ""
        if isinstance(value, bool):
            return "TRUE" if value else "FALSE"
        if isinstance(value, datetime):
            if value.time() == _MIDNIGHT and value.microsecond == 0:
                return value.date().isoformat()
            return value.isoformat(sep=" ", timespec="seconds")
        if isinstance(value, date | dt_time):
            return value.isoformat()
        if isinstance(value, float):
            return format(value, ".15g")
        return str(value)
