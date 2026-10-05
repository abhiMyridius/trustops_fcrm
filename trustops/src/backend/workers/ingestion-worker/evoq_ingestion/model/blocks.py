"""Frozen block dataclasses — the vocabulary every extractor speaks.

Scope & functionality
---------------------
A ``Document`` is an ordered sequence of these blocks. They are immutable
(``frozen=True, slots=True``) value objects with no rendering knowledge:
``GfmRenderer`` turns them into canonical Markdown, ``StructureBuilder``
counts them. Sequence fields accept any iterable and are stored as tuples.

* Text: ``Heading`` (``inferred``/``source`` record *how* a heading was
  found), ``Paragraph``, ``ListBlock``/``ListItem`` (nested, ordered with
  ``start``, task ``checked``), ``CodeBlock``, ``Quote`` (panels, expands,
  callouts — with an optional ``label``), ``HorizontalRule``.
* Tables: ``Table`` of ``Row`` of ``Cell`` with ``rowspan``/``colspan``,
  ``header`` flags, ``nested`` tables (flattened by the renderer), plus
  ``header_rows``, ``caption`` and ``name`` (sheet / table name).
* References: ``ImageRef``, ``Note`` (speaker notes, headers, footers, text
  boxes, comments, footnotes, endnotes, chart summaries).
* Unit markers: ``PageBreak``, ``SheetBreak``, ``SlideBreak`` — the only
  source of the exact ``--- Page N ---`` / ``--- Sheet: x ---`` /
  ``--- Slide N ---`` marker lines.
* ``Raw`` — Markdown passthrough for ``.md``/``.txt`` only.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TypeAlias

_INLINE_WS = str.maketrans({"\r": " ", "\n": " ", "\t": " "})


def _tuple(values: Iterable[object]) -> tuple[object, ...]:
    return tuple(values)


@dataclass(frozen=True, slots=True)
class Heading:
    """An ATX heading; ``inferred`` marks layout/numbering-based detection."""

    level: int
    text: str
    inferred: bool = False
    source: str = "explicit"  # explicit | style | outline | numbering | layout | bold_short

    def __post_init__(self) -> None:
        if not 1 <= self.level <= 6:
            raise ValueError(f"Heading.level must be 1..6, got {self.level}")


@dataclass(frozen=True, slots=True)
class Paragraph:
    """A run of prose; embedded newlines are kept as soft line breaks."""

    text: str


@dataclass(frozen=True, slots=True)
class ListItem:
    """One list entry with optional nested children and task/ordinal state."""

    text: str
    children: tuple[Block, ...] = ()
    checked: bool | None = None
    ordinal: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "children", _tuple(self.children))


@dataclass(frozen=True, slots=True)
class ListBlock:
    """A bullet or numbered list."""

    items: tuple[ListItem, ...]
    ordered: bool = False
    start: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", _tuple(self.items))

    def item_count(self) -> int:
        """Items including nested list items at any depth."""
        return sum(1 + _nested_item_count(item.children) for item in self.items)


@dataclass(frozen=True, slots=True)
class Cell:
    """A table cell; spans default to 1, ``nested`` holds tables found inside."""

    text: str = ""
    rowspan: int = 1
    colspan: int = 1
    header: bool = False
    nested: tuple[Table, ...] = ()

    def __post_init__(self) -> None:
        if self.rowspan < 1 or self.colspan < 1:
            raise ValueError("Cell spans must be >= 1")
        object.__setattr__(self, "nested", _tuple(self.nested))


@dataclass(frozen=True, slots=True)
class Row:
    """One table row."""

    cells: tuple[Cell, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "cells", _tuple(self.cells))

    def is_blank(self) -> bool:
        """True when every cell (including nested content) is empty."""
        return all(not c.text.strip() and not c.nested for c in self.cells)


@dataclass(frozen=True, slots=True)
class Table:
    """A table; ``header_rows == 0`` means the source had no header row."""

    rows: tuple[Row, ...]
    header_rows: int = 1
    caption: str | None = None
    name: str | None = None

    def __post_init__(self) -> None:
        if self.header_rows < 0:
            raise ValueError("Table.header_rows must be >= 0")
        object.__setattr__(self, "rows", _tuple(self.rows))

    def width(self) -> int:
        """Widest row measured in colspan-expanded columns."""
        return max((sum(c.colspan for c in row.cells) for row in self.rows), default=0)


@dataclass(frozen=True, slots=True)
class CodeBlock:
    """A fenced code block."""

    code: str
    lang: str = ""


@dataclass(frozen=True, slots=True)
class Quote:
    """A block quote / panel / expand; ``label`` becomes a bold first line."""

    children: tuple[Block, ...]
    label: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "children", _tuple(self.children))


@dataclass(frozen=True, slots=True)
class ImageRef:
    """An image reference (never the bytes)."""

    alt: str
    target: str


class NoteKind(StrEnum):
    """Where a ``Note`` came from."""

    speaker_notes = "speaker_notes"
    header = "header"
    footer = "footer"
    textbox = "textbox"
    comment = "comment"
    footnote = "footnote"
    endnote = "endnote"
    chart = "chart"


@dataclass(frozen=True, slots=True)
class Note:
    """Ancillary text (notes/headers/footers/…) kept out of the main prose flow."""

    kind: NoteKind
    text: str
    ref: str | None = None


@dataclass(frozen=True, slots=True)
class PageBreak:
    """Start of PDF page ``n`` (1-based)."""

    n: int


@dataclass(frozen=True, slots=True)
class SheetBreak:
    """Start of a spreadsheet sheet (or a CSV's single pseudo-sheet)."""

    name: str
    hidden: bool = False


@dataclass(frozen=True, slots=True)
class SlideBreak:
    """Start of slide ``n`` (1-based)."""

    n: int
    hidden: bool = False


@dataclass(frozen=True, slots=True)
class HorizontalRule:
    """A thematic break."""


@dataclass(frozen=True, slots=True)
class Raw:
    """Markdown passthrough — ``.md``/``.txt`` extractors only."""

    markdown: str = field(default="")


Block: TypeAlias = (
    Heading
    | Paragraph
    | ListBlock
    | Table
    | CodeBlock
    | Quote
    | ImageRef
    | Note
    | PageBreak
    | SheetBreak
    | SlideBreak
    | HorizontalRule
    | Raw
)


def inline_text(text: str) -> str:
    """Collapse newlines/tabs to single spaces (heading and cell text)."""
    return " ".join(text.translate(_INLINE_WS).split())


def _nested_item_count(children: tuple[Block, ...]) -> int:
    total = 0
    for child in children:
        if isinstance(child, ListBlock):
            total += child.item_count()
    return total
