r"""Visual line extraction, hyphenation repair, ligatures and paragraph assembly.

Scope & functionality
---------------------
``extract_lines(page)`` turns a pdfplumber page (or a cropped/filtered view
of one) into ``PdfLine`` records using ``Page.extract_text_lines()`` — the
single place that reads per-line font size (rounded to the nearest half
point) and boldness from the underlying chars, in reading order.

``LineAssembler`` groups a run of non-heading ``PdfLine``\ s into
``Paragraph`` blocks: a vertical gap greater than 1.5x the run's median line
height starts a new paragraph; joining two lines repairs end-of-line
hyphenation (``word-\nend`` -> ``wordend`` when the next line starts
lowercase) and maps the common ligatures (``ﬁﬂﬀﬃﬄﬅﬆ``) before NFC
normalising the result.

``normalize_repeated_line`` (digit runs -> ``#``) is the shared key used by
``repeated.RepeatedLineDetector`` so ``"Page 3 of 79"`` and ``"Page 71 of
79"`` compare equal.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from statistics import median
from typing import TYPE_CHECKING, Any

from evoq_ingestion.model.blocks import Paragraph

if TYPE_CHECKING:
    from evoq_ingestion.model.blocks import Block

_LIGATURES: dict[str, str] = {
    "ﬀ": "ff",
    "ﬁ": "fi",
    "ﬂ": "fl",
    "ﬃ": "ffi",
    "ﬄ": "ffl",
    "ﬅ": "st",
    "ﬆ": "st",
}
_DIGIT_RUN_RE = re.compile(r"\d+")
_HYPHEN_END_RE = re.compile(r"(?<=[A-Za-z])-$")


@dataclass(frozen=True, slots=True)
class PdfLine:
    """One visual line of PDF text with the font facts headings need."""

    text: str
    top: float
    bottom: float
    x0: float
    x1: float
    size: float
    bold: bool


def extract_lines(page: Any) -> list[PdfLine]:  # noqa: ANN401 - pdfplumber has no public Page type
    """Visual text lines of *page* in reading order (top to bottom)."""
    out: list[PdfLine] = []
    for line in page.extract_text_lines(strip=True) or []:
        text = (line.get("text") or "").strip()
        if not text:
            continue
        chars = [c for c in line.get("chars", []) if (c.get("text") or "").strip()]
        size = _mode(round(c.get("size", 0.0) * 2) / 2 for c in chars) if chars else 0.0
        bold = bool(chars) and sum(1 for c in chars if _is_bold(c)) >= len(chars) / 2
        x0 = min((c["x0"] for c in chars), default=line.get("x0", 0.0))
        x1 = max((c["x1"] for c in chars), default=line.get("x1", 0.0))
        out.append(
            PdfLine(
                text=text,
                top=float(line["top"]),
                bottom=float(line["bottom"]),
                x0=float(x0),
                x1=float(x1),
                size=float(size),
                bold=bold,
            )
        )
    return out


def _is_bold(char: dict[str, Any]) -> bool:
    fontname = (char.get("fontname") or "").lower()
    return "bold" in fontname


def _mode(values: Any) -> float:  # noqa: ANN401 - iterable of float
    counts: dict[float, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    if not counts:
        return 0.0
    return max(counts.items(), key=lambda kv: kv[1])[0]


def normalize_repeated_line(text: str) -> str:
    """Collapse digit runs to ``#`` so ``"Page 3 of 79"`` matches every page."""
    return _DIGIT_RUN_RE.sub("#", text.strip())


def repair_ligatures(text: str) -> str:
    """Map common ligatures to their plain letters, then NFC-normalise."""
    for ligature, replacement in _LIGATURES.items():
        if ligature in text:
            text = text.replace(ligature, replacement)
    return unicodedata.normalize("NFC", text)


def join_hyphenated(first: str, second: str) -> str:
    """Join two lines, repairing an end-of-line hyphen when it looks like a word break."""
    second = second.lstrip()
    if not first:
        return second
    if not second:
        return first
    if _HYPHEN_END_RE.search(first) and second[:1].islower():
        return first[:-1] + second
    return f"{first} {second}"


class LineAssembler:
    r"""Group a run of ``PdfLine``\ s into paragraphs by vertical gap."""

    def __init__(self, *, gap_ratio: float = 1.5) -> None:
        """Construct an assembler; a gap wider than ``gap_ratio`` x median line height splits."""
        self._gap_ratio = gap_ratio

    def assemble(self, lines: list[PdfLine]) -> list[Block]:
        """Return ``Paragraph`` blocks; empty input yields no blocks."""
        if not lines:
            return []
        heights = [max(line.bottom - line.top, 1.0) for line in lines]
        threshold = median(heights) * self._gap_ratio
        groups: list[list[PdfLine]] = [[lines[0]]]
        for previous, current in zip(lines, lines[1:], strict=False):
            gap = current.top - previous.bottom
            if gap > threshold:
                groups.append([current])
            else:
                groups[-1].append(current)
        return [Paragraph(text=self._join(group)) for group in groups if group]

    @staticmethod
    def _join(group: list[PdfLine]) -> str:
        text = group[0].text
        for line in group[1:]:
            text = join_hyphenated(text, line.text)
        return repair_ligatures(text)
