"""PPTX extractor — ``python-pptx``.

Scope & functionality
---------------------
One ``SlideBreak(n)`` marker per slide (1-based, ``n = len(prs.slides)`` —
the real count, never inferred by counting marker text). Per slide: the
title placeholder becomes an ``H2`` heading, other text frames become
``Paragraph``/``ListBlock`` content (paragraph ``level`` 0-8 drives list
nesting), ``GraphicFrame`` tables become a real ``Table`` block (merged
cells carry ``rowspan``/``colspan`` on the origin cell; spanned-over cells
are skipped, never duplicated — tables were previously dropped entirely),
charts become a best-effort summary ``Note``, pictures become ``ImageRef``,
and speaker notes become a ``Note`` when ``options.pptx_include_notes``.

**Grouped shapes are recursed** (``_iter_shapes`` walks into
``GroupShape.shapes``) so text nested inside a diagram group — including a
group nested inside another group — is no longer silently dropped, which is
the key fidelity fix over the pre-model extractor. Shapes are visited in
z-order; ``ExtractionOptions`` has no ``pptx_visual_order`` knob today, so
no ``(top, left)`` re-sort is applied (see plan §1.2 — "else just use
z-order and note it").

Hidden slides (``slide._element.get("show") == "0"``) still have their
content extracted — only flagged (``hidden_slide:{n}``) — matching the
XLSX extractor's ``hidden_sheet`` convention of always extracting.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.exc import PackageNotFoundError
from pptx.shapes.base import BaseShape

from evoq_ingestion.core.codes import FailureCode, WarningCode
from evoq_ingestion.core.limits import Deadline
from evoq_ingestion.extractors.base import (
    BaseExtractor,
    CorruptFile,
    EngineInfo,
    ExtractionError,
    LimitExceeded,
)
from evoq_ingestion.extractors.gate import FileFacts
from evoq_ingestion.model.blocks import (
    Cell,
    Heading,
    ImageRef,
    ListBlock,
    ListItem,
    Note,
    NoteKind,
    Paragraph,
    Row,
    SlideBreak,
    Table,
)
from evoq_ingestion.model.document import Document

__all__ = ["PptxExtractor"]


class PptxExtractor(BaseExtractor):
    """``python-pptx``-based PPTX extractor over the ``Document`` model."""

    info = EngineInfo("python-pptx", "2")
    supports = frozenset({".pptx"})

    def classify_error(self, exc: Exception, path: Path) -> ExtractionError:
        """Map a broken/non-zip container to ``CorruptFile``; else the base default."""
        if isinstance(exc, PackageNotFoundError):
            return CorruptFile(f"{path.name}: not a readable PPTX container", detail=str(exc))
        return super().classify_error(exc, path)

    def _parse(self, path: Path, facts: FileFacts, deadline: Deadline) -> Document:
        """Parse *path* into a ``Document``; one bad slide degrades, not the whole file."""
        prs = Presentation(str(path))
        limits = self.options.limits
        slide_total = len(prs.slides)
        if slide_total > limits.max_slides:
            raise LimitExceeded(
                f"{path.name} has {slide_total} slides (limit {limits.max_slides})",
                code=FailureCode.slide_limit_exceeded,
                detail=str(slide_total),
            )

        doc = Document()
        image_count = 0
        for idx, slide in enumerate(prs.slides, start=1):
            deadline.check()
            hidden = slide._element.get("show") == "0"  # noqa: SLF001 - only way to read it
            doc.add(SlideBreak(idx, hidden=hidden))
            if hidden:
                doc.add_warning(WarningCode.hidden_slide, idx)
            try:
                image_count += self._parse_slide(
                    doc, slide, idx, include_notes=self.options.pptx_include_notes
                )
            except Exception as exc:  # noqa: BLE001 - per-unit degradation boundary
                doc.fail_unit("slide", idx, FailureCode.extraction_failed, str(exc))

        if slide_total and len(doc.units.failed) == slide_total:
            raise CorruptFile(f"{path.name}: every slide failed to parse")

        doc.units.slide_count = slide_total
        doc.units.image_count = image_count
        return doc

    # ------------------------------------------------------------- per slide
    def _parse_slide(self, doc: Document, slide: object, idx: int, *, include_notes: bool) -> int:
        """Fill *doc* with one slide's blocks; returns the picture count found."""
        title = slide.shapes.title  # type: ignore[attr-defined]
        title_id = title.shape_id if title is not None else None
        title_text = title.text.strip() if title is not None else ""
        if title_text:
            doc.add(Heading(2, text=title_text))

        image_count = 0
        for shape in _iter_shapes(slide.shapes):  # type: ignore[attr-defined]
            if title_id is not None and shape.shape_id == title_id:
                continue
            if shape.has_table:
                doc.add(_table_block(shape))
            elif shape.has_chart:
                doc.add(_chart_note(shape))
                doc.add_warning(WarningCode.chart_summarised, idx)
            elif _is_picture(shape):
                doc.add(_picture_ref(shape))
                image_count += 1
            elif shape.has_text_frame:
                doc.add(*_text_frame_blocks(shape))

        if include_notes and slide.has_notes_slide:  # type: ignore[attr-defined]
            notes_frame = slide.notes_slide.notes_text_frame  # type: ignore[attr-defined]
            notes_text = (notes_frame.text or "").strip()
            if notes_text:
                doc.add(Note(kind=NoteKind.speaker_notes, text=notes_text))
        return image_count


# ------------------------------------------------------------------- shapes
def _iter_shapes(shapes: Iterable[BaseShape]) -> Iterator[BaseShape]:
    """Depth-first z-order walk that recurses into every level of group shapes."""
    for shape in shapes:
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            yield from _iter_shapes(shape.shapes)  # type: ignore[attr-defined]
        else:
            yield shape


def _is_picture(shape: BaseShape) -> bool:
    return shape.shape_type == MSO_SHAPE_TYPE.PICTURE


# -------------------------------------------------------------------- tables
def _table_block(shape: BaseShape) -> Table:
    """Build a ``Table`` from a ``GraphicFrame``; spanned cells are skipped, not duplicated."""
    table = shape.table  # type: ignore[attr-defined]
    rows: list[Row] = []
    for r_idx, row in enumerate(table.rows):
        cells: list[Cell] = []
        for cell in row.cells:
            if cell.is_spanned:
                continue
            rowspan = cell.span_height if cell.is_merge_origin else 1
            colspan = cell.span_width if cell.is_merge_origin else 1
            cells.append(
                Cell(
                    text=(cell.text or "").strip(),
                    rowspan=max(1, rowspan),
                    colspan=max(1, colspan),
                    header=(r_idx == 0),
                )
            )
        if cells:
            rows.append(Row(cells=tuple(cells)))
    return Table(rows=tuple(rows), header_rows=1 if rows else 0)


# -------------------------------------------------------------------- charts
def _chart_note(shape: BaseShape) -> Note:
    """Best-effort ``Note(kind=chart)`` summary: title + series names."""
    chart = shape.chart  # type: ignore[attr-defined]
    title = ""
    if chart.has_title:
        title = (chart.chart_title.text_frame.text or "").strip()
    title = title or "Chart"
    series_names: list[str] = []
    try:
        if chart.plots:
            series_names = [s.name for s in chart.plots[0].series if s.name]
    except Exception:  # noqa: BLE001 - best-effort summary, never fails the slide
        series_names = []
    text = f"{title}: " + ", ".join(series_names) if series_names else title
    return Note(kind=NoteKind.chart, text=text)


# ------------------------------------------------------------------ pictures
def _picture_ref(shape: BaseShape) -> ImageRef:
    """``ImageRef`` for a picture shape; falls back to the shape id when unembedded."""
    alt = shape.name or "image"
    filename: str | None = None
    try:
        filename = shape.image.filename  # type: ignore[attr-defined]
    except ValueError:
        filename = None
    return ImageRef(alt=alt, target=f"attachment:{filename or shape.shape_id}")


# --------------------------------------------------------------- text frames
def _text_frame_blocks(shape: BaseShape) -> tuple[Paragraph | ListBlock, ...]:
    """Paragraphs of a text frame as a plain ``Paragraph`` or a level-nested ``ListBlock``."""
    frame = shape.text_frame  # type: ignore[attr-defined]
    pairs = [(p.level, p.text.strip()) for p in frame.paragraphs if p.text.strip()]
    if not pairs:
        return ()
    looks_bulleted = len(pairs) > 1 or pairs[0][0] > 0
    if not looks_bulleted:
        return (Paragraph(text=pairs[0][1]),)
    items = _nest_items(pairs, 0, _Cursor())
    return (ListBlock(items=tuple(items)),) if items else ()


class _Cursor:
    """Mutable index into a flat ``(level, text)`` sequence for ``_nest_items``."""

    __slots__ = ("i",)

    def __init__(self) -> None:
        self.i = 0


def _nest_items(pairs: list[tuple[int, str]], min_level: int, cursor: _Cursor) -> list[ListItem]:
    """Build nested ``ListItem`` objects from a flat ``(level, text)`` sequence."""
    items: list[ListItem] = []
    while cursor.i < len(pairs) and pairs[cursor.i][0] >= min_level:
        level, text = pairs[cursor.i]
        cursor.i += 1
        children = _nest_items(pairs, level + 1, cursor)
        nested = (ListBlock(items=tuple(children)),) if children else ()
        items.append(ListItem(text=text, children=nested))
    return items
