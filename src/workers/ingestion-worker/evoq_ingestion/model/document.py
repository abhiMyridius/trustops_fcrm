"""``Document`` — what every extractor returns before rendering.

Scope & functionality
---------------------
A mutable container an extractor fills while parsing: the ordered ``blocks``
(see ``blocks.py``), a ``title``, free-form ``metadata`` (front matter,
author, …), the ``warnings`` codes it recorded, attachment references and
``Units`` — the authoritative unit facts (page/slide counts, per-sheet
shapes, image count) plus per-unit failures (``UnitFailure``: one page,
sheet, slide or table that could not be parsed while the rest of the file
was). ``StructureBuilder`` reads ``units`` so ``structure.page_count`` comes
from the parser, never from counting marker lines.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from evoq_ingestion.core.codes import WarningCode, with_detail
from evoq_ingestion.model.blocks import (
    Block,
    CodeBlock,
    Heading,
    HorizontalRule,
    ImageRef,
    ListBlock,
    Note,
    PageBreak,
    Paragraph,
    Quote,
    Raw,
    SheetBreak,
    SlideBreak,
    Table,
)


@dataclass(frozen=True, slots=True)
class SheetInfo:
    """Per-sheet facts for ``structure.sheets[]``."""

    name: str
    rows: int
    cols: int
    hidden: bool = False

    def to_dict(self) -> dict[str, str | int | bool]:
        """Contract shape ``{name, rows, cols, hidden}``."""
        return {"name": self.name, "rows": self.rows, "cols": self.cols, "hidden": self.hidden}


@dataclass(frozen=True, slots=True)
class UnitFailure:
    """One page/sheet/slide/table that failed while the rest of the file succeeded."""

    kind: str  # page | sheet | slide | table
    ref: str
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        """Serialisable form for ``extraction.unit_failures[]``."""
        return {"kind": self.kind, "ref": self.ref, "code": self.code, "message": self.message}

    def warning(self) -> str:
        """The ``unit_failed:{kind}:{ref}`` warning string."""
        return with_detail(WarningCode.unit_failed, f"{self.kind}:{self.ref}")


@dataclass(frozen=True, slots=True)
class AttachmentRef:
    """A referenced attachment/image (contract ``attachments[]`` shape)."""

    id: str | None = None
    title: str | None = None
    media_type: str | None = None
    size: int | None = None
    url: str | None = None

    def to_dict(self) -> dict[str, str | int | None]:
        """Contract shape ``{id, title, media_type, size, url}``."""
        return {
            "id": self.id,
            "title": self.title,
            "media_type": self.media_type,
            "size": self.size,
            "url": self.url,
        }


@dataclass(slots=True)
class Units:
    """Authoritative unit facts from the parser (not derived from text)."""

    page_count: int | None = None
    slide_count: int | None = None
    sheets: list[SheetInfo] = field(default_factory=list)
    image_count: int = 0
    failed: list[UnitFailure] = field(default_factory=list)

    @property
    def sheet_count(self) -> int | None:
        """Number of sheets, or ``None`` when the format has none."""
        return len(self.sheets) if self.sheets else None


@dataclass(slots=True)
class Document:
    """The extractor's output: blocks + facts. Rendering happens elsewhere."""

    blocks: list[Block] = field(default_factory=list)
    title: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    attachments: list[AttachmentRef] = field(default_factory=list)
    units: Units = field(default_factory=Units)

    # ------------------------------------------------------------- mutation
    def add(self, *blocks: Block) -> None:
        """Append blocks in order."""
        self.blocks.extend(blocks)

    def add_warning(self, code: WarningCode | str, detail: object = None) -> str:
        """Record a warning (``code`` or ``code:detail``) once; returns the string."""
        base = str(code)
        warning = base if detail is None or detail == "" else f"{base}:{detail}"
        if warning not in self.warnings:
            self.warnings.append(warning)
        return warning

    def fail_unit(self, kind: str, ref: object, code: WarningCode | str, message: str) -> None:
        """Record a per-unit failure and its ``unit_failed:{kind}:{ref}`` warning."""
        failure = UnitFailure(kind=kind, ref=str(ref), code=str(code), message=message)
        self.units.failed.append(failure)
        self.add_warning(failure.warning())

    # ---------------------------------------------------------------- queries
    def iter_blocks(self) -> Iterator[Block]:
        """Depth-first over top-level blocks, quote children and list-item children."""
        yield from _walk(self.blocks)

    def headings(self) -> list[Heading]:
        """Every heading in document order."""
        return [b for b in self.iter_blocks() if isinstance(b, Heading)]

    def tables(self) -> list[Table]:
        """Every (non-nested) table in document order."""
        return [b for b in self.iter_blocks() if isinstance(b, Table)]

    def image_refs(self) -> list[ImageRef]:
        """Every image reference."""
        return [b for b in self.iter_blocks() if isinstance(b, ImageRef)]

    def page_breaks(self) -> list[PageBreak]:
        """Every page marker."""
        return [b for b in self.blocks if isinstance(b, PageBreak)]

    def sheet_breaks(self) -> list[SheetBreak]:
        """Every sheet marker."""
        return [b for b in self.blocks if isinstance(b, SheetBreak)]

    def slide_breaks(self) -> list[SlideBreak]:
        """Every slide marker."""
        return [b for b in self.blocks if isinstance(b, SlideBreak)]

    def is_empty(self) -> bool:
        """True when no block carries any text/content (markers alone do not count)."""
        return not any(_has_content(b) for b in self.iter_blocks())


def _walk(blocks: list[Block] | tuple[Block, ...]) -> Iterator[Block]:
    for block in blocks:
        yield block
        if isinstance(block, Quote):
            yield from _walk(block.children)
        elif isinstance(block, ListBlock):
            for item in block.items:
                yield from _walk(item.children)


def _has_content(block: Block) -> bool:
    match block:
        case PageBreak() | SheetBreak() | SlideBreak() | HorizontalRule():
            return False
        case Table():
            return any(not row.is_blank() for row in block.rows)
        case ListBlock():
            return any(item.text.strip() or item.children for item in block.items)
        case Quote():
            return bool(block.label) or any(_has_content(c) for c in block.children)
        case ImageRef():
            return True
        case Heading() | Paragraph() | Note():
            return bool(block.text.strip())
        case CodeBlock():
            return bool(block.code.strip())
        case Raw():
            return bool(block.markdown.strip())
    return False
