r"""``StyleResolver`` — locale-independent Word style -> heading level.

Scope & functionality
----------------------
Word's built-in paragraph style *ids* (``Heading1``..``Heading9``, ``Title``,
``Subtitle``) are stable across locales; only the human-readable style
*name* is translated (``"berschrift 1"``, ``"Titre"``, ...). ``StyleResolver``
matches the id first (fast path, works for every English-authored document
and every document python-docx itself produces), then falls back to a
normalised (accent-stripped, case-folded) name match plus a small alias
table for the handful of European locales most likely to show up, so we
are not hardcoding every Word locale's translation table.

``w:outlineLvl`` (direct on the paragraph, or inherited from its style
chain) is a second, style-name-independent signal and is tried when no
style match is found — this is how a custom "Section Title" style that
nobody named "Heading" still produces a real heading level.

Results are cached per style id: the walk is O(distinct styles), not
O(paragraphs), so this stays cheap even on a 5,000-paragraph document.
"""

from __future__ import annotations

import re
import unicodedata
from typing import TYPE_CHECKING, NamedTuple, cast

from docx.oxml.ns import qn

if TYPE_CHECKING:
    from docx.document import Document as DocxDocument
    from docx.oxml.xmlchemy import BaseOxmlElement
    from docx.styles.style import ParagraphStyle
    from docx.text.paragraph import Paragraph

_HEADING_ID_RE = re.compile(r"^heading([1-9])$")
_HEADING_NAME_RE = re.compile(r"^(heading|berschrift|titre|titulo|titolo|zagolovok)\s*([1-9])$")
_TITLE_ALIASES = frozenset({"title", "titel", "titre", "titulo", "titolo"})
_SUBTITLE_ALIASES = frozenset(
    {"subtitle", "untertitel", "sous-titre", "soustitre", "subtitulo", "sottotitolo"}
)
_MAX_LEVEL = 6


class HeadingMatch(NamedTuple):
    """A resolved heading level plus how it was found."""

    level: int
    source: str  # "style" | "outline"


def _strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _normalise(text: str) -> str:
    return _strip_accents(text).strip().lower()


def _style_key(style: ParagraphStyle) -> str:
    """A stable per-style cycle-detection key (``style_id``, falling back to ``name``)."""
    return style.style_id or style.name or str(id(style))


def _direct_p_pr(p_elem: BaseOxmlElement) -> BaseOxmlElement | None:
    return cast("BaseOxmlElement | None", p_elem.find(qn("w:pPr")))


def _outline_level_of(p_pr: BaseOxmlElement | None) -> int | None:
    if p_pr is None:
        return None
    node = p_pr.find(qn("w:outlineLvl"))
    if node is None:
        return None
    try:
        return int(node.get(qn("w:val")))
    except (TypeError, ValueError):
        return None


class StyleResolver:
    """Resolve a paragraph's heading level from its style chain or ``outlineLvl``."""

    def __init__(self, document: DocxDocument) -> None:
        """Bind to *document* (read-only; nothing is cached across documents)."""
        self._document = document
        self._style_cache: dict[str, HeadingMatch | None] = {}
        self._outline_cache: dict[str, int | None] = {}

    def heading_for(self, paragraph: Paragraph) -> HeadingMatch | None:
        """The heading level for *paragraph*, or ``None`` when it is not a heading."""
        style = paragraph.style
        via_style = self._level_for_style(style) if style is not None else None
        if via_style is not None:
            return via_style
        outline = _outline_level_of(_direct_p_pr(paragraph._p))  # noqa: SLF001 - oxml access is the point
        if outline is None:
            outline = self._outline_for_style(style) if style is not None else None
        if outline is not None:
            return HeadingMatch(level=min(outline + 1, _MAX_LEVEL), source="outline")
        return None

    # ------------------------------------------------------------- internals
    # NOTE: cached by ``style_id`` (a stable string), never by ``id(style.element)``
    # -- lxml recycles its Python proxy objects for a given C node, so two
    # *different* styles accessed at different times can (and did, empirically)
    # briefly share the same ``id()`` once the earlier proxy is garbage
    # collected, corrupting the cache with another style's result.
    def _level_for_style(self, style: ParagraphStyle | None) -> HeadingMatch | None:
        if style is None:
            return None
        key = style.style_id or ""
        if key in self._style_cache:
            return self._style_cache[key]
        result = self._walk_style_for_level(style)
        self._style_cache[key] = result
        return result

    def _walk_style_for_level(self, style: ParagraphStyle | None) -> HeadingMatch | None:
        seen: set[str] = set()
        current = style
        while current is not None and _style_key(current) not in seen:
            seen.add(_style_key(current))
            style_id = _normalise(current.style_id or "")
            name = _normalise(current.name or "")
            match = _HEADING_ID_RE.match(style_id) or _HEADING_NAME_RE.match(name)
            if match:
                digits = match.group(match.lastindex) if match.lastindex else match.group(1)
                return HeadingMatch(level=min(int(digits), _MAX_LEVEL), source="style")
            if style_id == "title" or name in _TITLE_ALIASES:
                return HeadingMatch(level=1, source="style")
            if style_id == "subtitle" or name in _SUBTITLE_ALIASES:
                return HeadingMatch(level=2, source="style")
            current = current.base_style
        return None

    def _outline_for_style(self, style: ParagraphStyle | None) -> int | None:
        key = style.style_id or "" if style is not None else ""
        if key in self._outline_cache:
            return self._outline_cache[key]
        result = self._walk_style_for_outline(style)
        self._outline_cache[key] = result
        return result

    @staticmethod
    def _walk_style_for_outline(style: ParagraphStyle | None) -> int | None:
        seen: set[str] = set()
        current = style
        while current is not None and _style_key(current) not in seen:
            seen.add(_style_key(current))
            level = _outline_level_of(current.element.find(qn("w:pPr")))
            if level is not None:
                return level
            current = current.base_style
        return None
