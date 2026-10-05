r"""PDF extractors — ``pdfplumber`` (default) and ``pypdf`` (fallback).

Scope & functionality
---------------------
``PdfplumberExtractor`` is the default engine: it opens with an empty
password (encrypted -> ``EncryptedFile``, unparseable syntax ->
``CorruptFile``), refuses a page count over ``limits.max_pdf_pages`` before
touching any page's content, then in one pass over every page collects
per-line font sizes (``pdf.fonts.FontStats``) and first/last-line
candidates for header/footer removal (``pdf.repeated.RepeatedLineDetector``)
plus each page's raw text/image facts for **per-page** scanned detection —
the "average across pages" bug the audit found is fixed here by design:
a page counts as scanned when *that page's own* text is thin and it has
images, and the whole file fails only when the *fraction* of such pages
meets ``options.pdf_scanned_fail_fraction``.

A second pass builds the ``Document``: every table
(``page.find_tables()``) is rendered once and its bounding box is cropped
out of the text layer (``page.filter()``) before prose is extracted, so
table content is never duplicated as scrambled prose (the #1 empirically
verified bug this lane closes); a persistent x-gap
(``pdf.columns.ColumnDetector``) splits multi-column pages into separate
left-to-right text streams; within each stream, lines are classified as
headings by font-size ratio or line-leading numbering (``pdf.headings``)
or assembled into paragraphs with hyphenation/ligature repair
(``pdf.lines.LineAssembler``), interleaved with that column's tables in
top-to-bottom order; a page whose own text is near-empty but which has
characters is retried with ``layout=True`` (rotated-page recovery). A
single page's (or table's) failure is recorded as a ``UnitFailure`` and
does not fail the document; only when every page fails does the whole file
become ``CorruptFile``.

``PypdfExtractor`` is the simpler fallback used when ``pdfplumber`` is
unavailable or explicitly configured: per-page text via
``page.extract_text()``, the same hyphenation/ligature helpers,
numbering-only heading inference (no font sizes — pypdf doesn't expose
them easily), the same page cap and per-page scanned-fraction logic.

Interface contract for the (not-yet-wired) registry/record layer
------------------------------------------------------------------
Both engines write ``doc.metadata["scanned_page_fraction"]`` (float) and
``doc.metadata["is_scanned_pdf"]`` (bool, ``fraction >= 0.5``) — the facts
``ExtractionRecord`` needs that have no home on ``Document`` itself.
``raw_hash`` is deliberately never computed here: ``FileFacts.raw_hash``
(the gate's sha256 of the raw bytes) is the source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pdfplumber
from pdfminer.pdfdocument import PDFEncryptionError
from pdfminer.pdfexceptions import PDFException
from pdfminer.pdfparser import PDFSyntaxError
from pdfplumber.utils.exceptions import PdfminerException
from pypdf import PasswordType as _PdfPasswordType
from pypdf import PdfReader
from pypdf.errors import FileNotDecryptedError, PdfReadError

from evoq_ingestion.core.codes import FailureCode, WarningCode
from evoq_ingestion.extractors.base import (
    BaseExtractor,
    CorruptFile,
    EncryptedFile,
    EngineInfo,
    ExtractionError,
    ExtractionFailed,
    LimitExceeded,
)
from evoq_ingestion.extractors.pdf.columns import ColumnDetector
from evoq_ingestion.extractors.pdf.fonts import FontStats
from evoq_ingestion.extractors.pdf.headings import HeadingInferencer, classify_numbered
from evoq_ingestion.extractors.pdf.lines import (
    LineAssembler,
    extract_lines,
    join_hyphenated,
    normalize_repeated_line,
    repair_ligatures,
)
from evoq_ingestion.extractors.pdf.repeated import RepeatedLineDetector
from evoq_ingestion.model.blocks import Block, Cell, PageBreak, Paragraph, Row, Table
from evoq_ingestion.model.document import Document

if TYPE_CHECKING:
    from pathlib import Path

    from evoq_ingestion.core.limits import Deadline
    from evoq_ingestion.extractors.gate import FileFacts
    from evoq_ingestion.extractors.pdf.lines import PdfLine

_ROTATION_MIN_CHARS = 3
# (top, x-center, rendered block) — enough to sort a table into its column
# and interleave it with that column's lines in reading order.
_TableEntry = tuple[float, float, Table]


@dataclass(frozen=True, slots=True)
class _DocumentScan:
    """Facts the first pdfplumber pass gathers before any block is built."""

    body_size: float
    repeated: set[str]
    fraction: float
    scanned_count: int
    page_errors: dict[int, Exception]


class ScannedPdfFailure(ExtractionFailed):
    """Whole-file refusal: the scanned-page fraction met ``pdf_scanned_fail_fraction``."""

    code = FailureCode.scanned_pdf_no_ocr


# ============================================================== pdfplumber
class PdfplumberExtractor(BaseExtractor):
    """Default PDF engine: layout-aware headings, tables, columns, de-duplication."""

    info = EngineInfo("pdfplumber", "2")
    supports = frozenset({".pdf"})

    def classify_error(self, exc: Exception, path: Path) -> ExtractionError:
        """Map pdfminer's wrapped exceptions to ``EncryptedFile`` / ``CorruptFile``."""
        cause: BaseException = exc
        if isinstance(exc, PdfminerException) and exc.args and isinstance(exc.args[0], Exception):
            cause = exc.args[0]
        if isinstance(cause, PDFEncryptionError):
            return EncryptedFile(f"{path.name} is encrypted", detail=path.name)
        if isinstance(cause, PDFSyntaxError | PDFException):
            return CorruptFile(f"{path.name} could not be parsed: {cause}", detail=path.name)
        return super().classify_error(exc, path)

    def _parse(self, path: Path, facts: FileFacts, deadline: Deadline) -> Document:  # noqa: ARG002
        limits = self.options.limits
        with pdfplumber.open(str(path), password="") as pdf:
            n_pages = len(pdf.pages)
            if n_pages > limits.max_pdf_pages:
                raise LimitExceeded(
                    f"{path.name} has {n_pages} pages (limit {limits.max_pdf_pages})",
                    code=FailureCode.page_limit_exceeded,
                    detail=str(n_pages),
                )
            if n_pages == 0:
                raise CorruptFile(f"{path.name} has no pages")

            scan = self._scan_document(pdf, deadline)
            if scan.fraction >= self.options.pdf_scanned_fail_fraction:
                raise ScannedPdfFailure(
                    f"{scan.scanned_count}/{n_pages} pages of {path.name} appear scanned",
                    detail=f"{scan.scanned_count}/{n_pages}",
                )
            return self._build_document(pdf, path, n_pages, scan, deadline)

    def _build_document(
        self, pdf: Any, path: Path, n_pages: int, scan: _DocumentScan, deadline: Deadline  # noqa: ANN401
    ) -> Document:
        """Second pass: build every page's blocks from the first pass's facts."""
        doc = Document()
        if scan.repeated:
            doc.add_warning(WarningCode.header_footer_removed, len(scan.repeated))
        if scan.scanned_count:
            doc.add_warning(WarningCode.scanned_pages, f"{scan.scanned_count}/{n_pages}")

        for i, page in enumerate(pdf.pages, start=1):
            deadline.check()
            doc.add(PageBreak(i))
            if i in scan.page_errors:
                doc.fail_unit("page", i, FailureCode.extraction_failed, str(scan.page_errors[i]))
            else:
                self._add_page(doc, page, i, body_size=scan.body_size, repeated=scan.repeated)
            page.flush_cache()

        page_failures = sum(1 for f in doc.units.failed if f.kind == "page")
        if n_pages > 0 and page_failures == n_pages:
            raise CorruptFile(f"all {n_pages} pages of {path.name} failed to parse")

        doc.units.page_count = n_pages
        for source in sorted({h.source for h in doc.headings() if h.inferred}):
            doc.add_warning(WarningCode.inferred_headings, source)
        doc.metadata["scanned_page_fraction"] = scan.fraction
        doc.metadata["is_scanned_pdf"] = scan.fraction >= 0.5
        doc.title = path.stem
        return doc

    def _add_page(
        self, doc: Document, page: Any, page_num: int, *, body_size: float, repeated: set[str]  # noqa: ANN401
    ) -> None:
        """Build one page's blocks; a failure here is a per-page ``UnitFailure``, not a crash."""
        try:
            blocks = self._build_page(page, page_num, doc, body_size=body_size, repeated=repeated)
        except Exception as exc:  # noqa: BLE001 - one bad page must not fail the file
            doc.fail_unit("page", page_num, FailureCode.extraction_failed, str(exc))
            return
        doc.blocks.extend(blocks)

    # ---------------------------------------------------------- first pass
    def _scan_document(
        self, pdf: Any, deadline: Deadline  # noqa: ANN401 - pdfplumber has no public PDF type
    ) -> _DocumentScan:
        """One pass: body font size, repeated edge lines, per-page scanned facts."""
        font_stats = FontStats()
        repeated_detector = RepeatedLineDetector()
        min_chars = self.options.pdf_min_chars_per_page
        scanned_flags: list[bool] = []
        page_errors: dict[int, Exception] = {}

        for i, page in enumerate(pdf.pages, start=1):
            deadline.check()
            try:
                lines = extract_lines(page)
            except Exception:  # noqa: BLE001 - font/repeated stats are best-effort
                lines = []
            font_stats.add_lines(lines)
            repeated_detector.observe(lines)
            try:
                text = page.extract_text() or ""
                images = page.images
            except Exception as exc:  # noqa: BLE001 - isolated; Pass 2 records the UnitFailure
                page_errors[i] = exc
                scanned_flags.append(False)
                continue
            scanned_flags.append(len(text.strip()) < min_chars and bool(images))

        n = len(scanned_flags)
        scanned_count = sum(scanned_flags)
        fraction = scanned_count / n if n else 0.0
        return _DocumentScan(
            body_size=font_stats.body_size,
            repeated=repeated_detector.repeated(),
            fraction=fraction,
            scanned_count=scanned_count,
            page_errors=page_errors,
        )

    # ------------------------------------------------------------- one page
    def _build_page(
        self,
        page: Any,  # noqa: ANN401 - pdfplumber has no public Page type
        page_num: int,
        doc: Document,
        *,
        body_size: float,
        repeated: set[str],
    ) -> list[Block]:
        table_entries: list[_TableEntry] = []
        bboxes: list[tuple[float, float, float, float]] = []
        for idx, table in enumerate(page.find_tables(), start=1):
            try:
                block = _build_table_block(table)
            except Exception as exc:  # noqa: BLE001 - one table must not fail the page
                doc.fail_unit("table", f"{page_num}:{idx}", FailureCode.extraction_failed, str(exc))
                continue
            x0, top, x1, _bottom = table.bbox
            table_entries.append((top, (x0 + x1) / 2, block))
            bboxes.append(table.bbox)

        filtered = page.filter(lambda obj: not _in_any_bbox(obj, bboxes)) if bboxes else page

        probe = page.extract_text() or ""
        if len(probe.strip()) < _ROTATION_MIN_CHARS and page.chars:
            layout_text = page.extract_text(layout=True) or ""
            if len(layout_text.strip()) >= _ROTATION_MIN_CHARS:
                doc.add_warning(WarningCode.rotated_page, page_num)
                paragraphs = [p.strip() for p in layout_text.split("\n\n") if p.strip()]
                blocks: list[Block] = [Paragraph(text=repair_ligatures(p)) for p in paragraphs]
                blocks.extend(block for _, _, block in sorted(table_entries, key=lambda e: e[0]))
                return blocks

        columns = ColumnDetector().detect(filtered)
        if columns and len(columns) > 1:
            doc.add_warning(WarningCode.multi_column_detected, len(columns))
        else:
            columns = [(0.0, float(filtered.width))]

        blocks = []
        for col_x0, col_x1 in columns:
            sub = (
                filtered.within_bbox((col_x0, 0, col_x1, filtered.height))
                if len(columns) > 1
                else filtered
            )
            lines = [
                line
                for line in extract_lines(sub)
                if normalize_repeated_line(line.text) not in repeated
            ]
            col_tables = [
                (top, block)
                for top, x_center, block in table_entries
                if col_x0 - 1.0 <= x_center <= col_x1 + 1.0
            ]
            blocks.extend(_merge_column(lines, col_tables, body_size))
        return blocks


def _merge_column(
    lines: list[PdfLine], tables: list[tuple[float, Table]], body_size: float
) -> list[Block]:
    """Interleave classified lines (headings/paragraphs) and tables by vertical position."""
    inferencer = HeadingInferencer(body_size)
    events: list[tuple[float, PdfLine | None, Table | None]] = [
        (line.top, line, None) for line in lines
    ]
    events.extend((top, None, block) for top, block in tables)
    events.sort(key=lambda event: event[0])

    assembler = LineAssembler()
    blocks: list[Block] = []
    buffer: list[PdfLine] = []

    def flush() -> None:
        if buffer:
            blocks.extend(assembler.assemble(list(buffer)))
            buffer.clear()

    for _, line, table_block in events:
        if table_block is not None:
            flush()
            blocks.append(table_block)
            continue
        assert line is not None  # one of line/table_block is always set
        heading = inferencer.classify(line)
        if heading:
            flush()
            blocks.append(heading)
        else:
            buffer.append(line)
    flush()
    return blocks


def _build_table_block(table: Any) -> Table:  # noqa: ANN401 - pdfplumber Table has no public type
    """Render a ``pdfplumber`` table's cells (no rowspan/colspan info is available)."""
    rows_data = table.extract() or []
    rows = [
        Row(
            cells=tuple(
                Cell(text=(cell or "").strip(), header=(row_index == 0)) for cell in row
            )
        )
        for row_index, row in enumerate(rows_data)
    ]
    return Table(rows=tuple(rows), header_rows=1 if rows else 0)


def _in_any_bbox(
    obj: dict[str, float], bboxes: list[tuple[float, float, float, float]], pad: float = 0.5
) -> bool:
    if "x0" not in obj or "top" not in obj:
        return False
    cx = (obj["x0"] + obj.get("x1", obj["x0"])) / 2
    cy = (obj["top"] + obj.get("bottom", obj["top"])) / 2
    return any(
        bx0 - pad <= cx <= bx1 + pad and btop - pad <= cy <= bbot + pad
        for bx0, btop, bx1, bbot in bboxes
    )


# ==================================================================== pypdf
class PypdfExtractor(BaseExtractor):
    """Fallback PDF engine: plain per-page text, numbering-only heading inference."""

    info = EngineInfo("pypdf", "2")
    supports = frozenset({".pdf"})

    def classify_error(self, exc: Exception, path: Path) -> ExtractionError:
        """Map ``pypdf`` exceptions to ``EncryptedFile`` / ``CorruptFile``."""
        if isinstance(exc, FileNotDecryptedError):
            return EncryptedFile(f"{path.name} is encrypted", detail=path.name)
        if isinstance(exc, PdfReadError):
            return CorruptFile(f"{path.name} could not be parsed: {exc}", detail=path.name)
        return super().classify_error(exc, path)

    def _parse(self, path: Path, facts: FileFacts, deadline: Deadline) -> Document:  # noqa: ARG002
        reader = self._open_reader(path)
        n_pages = self._checked_page_count(reader, path)
        page_texts, page_errors, scanned_count = self._scan_pages(reader, deadline)
        fraction = scanned_count / n_pages if n_pages else 0.0
        if fraction >= self.options.pdf_scanned_fail_fraction:
            raise ScannedPdfFailure(
                f"{scanned_count}/{n_pages} pages of {path.name} appear scanned",
                detail=f"{scanned_count}/{n_pages}",
            )
        return self._build_document(path, n_pages, page_texts, page_errors, scanned_count, fraction)

    def _open_reader(self, path: Path) -> PdfReader:
        try:
            reader = PdfReader(str(path))
        except PdfReadError as exc:
            raise CorruptFile(f"{path.name} could not be parsed: {exc}", detail=path.name) from exc
        if reader.is_encrypted and reader.decrypt("") == _PdfPasswordType.NOT_DECRYPTED:
            raise EncryptedFile(f"{path.name} is encrypted", detail=path.name)
        return reader

    def _checked_page_count(self, reader: PdfReader, path: Path) -> int:
        try:
            n_pages = len(reader.pages)
        except PdfReadError as exc:
            raise CorruptFile(f"{path.name} could not be parsed: {exc}", detail=path.name) from exc
        limits = self.options.limits
        if n_pages > limits.max_pdf_pages:
            raise LimitExceeded(
                f"{path.name} has {n_pages} pages (limit {limits.max_pdf_pages})",
                code=FailureCode.page_limit_exceeded,
                detail=str(n_pages),
            )
        if n_pages == 0:
            raise CorruptFile(f"{path.name} has no pages")
        return n_pages

    def _scan_pages(
        self, reader: PdfReader, deadline: Deadline
    ) -> tuple[list[str], dict[int, Exception], int]:
        min_chars = self.options.pdf_min_chars_per_page
        page_texts: list[str] = []
        page_errors: dict[int, Exception] = {}
        scanned_count = 0
        for i, page in enumerate(reader.pages, start=1):
            deadline.check()
            try:
                text = page.extract_text() or ""
                images = page.images
            except Exception as exc:  # noqa: BLE001 - isolated per page
                page_errors[i] = exc
                page_texts.append("")
                continue
            page_texts.append(text)
            if len(text.strip()) < min_chars and images:
                scanned_count += 1
        return page_texts, page_errors, scanned_count

    def _build_document(
        self,
        path: Path,
        n_pages: int,
        page_texts: list[str],
        page_errors: dict[int, Exception],
        scanned_count: int,
        fraction: float,
    ) -> Document:
        doc = Document()
        if scanned_count:
            doc.add_warning(WarningCode.scanned_pages, f"{scanned_count}/{n_pages}")

        for i, text in enumerate(page_texts, start=1):
            doc.add(PageBreak(i))
            if i in page_errors:
                doc.fail_unit("page", i, FailureCode.extraction_failed, str(page_errors[i]))
                continue
            doc.blocks.extend(_blocks_from_plain_text(text))

        page_failures = sum(1 for f in doc.units.failed if f.kind == "page")
        if n_pages > 0 and page_failures == n_pages:
            raise CorruptFile(f"all {n_pages} pages of {path.name} failed to parse")

        doc.units.page_count = n_pages
        if any(h.inferred for h in doc.headings()):
            doc.add_warning(WarningCode.inferred_headings, "layout:numbered")
        doc.metadata["scanned_page_fraction"] = fraction
        doc.metadata["is_scanned_pdf"] = fraction >= 0.5
        doc.title = path.stem
        return doc


def _blocks_from_plain_text(text: str) -> list[Block]:
    """Numbering-only heading inference over plain ``extract_text()`` output."""
    blocks: list[Block] = []
    buffer: list[str] = []

    def flush() -> None:
        if not buffer:
            return
        merged = buffer[0]
        for following in buffer[1:]:
            merged = join_hyphenated(merged, following)
        blocks.append(Paragraph(text=repair_ligatures(merged)))
        buffer.clear()

    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            flush()
            continue
        heading = classify_numbered(line)
        if heading:
            flush()
            blocks.append(heading)
        else:
            buffer.append(line)
    flush()
    return blocks
