"""``Finalizer`` — not a user ``Enricher``; the last pipeline step.

Scope & functionality
----------------------
Runs once, after ``ParentChildSplitter`` and after every chunk (children
*and* parents) has had ``DEFAULT_ENRICHERS`` applied. Assigns:

- ``chunk_strategy``/``strategy_reason`` from the pipeline's
  ``StrategyDecision``.
- ``type`` via ``TypeResolver`` — computed exactly once per document
  (markers present -> ``pdf``/``tabular``/``slides``; else ``source_type``
  -> ``jira``/``confluence``; else the ``file_path`` extension; else
  headings present -> ``markdown``; else ``plain_text``).
- ``char_start``/``char_end``/``token_estimate`` re-derived from the
  chunk's *current* offsets/text (never stale, even after a guard split).
- ``para_group`` (= ``segment_ordinal``), ``topic_id`` (when the chunk has
  one), ``chunk_level`` (from ``chunk.level``).
- The two-pass keys (see ``parent_child.py``): children get
  ``chunk_index`` ``0..N-1`` in document order, parents ``N..N+P-1``; each
  child carrying a ``_parent_ordinal`` (set by ``ParentChildSplitter``)
  gets ``parent_key = vector_key(doc_id, content_hash, N + ordinal)`` — the
  frozen formula from ``vector/contract.py``, never reimplemented. A
  document that would need more than ``limits.MAX_CHUNKS_PER_DOCUMENT``
  records raises ``ChunkLimitExceeded`` before any index is assigned.

**Integration note**: ``content_hash`` is a required constructor argument,
not computed here — ``pipeline.py`` passes
``compute_content_hash(original_text.encode("utf-8"))`` where
``original_text`` is the pipeline's input *before* normalization (the same
bytes an upstream ``ExtractedItem.body`` holds), so ``parent_key`` resolves
to whatever key the embedder (lane C-E) later assembles for the same
document using ``item.content_hash``. See ``pipeline.py``'s module
docstring for the full reasoning.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from evoq_context.chunking.tokens import TokenEstimator
from evoq_context.core import limits
from evoq_context.core.errors import ChunkLimitExceeded
from evoq_context.vector.contract import vector_key

if TYPE_CHECKING:
    from evoq_context.chunking.selector import StrategyDecision
    from evoq_context.chunking.types import SourceDocument, StructuredChunk


class TypeResolver:
    """Computes ``metadata["type"]`` exactly once per document."""

    def __init__(self, doc: SourceDocument) -> None:
        """Bind the resolver to one document; ``resolve()`` memoises the result."""
        self._doc = doc
        self._resolved: str | None = None

    def resolve(self) -> str:
        """Return this document's ``type`` value, computing it on first call."""
        if self._resolved is not None:
            return self._resolved
        doc = self._doc
        kinds = {marker.kind for marker in doc.index.markers}
        if "page" in kinds:
            value = "pdf"
        elif "sheet" in kinds:
            value = "tabular"
        elif "slide" in kinds:
            value = "slides"
        elif doc.source_type == "jira":
            value = "jira"
        elif doc.source_type == "confluence":
            value = "confluence"
        else:
            value = self._from_extension() or self._from_headings()
        self._resolved = value
        return value

    def _from_extension(self) -> str | None:
        ext = self._doc.file_path.suffix.lower() if self._doc.file_path else ""
        if ext == ".pdf":
            return "pdf"
        if ext in (".xlsx", ".xls", ".csv"):
            return "tabular"
        if ext == ".pptx":
            return "slides"
        return None

    def _from_headings(self) -> str:
        return "markdown" if self._doc.index.headings else "plain_text"


class Finalizer:
    """Assigns strategy/type/offsets/topic/parent-key metadata on every chunk."""

    def __init__(
        self,
        doc: SourceDocument,
        decision: StrategyDecision,
        estimator: TokenEstimator,
        *,
        content_hash: str,
    ) -> None:
        """Bind the finalizer to one document/decision.

        See the module docstring for the ``content_hash`` contract.
        """
        self._doc = doc
        self._decision = decision
        self._estimator = estimator
        self._type = TypeResolver(doc).resolve()
        self._doc_id = doc.doc_id
        self._content_hash = content_hash

    def finalize(
        self, children: list[StructuredChunk], parents: list[StructuredChunk]
    ) -> tuple[list[StructuredChunk], list[StructuredChunk]]:
        """Assign the two-pass keys and every derived metadata field in place."""
        total = len(children) + len(parents)
        if total > limits.MAX_CHUNKS_PER_DOCUMENT:
            raise ChunkLimitExceeded(
                f"document {self._doc_id!r} would produce {total} records "
                f"({len(children)} children + {len(parents)} parents), over the "
                f"{limits.MAX_CHUNKS_PER_DOCUMENT}-record chunk_index ceiling"
            )
        for i, chunk in enumerate(children):
            self._finalize_common(chunk, i)
        for j, chunk in enumerate(parents):
            self._finalize_common(chunk, len(children) + j)
        for chunk in children:
            ordinal = chunk.metadata.pop("_parent_ordinal", None)
            if ordinal is not None:
                parent_index = len(children) + int(ordinal)
                chunk.metadata["parent_key"] = vector_key(
                    self._doc_id, self._content_hash, parent_index
                )
        for chunk in parents:
            chunk.metadata.pop("_parent_ordinal", None)
        return children, parents

    def _finalize_common(self, chunk: StructuredChunk, index: int) -> None:
        chunk.metadata["chunk_strategy"] = self._decision.strategy
        chunk.metadata["strategy_reason"] = self._decision.reason
        chunk.metadata["type"] = self._type
        chunk.metadata["char_start"] = chunk.char_start
        chunk.metadata["char_end"] = chunk.char_end
        chunk.metadata["token_estimate"] = self._estimator.count(chunk.text)
        chunk.metadata["para_group"] = chunk.segment_ordinal
        chunk.metadata["chunk_level"] = chunk.level
        chunk.metadata["chunk_index"] = index
        chunk.metadata["offset"] = index
        if chunk.topic_id:
            chunk.metadata["topic_id"] = chunk.topic_id
