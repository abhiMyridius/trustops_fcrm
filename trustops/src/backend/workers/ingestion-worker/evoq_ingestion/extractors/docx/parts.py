r"""Everything that is not "plain paragraph/table text" in a ``.docx``.

Scope & functionality
----------------------
* ``paragraph_inline_text`` — the single place that turns one ``w:p``
  element into a string, walking runs *directly from the XML* (never
  ``Paragraph.text``, which only sees direct-child ``w:r``/``w:hyperlink``
  and would silently drop ``w:ins``/``w:del`` content). Tracked changes:
  ``w:ins`` text is kept in place; ``w:del``/``w:delText`` is dropped
  unless ``include_deletions``, in which case it is kept wrapped in
  ``~~...~~``. Hyperlinks resolve to ``[text](url)``/``[text](#anchor)``
  via the owning part's relationships. ``w:footnoteReference`` /
  ``w:endnoteReference`` become an inline ``[^id]`` marker. Side effects
  (images found, text boxes found, whether any tracked-change element was
  seen) are gathered on the ``InlineCollector`` passed in, since a single
  paragraph can contain all of these at once.
* ``read_headers_footers`` — one ``Note`` per distinct, non-empty
  header/footer body across ``document.sections`` (``is_linked_to_previous``
  sections skipped; identical text deduped).
* ``read_footnotes_endnotes`` — the ``FOOTNOTES``/``ENDNOTES`` relationship
  parts (python-docx has no high-level wrapper for either; both are parsed
  from the raw part bytes), skipping the separator/continuation markers at
  ids ``-1``/``0``.
* ``read_comments`` — the ``COMMENTS`` relationship part, when present.
* ``classify_open_error`` — turns a failed ``docx.Document(path)`` call into
  ``CorruptFile`` or ``EncryptedFile`` (OLE/CFB signature => a
  password-protected package, not a corrupt zip).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml.ns import qn
from lxml import etree  # type: ignore[import-untyped]

from evoq_ingestion.extractors.base import CorruptFile, EncryptedFile, ExtractionError
from evoq_ingestion.model.blocks import Note, NoteKind

if TYPE_CHECKING:
    from docx.document import Document as DocxDocument
    from docx.opc.part import Part
    from docx.oxml.xmlchemy import BaseOxmlElement
    from docx.section import _Footer, _Header

_OLE_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_VML_NS = "urn:schemas-microsoft-com:vml"
_SEPARATOR_NOTE_IDS = frozenset({"-1", "0"})


def _vml(local: str) -> str:
    return f"{{{_VML_NS}}}{local}"


@dataclass
class ImageFound:
    """One image reference discovered while walking a paragraph's runs."""

    alt: str
    target: str


@dataclass
class InlineCollector:
    """Accumulates side effects of walking one or more paragraphs' inline content."""

    part: Any  # docx opc Part that owns rId-based relationships (rels)
    include_deletions: bool
    images: list[ImageFound] = field(default_factory=list)
    textbox_notes: list[str] = field(default_factory=list)
    footnote_ids: list[str] = field(default_factory=list)
    endnote_ids: list[str] = field(default_factory=list)
    tracked_change_seen: bool = False


def paragraph_inline_text(p_elem: BaseOxmlElement, collector: InlineCollector) -> str:
    """The rendered text of one ``w:p`` element (recursing hyperlinks/ins/del/sdt)."""
    return "".join(_walk_children(p_elem, collector))


def _walk_children(container: BaseOxmlElement, collector: InlineCollector) -> list[str]:
    parts: list[str] = []
    for child in container:
        tag = etree.QName(child).localname
        parts.extend(_child_text(tag, child, collector))
    return parts


def _child_text(tag: str, child: BaseOxmlElement, collector: InlineCollector) -> list[str]:
    if tag == "r":
        return [_run_text(child, collector)]
    if tag == "hyperlink":
        return [_hyperlink_text(child, collector)]
    if tag == "ins":
        collector.tracked_change_seen = True
        return _walk_children(child, collector)
    if tag == "del":
        return _del_text(child, collector)
    if tag in ("smartTag", "sdtContent"):
        return _walk_children(child, collector)
    if tag == "sdt":
        content = child.find(qn("w:sdtContent"))
        return _walk_children(content, collector) if content is not None else []
    # bookmarkStart/End, proofErr, commentRangeStart/End/Reference, pPr: no text
    return []


def _del_text(del_elem: BaseOxmlElement, collector: InlineCollector) -> list[str]:
    collector.tracked_change_seen = True
    if not collector.include_deletions:
        return []  # dropped entirely, as if the run never existed
    inner = "".join(_walk_children(del_elem, collector))
    return [f"~~{inner}~~"] if inner else []


def _run_text(r_elem: BaseOxmlElement, collector: InlineCollector) -> str:
    return "".join(
        _run_child_text(etree.QName(child).localname, child, collector) for child in r_elem
    )


def _run_child_text(tag: str, child: BaseOxmlElement, collector: InlineCollector) -> str:
    if tag in ("t", "delText"):
        return child.text or ""
    if tag == "tab":
        return "\t"
    if tag in ("br", "cr"):
        return "\n"
    if tag == "noBreakHyphen":
        return "-"
    if tag == "footnoteReference":
        return _note_reference_marker(child, collector.footnote_ids)
    if tag == "endnoteReference":
        return _note_reference_marker(child, collector.endnote_ids)
    if tag in ("drawing", "pict"):
        _handle_drawing(child, collector)
    return ""


def _note_reference_marker(ref_elem: BaseOxmlElement, id_list: list[str]) -> str:
    note_id = ref_elem.get(qn("w:id"))
    if note_id is None or note_id in _SEPARATOR_NOTE_IDS:
        return ""
    id_list.append(note_id)
    return f"[^{note_id}]"


def _hyperlink_text(hyperlink_elem: BaseOxmlElement, collector: InlineCollector) -> str:
    inner = "".join(_walk_children(hyperlink_elem, collector))
    if not inner:
        return ""
    rid = hyperlink_elem.get(qn("r:id"))
    anchor = hyperlink_elem.get(qn("w:anchor"))
    if rid:
        try:
            target = collector.part.rels[rid].target_ref
        except KeyError:
            target = ""
        if target:
            return f"[{inner}]({target})"
    if anchor:
        return f"[{inner}](#{anchor})"
    return inner


def _handle_drawing(drawing_elem: BaseOxmlElement, collector: InlineCollector) -> None:
    _extract_textboxes(drawing_elem, collector)
    alt = _drawing_alt(drawing_elem)
    _extract_drawingml_images(drawing_elem, collector, alt)
    _extract_vml_images(drawing_elem, collector, alt)


def _inside_textbox(elem: BaseOxmlElement) -> bool:
    return any(etree.QName(a).localname == "txbxContent" for a in elem.iterancestors())


def _extract_textboxes(drawing_elem: BaseOxmlElement, collector: InlineCollector) -> None:
    for txbx in drawing_elem.iter(qn("w:txbxContent")):
        joined = _textbox_text(txbx, collector)
        if joined:
            collector.textbox_notes.append(joined)


def _textbox_text(txbx: BaseOxmlElement, collector: InlineCollector) -> str:
    texts: list[str] = []
    for p_elem in txbx.findall(qn("w:p")):
        nested = InlineCollector(part=collector.part, include_deletions=collector.include_deletions)
        text = paragraph_inline_text(p_elem, nested)
        if text.strip():
            texts.append(text)
        collector.images.extend(nested.images)
        collector.footnote_ids.extend(nested.footnote_ids)
        collector.endnote_ids.extend(nested.endnote_ids)
        seen = collector.tracked_change_seen or nested.tracked_change_seen
        collector.tracked_change_seen = seen
    return "\n".join(texts).strip()


def _drawing_alt(drawing_elem: BaseOxmlElement) -> str | None:
    for wrapper_tag in (qn("wp:inline"), qn("wp:anchor")):
        wrapper = drawing_elem.find(wrapper_tag)
        if wrapper is not None:
            doc_pr = wrapper.find(qn("wp:docPr"))
            if doc_pr is not None:
                alt = doc_pr.get("descr") or doc_pr.get("name")
                return str(alt) if alt else None
            return None
    return None


def _extract_drawingml_images(
    drawing_elem: BaseOxmlElement, collector: InlineCollector, alt: str | None
) -> None:
    for blip in drawing_elem.iter(qn("a:blip")):
        if _inside_textbox(blip):
            continue
        rid = blip.get(qn("r:embed")) or blip.get(qn("r:link"))
        _add_image(collector, rid, alt)


def _extract_vml_images(
    drawing_elem: BaseOxmlElement, collector: InlineCollector, alt: str | None
) -> None:
    for imagedata in drawing_elem.iter(_vml("imagedata")):
        if _inside_textbox(imagedata):
            continue
        rid = imagedata.get(qn("r:id"))
        vml_alt = alt
        shape = next(imagedata.iterancestors(_vml("shape")), None)
        if shape is not None:
            vml_alt = shape.get("alt") or vml_alt
        _add_image(collector, rid, vml_alt)


def _add_image(collector: InlineCollector, rid: str | None, alt: str | None) -> None:
    if not rid:
        return
    try:
        target_ref = collector.part.rels[rid].target_ref
    except KeyError:
        return
    basename = Path(target_ref).name or target_ref
    image = ImageFound(alt=alt or basename or "image", target=f"attachment:{basename}")
    collector.images.append(image)


# ---------------------------------------------------------------- headers/footers
def read_headers_footers(document: DocxDocument) -> tuple[list[Note], bool]:
    """One ``Note`` per distinct, non-empty header/footer body across all sections."""
    notes: list[Note] = []
    seen: set[tuple[str, str]] = set()
    tracked_change_seen = False
    for section in document.sections:
        for kind, story in ((NoteKind.header, section.header), (NoteKind.footer, section.footer)):
            if story.is_linked_to_previous:
                continue
            text, tracked = _story_text(story)
            tracked_change_seen = tracked_change_seen or tracked
            if not text.strip():
                continue
            key = (str(kind), text)
            if key in seen:
                continue
            seen.add(key)
            notes.append(Note(kind=kind, text=text))
    return notes, tracked_change_seen


def _story_text(story: _Header | _Footer) -> tuple[str, bool]:
    collector = InlineCollector(part=story.part, include_deletions=False)
    lines = []
    for paragraph in story.paragraphs:
        text = paragraph_inline_text(paragraph._p, collector)  # noqa: SLF001
        if text.strip():
            lines.append(text)
    return "\n".join(lines), collector.tracked_change_seen


# ---------------------------------------------------------------- footnotes/endnotes
def read_footnotes_endnotes(
    document: DocxDocument, *, include_deletions: bool
) -> tuple[list[Note], bool]:
    """``Note`` blocks for every real footnote/endnote (separator ids ``-1``/``0`` skipped)."""
    footnotes, tracked1 = _read_note_part(
        document, RT.FOOTNOTES, "w:footnote", NoteKind.footnote, include_deletions
    )
    endnotes, tracked2 = _read_note_part(
        document, RT.ENDNOTES, "w:endnote", NoteKind.endnote, include_deletions
    )
    return [*footnotes, *endnotes], (tracked1 or tracked2)


def _read_note_part(
    document: DocxDocument,
    reltype: str,
    item_tag: str,
    kind: NoteKind,
    include_deletions: bool,
) -> tuple[list[Note], bool]:
    try:
        part = document.part.part_related_by(reltype)
    except KeyError:
        return [], False
    root = _parsed_root(part)
    if root is None:
        return [], False
    notes: list[Note] = []
    tracked_change_seen = False
    for item in root.findall(qn(item_tag)):
        note_id = item.get(qn("w:id"))
        if note_id is None or note_id in _SEPARATOR_NOTE_IDS:
            continue
        note_type = item.get(qn("w:type"))
        if note_type in ("separator", "continuationSeparator", "continuationNotice"):
            continue
        collector = InlineCollector(part=part, include_deletions=include_deletions)
        lines = [paragraph_inline_text(p_elem, collector) for p_elem in item.findall(qn("w:p"))]
        tracked_change_seen = tracked_change_seen or collector.tracked_change_seen
        text = "\n".join(line for line in lines if line.strip())
        if text.strip():
            notes.append(Note(kind=kind, text=text, ref=note_id))
    return notes, tracked_change_seen


def _parsed_root(part: Part) -> BaseOxmlElement | None:
    element = getattr(part, "element", None)
    if element is not None:
        return cast("BaseOxmlElement", element)
    blob = getattr(part, "blob", None)
    if not blob:
        return None
    try:
        return cast("BaseOxmlElement", etree.fromstring(blob))
    except etree.XMLSyntaxError:
        return None


# ---------------------------------------------------------------- comments
def read_comments(document: DocxDocument) -> list[Note]:
    """One ``Note`` per comment in the document's ``COMMENTS`` part, if present."""
    try:
        comments_part = document.part.part_related_by(RT.COMMENTS)
    except KeyError:
        return []
    comments = getattr(comments_part, "comments", None)
    if comments is None:
        return []
    notes: list[Note] = []
    for comment in comments:
        text = comment.text
        if text and text.strip():
            notes.append(Note(kind=NoteKind.comment, text=text.strip(), ref=comment.author or None))
    return notes


# ---------------------------------------------------------------- open-error classification
def classify_open_error(path: Path, exc: Exception) -> ExtractionError:
    """Map a failed ``docx.Document(path)`` call to ``EncryptedFile`` or ``CorruptFile``."""
    try:
        with open(path, "rb") as handle:  # noqa: PTH123 - short header peek
            head = handle.read(8)
    except OSError:
        head = b""
    if head.startswith(_OLE_SIGNATURE):
        return EncryptedFile(f"{path.name} is a password-protected OOXML package", detail=path.name)
    return CorruptFile(f"{path.name} could not be opened by python-docx: {exc}", detail=str(exc))
