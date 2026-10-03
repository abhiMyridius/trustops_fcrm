"""``HierarchyEnricher`` — document-global heading nesting, not per-page.

Scope & functionality
----------------------
Walks ``doc.index.headings`` (built once over the *whole* document) to find
the heading stack active at a chunk's ``char_start``. Because the index is
document-global, a heading on page 38 and a sub-heading on page 40 nest
correctly (``section_path == "8. Transactions > 8.3 Posting"``) even
across a page/sheet/slide boundary — this is the key fix over the old
per-page ``heading_sections`` call, whose ``parent_map`` reset at every
page marker.

Sets ``section`` (innermost heading text), ``heading_level`` and
``section_path`` (``" > "``-joined ancestor chain, trimmed from the left
with ``"… > "`` once it exceeds ``SECTION_PATH_MAX_CHARS``). Preamble
before any heading gets ``section_path = doc.title`` when a title was
given, else no ``section``/``section_path``/``heading_level`` keys at all.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from evoq_context.core import limits

if TYPE_CHECKING:
    from evoq_context.chunking.index import Heading
    from evoq_context.chunking.types import SourceDocument, StructuredChunk

_TRIM_MARKER = "… > "


def _heading_stack_at(headings: tuple[Heading, ...], position: int) -> list[Heading]:
    """Ancestor chain active at ``position`` (document-global, level-nested)."""
    stack: list[Heading] = []
    for heading in headings:
        if heading.start > position:
            break
        while stack and stack[-1].level >= heading.level:
            stack.pop()
        stack.append(heading)
    return stack


def _trim_left(path: str, max_chars: int) -> str:
    if len(path) <= max_chars:
        return path
    # Trim whole "Segment > " units from the left until it fits, prefixed
    # with the trim marker, so the innermost (most specific) part survives.
    parts = path.split(" > ")
    while len(_TRIM_MARKER + " > ".join(parts)) > max_chars and len(parts) > 1:
        parts.pop(0)
    trimmed = _TRIM_MARKER + " > ".join(parts)
    return trimmed if len(trimmed) <= max_chars else trimmed[-max_chars:]


class HierarchyEnricher:
    """Tags a chunk with its document-global heading stack (see module docstring)."""

    name = "hierarchy"

    def enrich(self, doc: SourceDocument, chunk: StructuredChunk) -> None:
        """Set ``section``/``heading_level``/``section_path`` from the heading stack."""
        stack = _heading_stack_at(doc.index.headings, chunk.char_start)
        if not stack:
            if doc.title:
                chunk.metadata["section_path"] = doc.title
            return
        innermost = stack[-1]
        chunk.metadata["section"] = innermost.text
        chunk.metadata["heading_level"] = innermost.level
        path = " > ".join(h.text for h in stack)
        chunk.metadata["section_path"] = _trim_left(path, limits.SECTION_PATH_MAX_CHARS)
