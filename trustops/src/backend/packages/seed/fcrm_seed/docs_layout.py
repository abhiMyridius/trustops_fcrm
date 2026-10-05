"""Block model, layout rules and PDF renderer for the synthetic demo documents.

:mod:`fcrm_seed.gen_docs` renders the documents from the content in
:mod:`fcrm_seed.docs_content`; this module is the engine underneath: explicit
pages of explicit blocks, drawn with reportlab, plus the prediction of the page
and line of every fact (ADR-29).

All data is synthetic. Every page carries a running notice saying so; the bank,
the partner, the auditor and every named person are invented, and the figures
are the walkthrough-v2 seed's own.

**Why the layout is rigid.** The H3 extractor turns a PDF into Markdown and
cites ``page:line``, where a line is a line of that Markdown, not a line of the
page. Blocks (headings, paragraphs, tables) are separated by one blank line and
a table row is one line each, so the line of any fact is fully determined by
the *block structure* of its page. The renderer therefore works from explicit
pages of explicit blocks (never flowing text), spaces blocks far enough apart
that the extractor keeps them separate, keeps every table cell on one line,
refuses a line that starts with a digit (the extractor would read it as a
numbered heading), and predicts the page and line of every fact from the
structure. The unit test then proves the prediction against the real
extractor. Consequences worth knowing:

* a page's lines 2, 4, 6 ... are blank separators, and in a table line 2 is the
  ``| --- |`` rule, so the walkthrough's own "case p.1 l.2" citation cannot be
  reproduced; where a seed citation can be honoured (p.1 l.3, p.1 l.9, p.2 l.8,
  p.4 l.12) it is, and ``seed_source`` records the seed's wording beside the
  real anchor;
* reportlab's standard fonts cannot carry the arrow in "US → Mexico" through
  extraction (the extractor decodes the Symbol glyph as "fi"), and no
  redistributable font with the glyph ships with the dependencies, so the
  documents say "US to Mexico".

Output is deterministic (fixed PDF dates and id, uncompressed streams), so
re-running leaves a clean diff.
"""

from __future__ import annotations

import io
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final, Optional, Union

from pydantic import BaseModel, ConfigDict
from reportlab.lib.pagesizes import letter
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas

logger = logging.getLogger(__name__)

PAGE_WIDTH: Final = float(letter[0])
PAGE_HEIGHT: Final = float(letter[1])
MARGIN_X: Final = 54.0
BODY_WIDTH: Final = PAGE_WIDTH - 2 * MARGIN_X

BODY_FONT: Final = "Helvetica"
BODY_SIZE: Final = 10.0
LEADING: Final = 13.0
HEADING_FONT: Final = "Helvetica-Bold"
HEADING_SIZE: Final = 11.0
TABLE_FONT: Final = "Helvetica"
TABLE_BOLD_FONT: Final = "Helvetica-Bold"
TABLE_SIZE: Final = 9.0
ROW_HEIGHT: Final = 18.0
CELL_PAD_X: Final = 5.0
NOTICE_FONT: Final = "Helvetica"
NOTICE_SIZE: Final = 8.0

#: Top of the first block and the lowest a block may reach (PDF points).
TOP_EDGE: Final = PAGE_HEIGHT - 68.0
BOTTOM_EDGE: Final = 62.0
#: Clear space between blocks. The extractor starts a new paragraph when the
#: gap exceeds 1.5 times the line height (15 pt at 10 pt type); 20 pt leaves a
#: wide margin without wasting the page.
BLOCK_GAP: Final = 20.0
#: Fraction of the font size above the baseline in a character box.
_ASCENT: Final = 0.793

#: The PDF's own metadata, fixed so the bytes never depend on the clock.
CREATOR: Final = "TrustOps fcrm_seed.gen_docs (synthetic demo data)"
AUTHOR: Final = "TrustOps synthetic demo data"

STAGE_INTAKE: Final = "intake"
STAGE_CLARIFICATION: Final = "clarification_v2"
_ENCODING: Final = "cp1252"


class DocumentLayoutError(ValueError):
    """A page cannot be laid out so the extractor reads it as intended."""


@dataclass(frozen=True)
class Fact:
    """One fact the extractor should find, declared next to the text that
    carries it. ``quote`` must be a verbatim substring of the Markdown line
    the block renders to.
    """

    field_id: Optional[str]
    label: str
    value: str
    quote: str
    stage: str = STAGE_INTAKE
    seed_source: Optional[str] = None


@dataclass(frozen=True)
class Heading:
    """A bold section heading: one Markdown line."""

    text: str


@dataclass(frozen=True)
class Paragraph:
    """Running text: wrapped on the page, one Markdown line."""

    text: str
    facts: tuple[Fact, ...] = ()


@dataclass(frozen=True)
class Table:
    """A ruled table with one-line cells: ``2 + len(rows)`` Markdown lines.

    ``facts`` maps a row index (0-based, header excluded) to the facts that row
    carries.
    """

    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    widths: tuple[float, ...]
    facts: Mapping[int, tuple[Fact, ...]] = field(default_factory=dict)


Block = Union[Heading, Paragraph, Table]


@dataclass(frozen=True)
class Page:
    """One PDF page: blocks from top to bottom."""

    blocks: tuple[Block, ...]


@dataclass(frozen=True)
class DocumentSpec:
    """A whole document. ``running_notice`` (drawn at the top and bottom of
    every page, which the extractor strips as a repeated header and footer)
    needs at least three pages to be stripped, so a shorter document sets it to
    ``None``.
    """

    filename: str
    title: str
    subject: str
    pages: tuple[Page, ...]
    running_notice: Optional[str] = None


class FactRow(BaseModel):
    """One row of ``facts.json``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    field_id: Optional[str]
    label: str
    value: str
    doc: str
    page: int
    line: int
    quote: str
    stage: str
    seed_source: Optional[str]


# ------------------------------------------------------------------ geometry
def block_line_count(block: Block) -> int:
    """Markdown lines the extractor renders ``block`` to."""
    if isinstance(block, Table):
        return 2 + len(block.rows)
    return 1


def block_first_lines(page: Page) -> list[int]:
    """1-based page-relative line on which each block starts (one blank line
    between blocks, as the Markdown renderer writes them).
    """
    starts: list[int] = []
    line = 1
    for block in page.blocks:
        starts.append(line)
        line += block_line_count(block) + 1
    return starts


def _width(text: str, font: str, size: float) -> float:
    return float(stringWidth(text, font, size))


def wrap_text(text: str, font: str, size: float, width: float) -> list[str]:
    """Greedy word wrap that never starts a line with a digit.

    The extractor reads a visual line that begins with a number as a numbered
    heading, so a word that starts with a digit is glued to the word before it.
    """
    chunks: list[str] = []
    for word in text.split(" "):
        if chunks and word[:1].isdigit():
            chunks[-1] += " " + word
        else:
            chunks.append(word)
    lines: list[str] = []
    current = ""
    for chunk in chunks:
        trial = f"{current} {chunk}" if current else chunk
        if current and _width(trial, font, size) > width:
            lines.append(current)
            current = chunk
        else:
            current = trial
    if current:
        lines.append(current)
    return lines


def _check_text(text: str, where: str) -> None:
    """Reject text the standard fonts or the extractor would mangle."""
    try:
        text.encode(_ENCODING)
    except UnicodeEncodeError as exc:
        raise DocumentLayoutError(f"{where}: {text!r} is not cp1252-encodable") from exc
    if "|" in text or "  " in text or text != text.strip():
        raise DocumentLayoutError(f"{where}: {text!r} has a pipe or odd spacing")


def paragraph_lines(paragraph: Paragraph) -> list[str]:
    """The wrapped visual lines of ``paragraph``, validated."""
    _check_text(paragraph.text, "paragraph")
    if paragraph.text[:1].isdigit():
        raise DocumentLayoutError(f"paragraph starts with a digit: {paragraph.text!r}")
    lines = wrap_text(paragraph.text, BODY_FONT, BODY_SIZE, BODY_WIDTH)
    if any(line.endswith("-") for line in lines):
        raise DocumentLayoutError(f"a wrapped line ends with a hyphen: {lines!r}")
    return lines


def _check_heading(heading: Heading) -> None:
    _check_text(heading.text, "heading")
    if len(heading.text.split()) > 12 or heading.text[-1] in ".,;":
        raise DocumentLayoutError(f"heading would not read as one: {heading.text!r}")
    if _width(heading.text, HEADING_FONT, HEADING_SIZE) > BODY_WIDTH:
        raise DocumentLayoutError(f"heading wraps: {heading.text!r}")


def _check_table(table: Table) -> None:
    columns = len(table.header)
    if not table.widths or len(table.widths) != columns:
        raise DocumentLayoutError("table widths must match the header")
    if sum(table.widths) > BODY_WIDTH + 0.01:
        raise DocumentLayoutError(f"table wider than the page: {table.widths}")
    for index, row in enumerate((table.header, *table.rows)):
        if len(row) != columns:
            raise DocumentLayoutError(f"table row {index} has the wrong cell count")
        font = TABLE_BOLD_FONT if index == 0 else TABLE_FONT
        for text, cell_width in zip(row, table.widths, strict=True):
            if text:
                _check_text(text, f"table row {index}")
            if _width(text, font, TABLE_SIZE) + 2 * CELL_PAD_X > cell_width:
                raise DocumentLayoutError(f"cell does not fit on one line: {text!r}")


def block_height(block: Block) -> float:
    """Vertical extent of ``block`` in points (first character top to last
    character bottom for text, the grid height for a table).
    """
    if isinstance(block, Table):
        return ROW_HEIGHT * (1 + len(block.rows))
    if isinstance(block, Heading):
        return HEADING_SIZE
    return (len(paragraph_lines(block)) - 1) * LEADING + BODY_SIZE


def block_tops(page: Page) -> list[float]:
    """The y of the top edge of every block, validating that the page fits."""
    tops: list[float] = []
    y = TOP_EDGE
    for block in page.blocks:
        if isinstance(block, Heading):
            _check_heading(block)
        elif isinstance(block, Table):
            _check_table(block)
        tops.append(y)
        y -= block_height(block) + BLOCK_GAP
    bottom = y + BLOCK_GAP
    if bottom < BOTTOM_EDGE:
        raise DocumentLayoutError(f"page overflows by {BOTTOM_EDGE - bottom:.1f} pt")
    return tops


# -------------------------------------------------------------------- facts
def fact_rows(spec: DocumentSpec) -> list[FactRow]:
    """The page and line of every fact in ``spec``, predicted from the block
    structure (the unit test checks the prediction against the real extractor).
    """
    rows: list[FactRow] = []
    for number, page in enumerate(spec.pages, start=1):
        for block, start in zip(page.blocks, block_first_lines(page), strict=True):
            if isinstance(block, Paragraph):
                placed = [(start, fact) for fact in block.facts]
            elif isinstance(block, Table):
                placed = [
                    (start + 2 + index, fact)
                    for index, facts in block.facts.items()
                    for fact in facts
                ]
            else:
                placed = []
            rows.extend(
                FactRow(
                    field_id=fact.field_id,
                    label=fact.label,
                    value=fact.value,
                    doc=spec.filename,
                    page=number,
                    line=line,
                    quote=fact.quote,
                    stage=fact.stage,
                    seed_source=fact.seed_source,
                )
                for line, fact in placed
            )
    return rows


def markdown_line(block: Block, row: Optional[int] = None) -> str:
    """The Markdown line a block (or one table row) renders to, before masking."""
    if isinstance(block, Table):
        cells = block.header if row is None else block.rows[row]
        return "| " + " | ".join(cells) + " |"
    if isinstance(block, Heading):
        return block.text
    return block.text


def validate_facts(spec: DocumentSpec) -> None:
    """Every fact's quote must be a substring of the line it is declared on."""
    for page in spec.pages:
        for block in page.blocks:
            if isinstance(block, Paragraph):
                lines = [(markdown_line(block), fact) for fact in block.facts]
            elif isinstance(block, Table):
                lines = [
                    (markdown_line(block, index), fact)
                    for index, facts in block.facts.items()
                    for fact in facts
                ]
            else:
                lines = []
            for line, fact in lines:
                if fact.quote not in line:
                    raise DocumentLayoutError(
                        f"{spec.filename}: quote {fact.quote!r} is not in {line!r}"
                    )


# ---------------------------------------------------------------- rendering
def _draw_paragraph(canvas: Canvas, paragraph: Paragraph, top: float) -> None:
    canvas.setFont(BODY_FONT, BODY_SIZE)
    first = top - _ASCENT * BODY_SIZE
    for index, line in enumerate(paragraph_lines(paragraph)):
        canvas.drawString(MARGIN_X, first - index * LEADING, line)


def _draw_heading(canvas: Canvas, heading: Heading, top: float) -> None:
    canvas.setFont(HEADING_FONT, HEADING_SIZE)
    canvas.drawString(MARGIN_X, top - _ASCENT * HEADING_SIZE, heading.text)


def _draw_table(canvas: Canvas, table: Table, top: float) -> None:
    xs = [MARGIN_X]
    for width in table.widths:
        xs.append(xs[-1] + width)
    all_rows = (table.header, *table.rows)
    ys = [top - ROW_HEIGHT * index for index in range(len(all_rows) + 1)]
    canvas.setLineWidth(0.6)
    canvas.setStrokeGray(0.3)
    canvas.grid(xs, ys)
    for index, row in enumerate(all_rows):
        canvas.setFont(TABLE_BOLD_FONT if index == 0 else TABLE_FONT, TABLE_SIZE)
        baseline = ys[index] - ROW_HEIGHT / 2 - TABLE_SIZE * 0.35
        for text, left in zip(row, xs, strict=False):
            if text:
                canvas.drawString(left + CELL_PAD_X, baseline, text)


def _draw_notice(canvas: Canvas, notice: str, number: int, total: int) -> None:
    canvas.setFont(NOTICE_FONT, NOTICE_SIZE)
    canvas.setFillGray(0.4)
    canvas.drawString(MARGIN_X, PAGE_HEIGHT - 40.0, notice)
    canvas.drawString(
        MARGIN_X,
        36.0,
        "Synthetic demonstration document, no real person or organisation. "
        f"Page {number} of {total}",
    )
    canvas.setFillGray(0.0)


def render_pdf(spec: DocumentSpec) -> bytes:
    """The PDF bytes of ``spec``; identical on every run."""
    buffer = io.BytesIO()
    canvas = Canvas(
        buffer,
        pagesize=letter,
        invariant=1,
        pageCompression=0,
        initialFontName=BODY_FONT,
    )
    canvas.setTitle(spec.title)
    canvas.setAuthor(AUTHOR)
    canvas.setSubject(spec.subject)
    canvas.setCreator(CREATOR)
    total = len(spec.pages)
    for number, page in enumerate(spec.pages, start=1):
        tops = block_tops(page)
        if spec.running_notice:
            _draw_notice(canvas, spec.running_notice, number, total)
        for block, top in zip(page.blocks, tops, strict=True):
            canvas.setStrokeGray(0.0)
            if isinstance(block, Heading):
                _draw_heading(canvas, block, top)
            elif isinstance(block, Table):
                _draw_table(canvas, block, top)
            else:
                _draw_paragraph(canvas, block, top)
        canvas.showPage()
    canvas.save()
    return buffer.getvalue()


def facts_json(rows: Sequence[FactRow]) -> bytes:
    """``facts.json``: a list of rows, two-space indent, UTF-8, trailing newline."""
    payload = [row.model_dump(mode="json") for row in rows]
    return (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
