"""``ParentChildSplitter`` — stored-only structural parents, two-pass keys.

Scope & functionality
----------------------
For each ``(Segment, children)`` pair (a segment is one section/page/sheet/
slide/window — see ``types.Segment`` — and its already-packed, already
hard-max-guarded children), builds zero or more **parent** chunks: the
parent's text is the enclosing segment's *verbatim* source span (heading
line included, since a segment's own ``[start, end)`` already covers it).
A segment whose token count exceeds ``cfg.parent_max_tokens`` (1,500 by
default) is split into several consecutive parents, **aligned at child
boundaries** — a parent's span always starts and ends exactly where a
child's span does, never mid-child. A segment with exactly one child whose
text equals the parent's text produces **no parent** (there is no point
storing a byte-identical duplicate) — that child keeps no ``parent_key``.

Parents get ``level=0`` (``StructuredChunk`` default is ``1``): this marks
them **stored-only, never embedded** — enforcement of that is the
downstream embedder's responsibility (whoever calls it must filter
``level == 0`` out of what gets sent for embedding; this module only
produces the flag).

``ParentChildSplitter`` does **not** assign final numeric keys itself — it
only creates the parent chunks and marks, on every child a parent covers,
which parent (by position in the list this call returns) it belongs to,
via a *temporary* ``chunk.metadata["_parent_ordinal"]`` key. ``Finalizer``
(``enrichers/finalize.py``) is what turns ``_parent_ordinal`` into a real
``parent_key`` in the two-pass ``vector_key(doc_id, content_hash,
chunk_index)`` scheme — see its module docstring — and pops the temporary
key so it never reaches the Index Contract metadata.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from evoq_context.chunking.text_ops import ws_collapse
from evoq_context.chunking.tokens import TokenEstimator
from evoq_context.chunking.types import StructuredChunk
from evoq_context.core.config import ParentChildConfig

if TYPE_CHECKING:
    from evoq_context.chunking.types import Segment

__all__ = ["ParentChildConfig", "ParentChildSplitter"]


class ParentChildSplitter:
    """Builds stored-only structural parents from segments and their children."""

    def __init__(self, cfg: ParentChildConfig, estimator: TokenEstimator) -> None:
        """Bind the splitter to its config and the shared token estimator."""
        self._cfg = cfg
        self._est = estimator

    def split(
        self, segments_with_children: Sequence[tuple[Segment, list[StructuredChunk]]]
    ) -> list[StructuredChunk]:
        """Return the stored-only parents, linking each covered child via ``_parent_ordinal``."""
        parents: list[StructuredChunk] = []
        if not self._cfg.enabled:
            return parents
        for segment, children in segments_with_children:
            if not children:
                continue
            self._split_one(segment, children, parents)
        return parents

    # -- internals ------------------------------------------------------

    def _split_one(
        self,
        segment: Segment,
        children: list[StructuredChunk],
        parents: list[StructuredChunk],
    ) -> None:
        if len(children) == 1 and self._is_duplicate_of_segment(children[0], segment):
            return  # duplicate of its only child — no parent
        tokens = self._est.count(segment.text)
        if tokens <= self._cfg.parent_max_tokens:
            self._emit_parent(segment, segment.start, segment.end, children, parents)
            return
        for group in self._group_by_token_budget(segment, children):
            start = group[0].char_start
            end = group[-1].char_end
            self._emit_parent(segment, start, end, group, parents)

    @staticmethod
    def _is_duplicate_of_segment(child: StructuredChunk, segment: Segment) -> bool:
        """Report whether a parent for this segment would duplicate its only child.

        The common case (heading/paragraph/page/etc. segments): the child's
        own ``[char_start, char_end)`` already covers the whole segment span
        and carries no (or a redundant) ``injected_prefix``, so
        ``child.body`` (prefix stripped) equals the segment's raw text.

        Table segments are different: ``tables.batch_rows`` deliberately
        excludes the header/separator/caption from a batch's
        ``char_start``/``char_end`` (those become ``injected_prefix``
        instead, since they are *re-rendered*, not a verbatim source
        slice — see ``pipeline.py::_pack_table_segment``). So for a
        single-batch table, ``child.body`` alone is only the data rows and
        can never equal ``segment.text`` (header + rows) — but
        ``child.text`` (prefix included) *does* reconstruct the whole
        table, since the re-rendered header/separator is whitespace-
        equivalent to the segment's own. Checking both keeps the original
        (prefix-stripped) comparison for the segments it was written for
        while also catching the table case — found running the shared
        corpus's single-table CSV/XLSX scenarios end to end during final
        integration: every single-batch table document was getting a
        redundant, byte-identical parent the docstring above explicitly
        says should never be stored.
        """
        target = ws_collapse(segment.text)
        return ws_collapse(child.body) == target or ws_collapse(child.text) == target

    def _emit_parent(
        self,
        segment: Segment,
        start: int,
        end: int,
        children: list[StructuredChunk],
        parents: list[StructuredChunk],
    ) -> None:
        rel_start, rel_end = start - segment.start, end - segment.start
        text = segment.text[rel_start:rel_end]
        parent = StructuredChunk(
            text=text,
            char_start=start,
            char_end=end,
            level=0,
            segment_ordinal=children[0].segment_ordinal,
            topic_id=children[0].topic_id,
        )
        ordinal = len(parents)
        parent.metadata["_parent_ordinal"] = ordinal
        for child in children:
            child.metadata["_parent_ordinal"] = ordinal
        parents.append(parent)

    def _group_by_token_budget(
        self, segment: Segment, children: list[StructuredChunk]
    ) -> list[list[StructuredChunk]]:
        """Greedily group consecutive children into token-budgeted parents.

        Grouped by each group's *span text* (not the sum of per-child
        estimates, which double-counts overlap) staying at or under
        ``parent_max_tokens`` — never splitting a child.
        """
        groups: list[list[StructuredChunk]] = []
        current: list[StructuredChunk] = []
        for child in children:
            current.append(child)
            span_text = segment.text[
                current[0].char_start - segment.start : current[-1].char_end - segment.start
            ]
            if len(current) > 1 and self._est.count(span_text) > self._cfg.parent_max_tokens:
                current.pop()
                groups.append(current)
                current = [child]
        if current:
            groups.append(current)
        return groups
