r"""``GfmRenderer`` — the only writer of canonical Markdown.

Scope & functionality
---------------------
Turns a ``Document`` into the GFM body every consumer (hashing, profiling,
the context chunker) reads. Being the single writer is what lets
``core/markdown_scan.py`` be the single reader: the grammar is defined once
on each side. Policies (all property-tested in ``tests/test_renderer.py``):

* Tables — ``colspan`` puts the text in the first slot and pads empty cells;
  ``rowspan`` repeats the text on the covered rows (``RowspanPolicy.repeat``,
  default; ``blank`` / ``marker`` selectable); a nested table is flattened
  into its cell (``<br>`` between rows, `` / `` between cells) with a
  ``nested_table_flattened`` warning; cell text escapes ``|`` as ``\\|`` and
  newlines as ``<br>``; all-blank rows are dropped; ``header_rows == 0``
  yields a synthetic (empty-cell) header row so the block is still a GFM
  table; ``caption`` renders as a ``Table: …`` line before the table.
* Markers are exactly ``--- Page N ---``, ``--- Sheet: {name} ---`` and
  ``--- Slide N ---``.
* Prose lines that *look* like block syntax (ATX ``#``, list markers, table
  pipes, fences, rules, markers, block quotes) are backslash-escaped so
  re-scanning the output never invents structure.
* Headings inside quotes render as bold text (a panel's title is not a
  document section); everything else inside a quote is prefixed ``> ``.
* ``Raw`` passes through untouched apart from CRLF normalisation.

``render`` returns ``Rendered(markdown, heading_lines, warnings, page_lines)``
— the heading line numbers feed ``structure.headings[].line`` and (TrustOps
addition, ``RENDERER_VERSION`` 3) the page line ranges feed
``structure.pages[]`` so page:line citations resolve to real text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from evoq_ingestion.core.codes import WarningCode
from evoq_ingestion.core.markdown_scan import (
    FENCE_RE,
    HEADING_RE,
    HR_RE,
    LIST_ITEM_RE,
    PAGE_MARKER,
    PAGE_MARKER_RE,
    SHEET_MARKER,
    SHEET_MARKER_RE,
    SLIDE_MARKER,
    SLIDE_MARKER_RE,
    TABLE_ROW_RE,
    TABLE_SEP_RE,
    HeadingLine,
    PageLines,
    scan_markdown,
    scan_page_lines,
)
from evoq_ingestion.model.blocks import (
    Block,
    Cell,
    CodeBlock,
    Heading,
    HorizontalRule,
    ImageRef,
    ListBlock,
    ListItem,
    Note,
    NoteKind,
    PageBreak,
    Paragraph,
    Quote,
    Raw,
    SheetBreak,
    SlideBreak,
    Table,
    inline_text,
)
from evoq_ingestion.model.document import Document

_ATX_LIKE_RE = re.compile(r"^\s{0,3}#{1,6}(?:\s|$)")
_LIST_MARKER_RE = re.compile(r"^(\s*)([-*+]|\d{1,9})([.)]?)(\s+)")
_SETEXT_OR_RULE_RE = re.compile(r"^\s{0,3}(?:-+|=+|\*+|_+)\s*$")
_QUOTE_RE = re.compile(r"^\s{0,3}>")
_NOTE_LABELS: dict[NoteKind, str] = {
    NoteKind.speaker_notes: "Speaker notes",
    NoteKind.header: "Header",
    NoteKind.footer: "Footer",
    NoteKind.textbox: "Text box",
    NoteKind.comment: "Comment",
    NoteKind.footnote: "Footnote",
    NoteKind.endnote: "Endnote",
    NoteKind.chart: "Chart",
}


class RowspanPolicy(StrEnum):
    """How a vertically merged cell is written on the rows it covers."""

    repeat = "repeat"
    blank = "blank"
    marker = "marker"


@dataclass(frozen=True, slots=True)
class Rendered:
    """Renderer output: the body plus the facts only the renderer knows."""

    markdown: str
    heading_lines: tuple[HeadingLine, ...] = ()
    warnings: tuple[str, ...] = ()
    page_lines: tuple[PageLines, ...] = ()


class GfmRenderer:
    """Render a ``Document`` to canonical GitHub-flavoured Markdown."""

    ROWSPAN_MARKER = "^"
    RENDERER_VERSION = "3"  # TrustOps: "3" adds page_lines / structure.pages

    def __init__(self, *, rowspan_policy: RowspanPolicy | str = RowspanPolicy.repeat) -> None:
        """Construct a renderer with the given rowspan policy (default: repeat)."""
        self._rowspan_policy = RowspanPolicy(rowspan_policy)

    @property
    def rowspan_policy(self) -> RowspanPolicy:
        """The configured rowspan policy."""
        return self._rowspan_policy

    # ---------------------------------------------------------------- public
    def render(self, doc: Document) -> Rendered:
        """Render every block; ``markdown`` ends with exactly one newline (or is empty)."""
        lines: list[str] = []
        headings: list[HeadingLine] = []
        warnings: list[str] = []
        for block in doc.blocks:
            block_lines = self._block_lines(block, warnings)
            if not block_lines:
                continue
            if lines:
                lines.append("")
            offset = len(lines)
            if isinstance(block, Heading):
                headings.append(HeadingLine(block.level, _heading_text(block), offset + 1))
            elif isinstance(block, Raw):
                headings.extend(
                    HeadingLine(found.level, found.text, offset + found.line)
                    for found in scan_markdown("\n".join(block_lines)).headings
                )
            lines.extend(block_lines)
        markdown = "\n".join(lines) + "\n" if lines else ""
        # Page anchors are read back off the final body with the one shared
        # marker reader: prose that merely looks like a marker is escaped
        # above, so every match is a real PageBreak (or a marker a Raw
        # passthrough carried, which downstream readers treat identically).
        return Rendered(
            markdown=markdown,
            heading_lines=tuple(headings),
            warnings=tuple(warnings),
            page_lines=scan_page_lines(markdown),
        )

    def render_table(self, table: Table) -> tuple[list[str], list[str]]:
        """Render one table to GFM lines; returns ``(lines, warnings)``."""
        warnings: list[str] = []
        return self._table_lines(table, warnings), warnings

    # ---------------------------------------------------------------- blocks
    def _block_lines(  # noqa: C901, PLR0912 - one dispatch site over the closed Block union
        self, block: Block, warnings: list[str]
    ) -> list[str]:
        match block:
            case Heading():
                text = _heading_text(block)
                return [f"{'#' * block.level} {text}"] if text else []
            case Paragraph():
                return _prose_lines(block.text)
            case ListBlock():
                return self._list_lines(block, warnings)
            case Table():
                return self._table_lines(block, warnings)
            case CodeBlock():
                return _code_lines(block)
            case Quote():
                return self._quote_lines(block, warnings)
            case ImageRef():
                return [_image_line(block)]
            case Note():
                return _note_lines(block)
            case PageBreak():
                return [PAGE_MARKER.format(n=block.n)]
            case SheetBreak():
                return [SHEET_MARKER.format(name=inline_text(block.name) or "Sheet")]
            case SlideBreak():
                return [SLIDE_MARKER.format(n=block.n)]
            case HorizontalRule():
                return ["---"]
            case Raw():
                return _raw_lines(block.markdown)
        return []

    def _list_lines(self, block: ListBlock, warnings: list[str]) -> list[str]:
        lines: list[str] = []
        ordinal = block.start
        for item in block.items:
            text_lines = _prose_lines(item.text)
            if not text_lines and not item.children:
                continue
            if block.ordered:
                number = item.ordinal if item.ordinal is not None else ordinal
                marker = f"{number}."
                ordinal = number + 1
            else:
                marker = "-"
            if item.checked is not None:
                marker += " [x]" if item.checked else " [ ]"
            indent = " " * (len(marker) + 1)
            first, *rest = text_lines or [""]
            lines.append(f"{marker} {first}".rstrip())
            lines.extend(indent + line for line in rest)
            lines.extend(self._item_children_lines(item, indent, warnings))
        return lines

    def _item_children_lines(self, item: ListItem, indent: str, warnings: list[str]) -> list[str]:
        lines: list[str] = []
        for child in item.children:
            child_lines = self._block_lines(child, warnings)
            if not child_lines:
                continue
            if not isinstance(child, ListBlock):
                lines.append("")
            lines.extend((indent + line) if line else "" for line in child_lines)
        return lines

    def _quote_lines(self, block: Quote, warnings: list[str]) -> list[str]:
        inner: list[str] = []
        if block.label:
            inner.append(f"**{_escape_inline(inline_text(block.label))}**")
        for child in block.children:
            if isinstance(child, Heading):
                text = _heading_text(child)
                child_lines = [f"**{text}**"] if text else []
            else:
                child_lines = self._block_lines(child, warnings)
            if not child_lines:
                continue
            if inner:
                inner.append("")
            inner.extend(child_lines)
        return [f"> {line}" if line else ">" for line in inner]

    # ---------------------------------------------------------------- tables
    def _table_lines(self, table: Table, warnings: list[str]) -> list[str]:
        grid = self._expand_grid(table, warnings)
        grid = [row for row in grid if any(cell.strip() for cell in row)]
        if not grid:
            return []
        width = max(len(row) for row in grid)
        for row in grid:
            row.extend([""] * (width - len(row)))
        if table.header_rows >= 1:
            header, body = grid[0], grid[1:]
        else:
            header, body = [""] * width, grid
        lines: list[str] = []
        if table.caption and inline_text(table.caption):
            lines.append(f"Table: {_escape_inline(inline_text(table.caption))}")
            lines.append("")
        lines.append(_row_line(header))
        lines.append("|" + "|".join(["---"] * width) + "|")
        lines.extend(_row_line(row) for row in body)
        return lines

    def _expand_grid(self, table: Table, warnings: list[str]) -> list[list[str]]:
        grid: list[list[str]] = []
        pending: dict[int, tuple[str, int]] = {}
        for row in table.rows:
            out: list[str] = []
            col = 0
            cells = iter(row.cells)
            while True:
                if col in pending:
                    text, remaining = pending[col]
                    out.append(self._covered_text(text))
                    if remaining <= 1:
                        del pending[col]
                    else:
                        pending[col] = (text, remaining - 1)
                    col += 1
                    continue
                cell = next(cells, None)
                if cell is None:
                    if any(c > col for c in pending):
                        out.append("")
                        col += 1
                        continue
                    break
                text = self._cell_text(cell, warnings)
                out.append(text)
                out.extend([""] * (cell.colspan - 1))
                if cell.rowspan > 1:
                    for c in range(col, col + cell.colspan):
                        pending[c] = (text if c == col else "", cell.rowspan - 1)
                col += cell.colspan
            grid.append(out)
        return grid

    def _covered_text(self, text: str) -> str:
        if not text:
            return ""
        if self._rowspan_policy is RowspanPolicy.repeat:
            return text
        if self._rowspan_policy is RowspanPolicy.marker:
            return self.ROWSPAN_MARKER
        return ""

    def _cell_text(self, cell: Cell, warnings: list[str]) -> str:
        parts = [_escape_cell(cell.text)] if cell.text.strip() else []
        for nested in cell.nested:
            flattened = self._flatten_nested(nested, warnings)
            if flattened:
                parts.append(flattened)
            if WarningCode.nested_table_flattened not in warnings:
                warnings.append(str(WarningCode.nested_table_flattened))
        return "<br>".join(parts)

    def _flatten_nested(self, table: Table, warnings: list[str]) -> str:
        rows = [
            " / ".join(row)
            for row in self._expand_grid(table, warnings)
            if any(cell.strip() for cell in row)
        ]
        return "<br>".join(rows)


# ------------------------------------------------------------------- helpers
def _heading_text(block: Heading) -> str:
    return _escape_inline(inline_text(block.text))


def _row_line(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _escape_cell(text: str) -> str:
    normalised = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    lines = [" ".join(line.split()) for line in normalised.split("\n")]
    return "<br>".join(line.replace("|", "\\|") for line in lines if line or len(lines) == 1)


def _escape_inline(text: str) -> str:
    return text.replace("|", "\\|")


def _prose_lines(text: str) -> list[str]:
    normalised = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.strip() for line in normalised.split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    out: list[str] = []
    for line in lines:
        if not line:
            if out and out[-1]:
                out.append("")
            continue
        out.append(escape_block_syntax(line))
    return out


def escape_block_syntax(line: str) -> str:
    """Backslash-escape a prose line that would otherwise read as block syntax."""
    if not line:
        return line
    if (
        _ATX_LIKE_RE.match(line)
        or FENCE_RE.match(line)
        or _SETEXT_OR_RULE_RE.match(line)
        or HR_RE.match(line)
        or TABLE_SEP_RE.match(line)
        or _QUOTE_RE.match(line)
        or PAGE_MARKER_RE.match(line)
        or SHEET_MARKER_RE.match(line)
        or SLIDE_MARKER_RE.match(line)
        or HEADING_RE.match(line)
    ):
        return "\\" + line
    if TABLE_ROW_RE.match(line):
        stripped = line.lstrip()
        return "\\" + stripped
    marker = _LIST_MARKER_RE.match(line)
    if marker and LIST_ITEM_RE.match(line):
        lead, token, punct, space = marker.groups()
        rest = line[marker.end() :]
        if token.isdigit():
            return f"{lead}{token}\\{punct}{space}{rest}"
        return f"{lead}\\{token}{space}{rest}"
    return line


def _code_lines(block: CodeBlock) -> list[str]:
    code = block.code.replace("\r\n", "\n").replace("\r", "\n")
    if code.endswith("\n"):
        code = code[:-1]
    fence = "```"
    while fence in code:
        fence += "`"
    lang = inline_text(block.lang).split(" ")[0] if block.lang else ""
    return [fence + lang, *code.split("\n"), fence]


def _image_line(block: ImageRef) -> str:
    alt = inline_text(block.alt).replace("]", "\\]")
    target = "".join(block.target.split())
    return f"![{alt}]({target})"


def _note_lines(block: Note) -> list[str]:
    body = _prose_lines(block.text)
    if not body:
        return []
    label = _NOTE_LABELS[block.kind]
    if block.ref:
        label = f"{label} {inline_text(block.ref)}"
    first, *rest = body
    return [f"**{label}:** {first}", *rest]


def _raw_lines(markdown: str) -> list[str]:
    normalised = markdown.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalised.split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return lines
