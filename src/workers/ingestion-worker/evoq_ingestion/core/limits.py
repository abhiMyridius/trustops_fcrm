"""Hard limits and the cooperative ``Deadline`` every extractor honours.

Scope & functionality
---------------------
``Limits`` is the one place the platform's caps live (file bytes, PDF pages,
slides, rows/columns per sheet, CSV rows, body characters, merged-range scan
bytes, per-file timeout; the upstream MCP batch timeout was pruned on
clone). Policy, shared by every engine:

* whole-file refusals (bytes, pages, slides, encrypted, corrupt, extension
  mismatch, binary content, scanned PDF) -> the file becomes a ``FAILED``
  row carrying a ``FailureCode``;
* streaming truncations (rows, columns, body characters) -> the file stays
  ``COMPLETE`` and a ``WarningCode`` records what was cut.

``Deadline`` is a monotonic budget an extractor checks between units
(pages / sheets / slides) so a slow parse stops cooperatively instead of
being killed mid-write; ``DeadlineExceeded`` is translated to
``ExtractionTimeout`` by ``extractors.base.BaseExtractor``.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

MIB = 1024 * 1024


class DeadlineExceeded(TimeoutError):
    """Raised by ``Deadline.check()`` once the budget is spent."""


@dataclass(frozen=True, slots=True)
class Limits:
    """Platform caps. Whole-file caps refuse the file; streaming caps truncate it."""

    max_file_bytes: int = 200 * MIB
    max_pdf_pages: int = 2000
    max_slides: int = 100
    max_rows_per_sheet: int = 200_000
    max_csv_rows: int = 200_000
    max_cols_per_sheet: int = 256
    max_body_chars: int = 20_000_000
    xlsx_merge_scan_max_bytes: int = 64 * MIB
    per_file_timeout_seconds: float = 120.0

    def __post_init__(self) -> None:
        for name in (
            "max_file_bytes",
            "max_pdf_pages",
            "max_slides",
            "max_rows_per_sheet",
            "max_csv_rows",
            "max_cols_per_sheet",
            "max_body_chars",
            "xlsx_merge_scan_max_bytes",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"Limits.{name} must be positive")
        if self.per_file_timeout_seconds <= 0:
            raise ValueError("Limits timeouts must be positive")

    def deadline(self, seconds: float | None = None) -> Deadline:
        """A fresh ``Deadline`` for one file (defaults to ``per_file_timeout_seconds``)."""
        return Deadline(self.per_file_timeout_seconds if seconds is None else seconds)


class Deadline:
    """A monotonic time budget; ``None`` seconds means unlimited."""

    __slots__ = ("_clock", "_ends_at", "_seconds")

    def __init__(
        self,
        seconds: float | None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Start a deadline of ``seconds`` from now (``None`` = unlimited)."""
        if seconds is not None and seconds < 0:
            raise ValueError("Deadline seconds must be >= 0 or None")
        self._seconds = seconds
        self._clock = clock
        self._ends_at = None if seconds is None else clock() + seconds

    @classmethod
    def unlimited(cls) -> Deadline:
        """A deadline that never expires."""
        return cls(None)

    @property
    def seconds(self) -> float | None:
        """The original budget in seconds (``None`` = unlimited)."""
        return self._seconds

    def remaining(self) -> float | None:
        """Seconds left (never negative), or ``None`` when unlimited."""
        if self._ends_at is None:
            return None
        return max(0.0, self._ends_at - self._clock())

    @property
    def expired(self) -> bool:
        """True once the budget is spent."""
        return self._ends_at is not None and self._clock() >= self._ends_at

    def check(self) -> None:
        """Raise ``DeadlineExceeded`` when expired; call between units of work."""
        if self.expired:
            raise DeadlineExceeded(f"deadline of {self._seconds}s exceeded")

    def __repr__(self) -> str:
        return f"Deadline(seconds={self._seconds!r}, remaining={self.remaining()!r})"
