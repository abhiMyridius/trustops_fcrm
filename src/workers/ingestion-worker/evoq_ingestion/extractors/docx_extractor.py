r"""DOCX extractor — ``python-docx``, driven directly off the OOXML tree.

Scope & functionality
----------------------
``DocxExtractor`` walks ``document.element.body.iterchildren()`` once, in
document order, building ``Paragraph``/``Heading``/``ListBlock``/``Table``
blocks straight from each child element (an ``id(oxml_element) ->
python-docx wrapper`` map is never built — a ``Paragraph`` wrapper is
constructed directly from the child, which is O(1), replacing the old
``next(p for p in document.paragraphs if p._p is child)`` O(n^2) scan).

Headings come from ``docx.styles.StyleResolver`` (locale-independent style
id / ``outlineLvl`` chain) first; failing that, a numbered-line pattern
(``"1.2 Scope"``) infers a heading by dotted depth, then a short,
fully-bold paragraph with no terminal punctuation is the last-resort
heuristic — both fallbacks only run when ``docx_infer_headings`` is set and
both record ``inferred_headings:<source>``.

List paragraphs (direct or style-inherited ``w:numPr``) are buffered and
flushed as one nested ``ListBlock`` per run of consecutive same-``numId``
paragraphs (``docx.numbering.NumberingResolver``). Tables are built from
the raw ``w:tbl`` XML (``docx.tables.build_table``) so merged cells are
spanned, not duplicated. Headers/footers, footnotes/endnotes and comments
(``docx.parts``) are appended as ``Note`` blocks; text boxes are emitted
right after their containing paragraph; hyperlinks/images/tracked changes
are handled inline by the same paragraph walker used everywhere else.

Per-table degradation: a table whose XML cannot be parsed is recorded as a
``UnitFailure`` and skipped — one malformed table does not fail the whole
document. A ``.docx`` that python-docx cannot open at all is classified as
``CorruptFile`` (bad zip/XML) or ``EncryptedFile`` (OLE/CFB signature —
Word's password-protected container format).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import docx
from docx.opc.exceptions import OpcError
from docx.text.paragraph import Paragraph as DocxParagraph
from lxml import etree  # type: ignore[import-untyped]

from evoq_ingestion.core.codes import WarningCode
from evoq_ingestion.core.markdown_scan import NUMBERED_HEADING_RE
from evoq_ingestion.extractors.base import BaseExtractor, EngineInfo, ExtractionError
from evoq_ingestion.extractors.docx.numbering import (
    ListBinding,
    NumberingResolver,
    build_list_block,
)
from evoq_ingestion.extractors.docx.parts import (
    InlineCollector,
    classify_open_error,
    paragraph_inline_text,
    read_comments,
    read_footnotes_endnotes,
    read_headers_footers,
)
from evoq_ingestion.extractors.docx.styles import StyleResolver
from evoq_ingestion.extractors.docx.tables import build_table
from evoq_ingestion.model.blocks import Heading, ImageRef, Note, NoteKind, Paragraph
from evoq_ingestion.model.document import AttachmentRef, Document

if TYPE_CHECKING:
    from docx.document import Document as DocxDocument
    from docx.oxml.xmlchemy import BaseOxmlElement

    from evoq_ingestion.core.config import ExtractionOptions
    from evoq_ingestion.core.limits import Deadline
    from evoq_ingestion.extractors.gate import FileFacts

_MAX_INFERRED_HEADING_WORDS = 12
_TERMINAL_PUNCTUATION = (".", ",", ";", ":")


class DocxExtractor(BaseExtractor):
    """``python-docx`` engine; ``supports = {'.docx'}``."""

    info = EngineInfo("python-docx", "2")
    supports = frozenset({".docx"})

    def classify_error(self, exc: Exception, path: Path) -> ExtractionError:
        """Bad zip / malformed XML -> ``CorruptFile``; OLE/CFB signature -> ``EncryptedFile``."""
        return classify_open_error(path, exc)

    def _parse(self, path: Path, facts: FileFacts, deadline: Deadline) -> Document:  # noqa: ARG002
        try:
            docx_document = docx.Document(str(path))
        except (OpcError, etree.XMLSyntaxError, KeyError, OSError, ValueError) as exc:
            raise self.classify_error(exc, path) from exc

        doc = Document(title=path.stem)
        builder = _DocxBuilder(
            document=docx_document, options=self.options, doc=doc, deadline=deadline
        )
        builder.run()
        return doc


class _DocxBuilder:
    """Stateful, single-use walk of one ``docx.Document`` into a model ``Document``."""

    def __init__(
        self,
        *,
        document: DocxDocument,
        options: ExtractionOptions,
        doc: Document,
        deadline: Deadline,
    ) -> None:
        self._document = document
        self._options = options
        self._doc = doc
        self._deadline = deadline
        self._part = document.part
        self._style_resolver = StyleResolver(document)
        self._numbering_resolver = NumberingResolver(document)
        self._pending_list: list[tuple[ListBinding, str]] = []
        self._table_index = 0
        self._tracked_change_seen = False
        self._used_inference: set[str] = set()

    def run(self) -> None:
        """Walk the body once, then append headers/footers/footnotes/comments."""
        for child in self._document.element.body.iterchildren():
            tag = etree.QName(child).localname
            if tag == "p":
                self._handle_paragraph(child)
            elif tag == "tbl":
                self._flush_list()
                self._handle_table(child)
            # sectPr and anything else at body level carries no content
        self._flush_list()
        self._append_parts()
        for source in self._used_inference:
            self._doc.add_warning(WarningCode.inferred_headings, source)
        if self._tracked_change_seen:
            self._doc.add_warning(WarningCode.tracked_changes_present)

    # ------------------------------------------------------------- paragraphs
    def _handle_paragraph(self, p_elem: BaseOxmlElement) -> None:
        paragraph = DocxParagraph(p_elem, self._document)
        collector = InlineCollector(
            part=self._part, include_deletions=self._options.docx_include_deletions
        )
        text = paragraph_inline_text(p_elem, collector)
        self._tracked_change_seen = self._tracked_change_seen or collector.tracked_change_seen

        if not text.strip():
            self._flush_extras(collector)
            return

        heading = self._resolve_heading(paragraph, text)
        if heading is not None:
            self._flush_list()
            self._doc.add(heading)
            self._flush_extras(collector)
            return

        binding = self._numbering_resolver.binding_for(paragraph)
        if binding is not None:
            if self._pending_list and self._pending_list[-1][0].num_id != binding.num_id:
                self._flush_list()
            self._pending_list.append((binding, text))
            self._flush_extras(collector)
            return

        self._flush_list()
        self._doc.add(Paragraph(text=text))
        self._flush_extras(collector)

    def _resolve_heading(self, paragraph: DocxParagraph, text: str) -> Heading | None:
        match = self._style_resolver.heading_for(paragraph)
        if match is not None:
            return Heading(level=match.level, text=text, inferred=False, source=match.source)
        if not self._options.docx_infer_headings:
            return None
        numbered = NUMBERED_HEADING_RE.match(text)
        if numbered:
            self._used_inference.add("numbered")
            return Heading(level=_dotted_depth(text), text=text, inferred=True, source="numbering")
        if _is_bold_short(paragraph, text):
            self._used_inference.add("bold_short")
            return Heading(level=2, text=text, inferred=True, source="bold_short")
        return None

    def _flush_extras(self, collector: InlineCollector) -> None:
        for image in collector.images:
            self._doc.add(ImageRef(alt=image.alt, target=image.target))
            self._doc.attachments.append(AttachmentRef(title=image.alt, url=image.target))
        for note_text in collector.textbox_notes:
            self._doc.add(Note(kind=NoteKind.textbox, text=note_text))

    def _flush_list(self) -> None:
        if not self._pending_list:
            return
        self._doc.add(build_list_block(self._pending_list))
        self._pending_list = []

    # ------------------------------------------------------------------ tables
    def _handle_table(self, tbl_elem: BaseOxmlElement) -> None:
        self._table_index += 1
        index = self._table_index
        self._deadline.check()
        try:
            table, tracked = build_table(
                tbl_elem, self._part, include_deletions=self._options.docx_include_deletions
            )
        except Exception as exc:  # noqa: BLE001 - one bad table must not fail the document
            self._doc.fail_unit("table", index, WarningCode.extraction_failed, str(exc))
            return
        self._tracked_change_seen = self._tracked_change_seen or tracked
        self._doc.add(table)

    # -------------------------------------------------------------- doc parts
    def _append_parts(self) -> None:
        header_footer_notes, hf_tracked = read_headers_footers(self._document)
        self._tracked_change_seen = self._tracked_change_seen or hf_tracked
        for note in header_footer_notes:
            self._doc.add(note)

        note_blocks, notes_tracked = read_footnotes_endnotes(
            self._document, include_deletions=self._options.docx_include_deletions
        )
        self._tracked_change_seen = self._tracked_change_seen or notes_tracked
        for note in note_blocks:
            self._doc.add(note)

        for note in read_comments(self._document):
            self._doc.add(note)


def _dotted_depth(text: str) -> int:
    prefix = text.split(None, 1)[0] if text else ""
    prefix = prefix.rstrip(".")
    segments = [s for s in prefix.split(".") if s]
    return min(max(len(segments), 1), 6)


def _is_bold_short(paragraph: DocxParagraph, text: str) -> bool:
    words = text.split()
    if not words or len(words) > _MAX_INFERRED_HEADING_WORDS:
        return False
    if text.endswith(_TERMINAL_PUNCTUATION):
        return False
    runs = [r for r in paragraph.runs if r.text.strip()]
    if not runs:
        return False
    return all(r.bold for r in runs)


__all__ = ["DocxExtractor"]
