r"""``NumberingResolver`` — real Word list numbering, not a hardcoded ``"1."``.

Scope & functionality
----------------------
Resolves a paragraph's ``(numId, ilvl)`` binding (direct ``w:numPr`` on the
paragraph, or inherited from its style chain), looks up the bound
``abstractNum``'s ``lvl[ilvl]`` for ``numFmt``/``start``, and maintains a
running per-``(numId, ilvl)`` ordinal counter so real numbered lists count
``1, 2, 3`` (not a literal ``"1."`` on every item) with resets whenever a
shallower level advances (a new top-level item restarts every nested
counter under it). ``numFmt == "bullet"`` -> unordered; anything else
(``decimal``, ``lowerLetter``, ``upperRoman``, ...) -> ordered.

Also builds the nested ``ListBlock`` tree for one run of consecutive
paragraphs that share the same ``numId`` (multi-level lists collapse
correctly instead of flattening to one level).

A document with no ``numbering.xml`` part (no lists at all) is the common
case and must not raise — ``part_related_by`` is used directly (never the
auto-vivifying ``numbering_part`` property, which raises
``NotImplementedError`` when it has to create one).
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml.ns import qn

from evoq_ingestion.model.blocks import ListBlock, ListItem

if TYPE_CHECKING:
    from docx.document import Document as DocxDocument
    from docx.oxml.xmlchemy import BaseOxmlElement
    from docx.styles.style import ParagraphStyle
    from docx.text.paragraph import Paragraph

_DEFAULT_FORMAT = ("decimal", 1)


@dataclass(frozen=True, slots=True)
class ListBinding:
    """One paragraph's resolved list membership."""

    num_id: str
    ilvl: int
    ordered: bool
    ordinal: int | None  # None for bullets


@dataclass(slots=True)
class _Entry:
    ilvl: int
    ordered: bool
    ordinal: int | None
    text: str


@dataclass(slots=True)
class _Node:
    text: str
    ordered: bool
    ordinal: int | None
    children: list[_Node] = field(default_factory=list)


class NumberingResolver:
    """Reads ``numbering.xml`` once and hands out ordinals as paragraphs are visited."""

    def __init__(self, document: DocxDocument) -> None:
        """Parse the document's numbering definitions (a no-op when there are none)."""
        self._num_to_abstract: dict[str, str] = {}
        self._abstract_levels: dict[str, dict[int, tuple[str, int]]] = {}
        self._overrides: dict[tuple[str, int], int] = {}
        self._counters: dict[tuple[str, int], int] = {}
        root = self._numbering_root(document)
        if root is not None:
            self._parse(root)

    @staticmethod
    def _numbering_root(document: DocxDocument) -> BaseOxmlElement | None:
        try:
            part = document.part.part_related_by(RT.NUMBERING)
        except KeyError:
            return None
        element = getattr(part, "element", None)
        return element

    def _parse(self, root: BaseOxmlElement) -> None:
        for num_el in root.findall(qn("w:num")):
            self._parse_num(num_el)
        for abstract_el in root.findall(qn("w:abstractNum")):
            self._parse_abstract_num(abstract_el)

    def _parse_num(self, num_el: BaseOxmlElement) -> None:
        num_id = num_el.get(qn("w:numId"))
        abstract_el = num_el.find(qn("w:abstractNumId"))
        if num_id is not None and abstract_el is not None:
            abstract_id = abstract_el.get(qn("w:val"))
            if abstract_id is not None:
                self._num_to_abstract[num_id] = abstract_id
        for override_el in num_el.findall(qn("w:lvlOverride")):
            self._parse_lvl_override(num_id, override_el)

    def _parse_lvl_override(self, num_id: str | None, override_el: BaseOxmlElement) -> None:
        ilvl_str = override_el.get(qn("w:ilvl"))
        start_el = override_el.find(qn("w:startOverride"))
        if num_id is None or ilvl_str is None or start_el is None:
            return
        with contextlib.suppress(TypeError, ValueError):
            self._overrides[(num_id, int(ilvl_str))] = int(start_el.get(qn("w:val")))

    def _parse_abstract_num(self, abstract_el: BaseOxmlElement) -> None:
        abstract_id = abstract_el.get(qn("w:abstractNumId"))
        if abstract_id is None:
            return
        levels: dict[int, tuple[str, int]] = {}
        for lvl_el in abstract_el.findall(qn("w:lvl")):
            ilvl_str = lvl_el.get(qn("w:ilvl"))
            if ilvl_str is None:
                continue
            fmt_el = lvl_el.find(qn("w:numFmt"))
            start_el = lvl_el.find(qn("w:start"))
            num_fmt = fmt_el.get(qn("w:val")) if fmt_el is not None else _DEFAULT_FORMAT[0]
            start = _DEFAULT_FORMAT[1]
            if start_el is not None:
                with contextlib.suppress(TypeError, ValueError):
                    start = int(start_el.get(qn("w:val")))
            levels[int(ilvl_str)] = (num_fmt or _DEFAULT_FORMAT[0], start)
        self._abstract_levels[abstract_id] = levels

    # --------------------------------------------------------------- binding
    def binding_for(self, paragraph: Paragraph) -> ListBinding | None:
        """The ``(numId, ilvl)`` this paragraph belongs to, with its resolved ordinal."""
        direct = self._direct_num_pr(paragraph)
        num_pr = direct if direct is not None else self._style_num_pr(paragraph.style)
        if num_pr is None:
            return None
        num_id_el = num_pr.find(qn("w:numId"))
        if num_id_el is None:
            return None
        num_id = num_id_el.get(qn("w:val"))
        if num_id is None or num_id == "0":  # numId 0 explicitly means "no numbering"
            return None
        ilvl_el = num_pr.find(qn("w:ilvl"))
        try:
            ilvl = int(ilvl_el.get(qn("w:val"))) if ilvl_el is not None else 0
        except (TypeError, ValueError):
            ilvl = 0
        num_fmt, _start = self._format_for(num_id, ilvl)
        ordered = num_fmt != "bullet"
        ordinal = self._next_ordinal(num_id, ilvl) if ordered else None
        return ListBinding(num_id=num_id, ilvl=ilvl, ordered=ordered, ordinal=ordinal)

    @staticmethod
    def _direct_num_pr(paragraph: Paragraph) -> BaseOxmlElement | None:
        p_pr = paragraph._p.find(qn("w:pPr"))  # noqa: SLF001 - oxml access is the point
        return p_pr.find(qn("w:numPr")) if p_pr is not None else None

    @staticmethod
    def _style_num_pr(style: ParagraphStyle | None) -> BaseOxmlElement | None:
        seen: set[int] = set()
        current = style
        while current is not None and id(current.element) not in seen:
            seen.add(id(current.element))
            p_pr = current.element.find(qn("w:pPr"))
            if p_pr is not None:
                num_pr = p_pr.find(qn("w:numPr"))
                if num_pr is not None:
                    return cast("BaseOxmlElement", num_pr)
            current = current.base_style
        return None

    def _format_for(self, num_id: str, ilvl: int) -> tuple[str, int]:
        abstract_id = self._num_to_abstract.get(num_id)
        levels = self._abstract_levels.get(abstract_id or "", {})
        if ilvl in levels:
            fmt, start = levels[ilvl]
        elif levels:
            fmt, start = levels[max(levels)]
        else:
            fmt, start = _DEFAULT_FORMAT
        start = self._overrides.get((num_id, ilvl), start)
        return fmt, start

    def _next_ordinal(self, num_id: str, ilvl: int) -> int:
        for key in [k for k in self._counters if k[0] == num_id and k[1] > ilvl]:
            del self._counters[key]
        key = (num_id, ilvl)
        current = self._counters.get(key)
        if current is None:
            _fmt, start = self._format_for(num_id, ilvl)
            current = start
        else:
            current += 1
        self._counters[key] = current
        return current


def build_list_block(entries: list[tuple[ListBinding, str]]) -> ListBlock:
    """One nested ``ListBlock`` for a run of consecutive same-``numId`` paragraphs."""
    flat = [
        _Entry(ilvl=binding.ilvl, ordered=binding.ordered, ordinal=binding.ordinal, text=text)
        for binding, text in entries
    ]
    roots: list[_Node] = []
    stack: list[tuple[int, list[_Node]]] = [(-1, roots)]
    for entry in flat:
        while stack[-1][0] >= entry.ilvl:
            stack.pop()
        node = _Node(text=entry.text, ordered=entry.ordered, ordinal=entry.ordinal)
        stack[-1][1].append(node)
        stack.append((entry.ilvl, node.children))
    return _freeze(roots)


def _freeze(nodes: list[_Node]) -> ListBlock:
    ordered = nodes[0].ordered
    start = nodes[0].ordinal if nodes[0].ordinal is not None else 1
    items = []
    for node in nodes:
        children: tuple[ListBlock, ...] = ()
        if node.children:
            children = (_freeze(node.children),)
        ordinal = node.ordinal if ordered else None
        items.append(ListItem(text=node.text, children=children, ordinal=ordinal))
    return ListBlock(items=tuple(items), ordered=ordered, start=start)
