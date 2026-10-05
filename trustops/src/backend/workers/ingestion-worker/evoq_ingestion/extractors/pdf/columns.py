"""``ColumnDetector`` — a persistent x-gap that splits prose into columns.

Scope & functionality
---------------------
Projects every character's x-range onto the horizontal axis (ignoring y)
and merges overlapping/touching ranges into a "text shadow". A gap in that
shadow wide enough (``min_gap_width``) is only treated as a real column
separator when there is text on *both* sides spanning at least
``min_gap_height_fraction`` of the page height — this is what tells a
genuine two-column layout apart from an incidental margin (a pull-quote,
an indented block) that happens to leave a narrow horizontal band empty.

``detect`` returns the column x-ranges left to right, or ``None`` for a
single-column (or too-sparse) page.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ColumnDetector:
    """Detect a persistent vertical gap that splits a page into columns."""

    min_gap_width: float = 8.0
    min_gap_height_fraction: float = 0.6
    merge_tolerance: float = 1.0
    min_chars: int = 20

    def detect(self, page: Any) -> list[tuple[float, float]] | None:  # noqa: ANN401
        """Column ``(x0, x1)`` ranges left to right, or ``None`` for one column."""
        chars = [c for c in page.chars if (c.get("text") or "").strip()]
        if len(chars) < self.min_chars:
            return None
        merged = self._merged_intervals(chars)
        if len(merged) < 2:
            return None
        page_height = page.height
        boundaries = [merged[0][0]]
        for (_, end), (start, _) in zip(merged, merged[1:], strict=False):
            if not self._is_real_gap(chars, end, start, page_height):
                continue
            boundaries.append(end)
            boundaries.append(start)
        boundaries.append(merged[-1][1])
        if len(boundaries) < 4:
            return None
        return [(boundaries[i], boundaries[i + 1]) for i in range(0, len(boundaries), 2)]

    def _merged_intervals(self, chars: list[dict[str, Any]]) -> list[list[float]]:
        intervals = sorted((c["x0"], c["x1"]) for c in chars)
        merged: list[list[float]] = []
        for start, end in intervals:
            if merged and start <= merged[-1][1] + self.merge_tolerance:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        return merged

    def _is_real_gap(
        self, chars: list[dict[str, Any]], gap_start: float, gap_end: float, page_height: float
    ) -> bool:
        if gap_end - gap_start < self.min_gap_width:
            return False
        left = [c for c in chars if c["x1"] <= gap_start + self.merge_tolerance]
        right = [c for c in chars if c["x0"] >= gap_end - self.merge_tolerance]
        if not left or not right:
            return False
        left_span = float(max(c["bottom"] for c in left)) - float(min(c["top"] for c in left))
        right_span = float(max(c["bottom"] for c in right)) - float(min(c["top"] for c in right))
        threshold = self.min_gap_height_fraction * page_height
        return bool(left_span >= threshold and right_span >= threshold)
