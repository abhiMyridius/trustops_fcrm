"""The ``Segmenter`` protocol and small helpers every concrete segmenter shares.

Scope & functionality
----------------------
A ``Segmenter`` turns one ``SourceDocument`` into an ordered list of
``Segment``s — structural, parent-candidate units (a section, a page, a
table, a comment window, ...). Exactly one segmenter runs per document
(chosen by the selector, looked up in ``SEGMENTERS``); every concrete
segmenter in this package builds its segments' ``units`` with
``text_ops.split_units``, which already treats a structural marker line
(page/sheet/slide, and — via ``HEADING_RE`` matching
``### Comment N — ...`` — a comment header too) as its own atomic unit, so
no unit from any segmenter ever spans a marker boundary. Segmenters do
**not** run the token packer themselves (that is the pipeline's "pack
children" stage, lane C-C): a segmenter's only job is correct, exact-offset
``Segment`` boundaries.

``window_segment`` is the one place every segmenter builds a ``Segment``
from a ``[start, end)`` range, so the offset/blank-range handling is
written once.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from evoq_context.chunking.text_ops import split_units
from evoq_context.chunking.types import ParentKind, Segment

if TYPE_CHECKING:
    from evoq_context.chunking.budget import ChunkBudget
    from evoq_context.chunking.types import SourceDocument


@runtime_checkable
class Segmenter(Protocol):
    """Picks the structural units one document is packed into chunks from."""

    name: str

    def segment(self, doc: SourceDocument, budget: ChunkBudget) -> list[Segment]:
        """Return this document's ``Segment``s in document order."""
        ...


def window_segment(
    text: str,
    start: int,
    end: int,
    parent_kind: ParentKind,
    *,
    heading_line: str = "",
) -> Segment | None:
    """One ``Segment`` over ``text[start:end]``, or ``None`` when it is blank."""
    body = text[start:end]
    if not body.strip():
        return None
    return Segment(
        start=start,
        end=end,
        text=body,
        kind="prose",
        units=tuple(split_units(body, base=start)),
        heading_line=heading_line,
        parent_kind=parent_kind,
    )
