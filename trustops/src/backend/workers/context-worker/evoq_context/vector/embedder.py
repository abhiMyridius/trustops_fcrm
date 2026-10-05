"""Turn one document into embedded children + stored-only parents.

Scope & functionality
----------------------
``DocumentPreparer.prepare`` is the new, streaming-friendly entry point: it
runs a chunking pipeline (``ChunkPipeline`` — see ``ChunkPipelineLike``
below for the exact structural shape this module depends on; lane C-C owns
the concrete class under ``evoq_context/chunking/pipeline.py``), embeds
every **child** (``chunk_level=1``) chunk, and assembles Index-Contract-v2
metadata for children AND the stored-only **parents** (``chunk_level=0``,
level=0 — never embedded, never passed to ``VectorStore.upsert``, written
only to ``chunks/{source_id}.jsonl`` for a small-to-big consumer to fetch
by ``parent_key``). Children occupy ``chunk_index`` ``0..N-1`` and parents
``N..N+P-1`` — the ordering ``ChunkResult.all_chunks`` already guarantees —
so a document's own two-pass key assignment (parents computed first inside
the pipeline, referenced by ``parent_key`` on their children) round-trips
through ``vector_key`` unchanged.

A ``MetadataContractViolation`` raised while assembling one chunk's
metadata is reported to the observer as a ``GuardrailEvent`` (outcome
``"rejected"``) before propagating — ``VectorWriter`` isolates that failure
to this one document, it never aborts the whole batch.

``build_enriched_metadata`` (name/signature kept for
``test_kr_metadata_end_to_end.py``) is unchanged: the document-level
enrichment step (KR passthrough keys, Canonical Document fields, and
whatever structural metadata the chunk carries — including the v2.1
``parent_key``/``chunk_level``/``topic_id`` keys the ``Finalizer``
populates, since this function already copies any int/str/bool/list-valued
structural key through generically).

The legacy ``prepare_chunks`` free function (pre-``DocumentPreparer``, went
through ``chunking.strategies.smart_chunk`` directly) has been deleted:
lane C-F migrated every caller (``pipeline/build.py``, ``pipeline/revert.py``,
and the tests that used it directly) onto ``DocumentPreparer`` and
confirmed no other caller remained.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from evoq_context.core.errors import ChunkLimitExceeded, MetadataContractViolation
from evoq_context.core.limits import MAX_CHUNKS_PER_DOCUMENT
from evoq_context.core.models import ContextItem, JsonValue
from evoq_context.core.observer import ContextObserver, GuardrailEvent, NullObserver
from evoq_context.llm.client import LLMClient
from evoq_context.vector.contract import (
    CHUNK_LEVEL_CHILD,
    CHUNK_LEVEL_PARENT,
    INDEX_CONTRACT_VERSION,
    MAX_META_TEXT_CHARS,
    join_list_metadata,
    validate_metadata,
    vector_key,
)
from evoq_context.vector.interface import StoredChunk, VectorChunk

if TYPE_CHECKING:  # pragma: no cover - typing only
    from evoq_context.chunking.types import ChunkResult
    from evoq_context.core.config import ContextConfig

logger = logging.getLogger(__name__)
_MAX_META_FIELD_CHARS = 512

# Knowledge Repository metadata carried onto every chunk when present, so
# retrieval-time filtering and citation display (FR-KR-02/FR-ANA-02) can use
# it without a second lookup back to the source. Optional/opt-in upstream
# (see utils/context_adapter.py::_map_file_source) — most items won't have
# these yet, that's fine. "doc_id" is deliberately renamed to "kr_doc_id"
# here: meta["doc_id"] above already means something else (the internal
# source_type:source_id compound id used for delete_by_doc_id etc.), and
# this is a *different* id — the Knowledge Repository document identity an
# uploader supplies for versioning.
_KR_METADATA_KEYS = (
    "category",
    "effective_date",
    "expiry_date",
    "jurisdiction",
    "regulations_ref",
    "owner_id",
    "kr_version",
    # TrustOps Index Contract additions (design A §6.4), passed through the same way.
    "corpus_version",
    "trigger_type",
    "risk_area",
    "closed_date",
    "clause_id",
    "index_kind",
)


@runtime_checkable
class ChunkPipelineLike(Protocol):
    """Structural shape ``DocumentPreparer`` depends on.

    Deliberately a ``Protocol``, not an import of the concrete
    ``evoq_context.chunking.pipeline.ChunkPipeline`` (lane C-C, developed in
    parallel with this lane) — any object with this ``run`` method works,
    which is exactly what lets this module's tests pass a small fake
    pipeline instead of the real segmenter/enricher chain.
    """

    def run(
        self,
        text: str,
        *,
        source_type: str,
        source_id: str,
        title: str = "",
        file_path: Path | None = None,
        metadata: Mapping[str, JsonValue] | None = None,
        profile_hints: Mapping[str, JsonValue] | None = None,
    ) -> ChunkResult: ...


@dataclass(slots=True)
class PreparedDocument:
    """One document's chunk/embed outcome, ready for
    ``VectorWriter``/``storage.snapshot.write_chunks_file``.
    """

    doc_id: str
    source_id: str
    strategy: str
    decision_reason: str
    vector_chunks: list[VectorChunk] = field(default_factory=list)
    stored_chunks: list[StoredChunk] = field(default_factory=list)
    manifest_entry: dict[str, Any] = field(default_factory=dict)
    stats: Any = None


def build_enriched_metadata(
    item: ContextItem,
    chunk_index: int,
    structural_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the enriched, document-level metadata dict for one chunk.

    Kept name/signature for ``test_kr_metadata_end_to_end.py`` — callers
    that need the *full* Index Contract v2 shape (``chunk_key``,
    ``snapshot_version``, ...) go through :meth:`DocumentPreparer.prepare`,
    which layers those on top of what this function returns.
    ``structural_meta`` is copied through
    generically — this is how v2.1's ``parent_key``/``chunk_level``/
    ``topic_id`` reach the wire once the segmenter/enricher chain populates
    them, with no change needed here.
    """
    content_hash = item.content_hash
    if content_hash and not content_hash.startswith("sha256:"):
        # Index Contract v2 requires the "sha256:" prefix on the
        # metadata field; ContextItem.content_hash (an internal
        # change-detection hash, computed independently of whatever the
        # Canonical Document's own content_hash carries) is the bare hex
        # digest — prefix it here, at the one place it crosses into
        # vector metadata, rather than changing its internal meaning.
        content_hash = f"sha256:{content_hash}"

    meta: dict[str, Any] = {
        "doc_id": item.id,
        "project": item.project,
        "source_type": item.source_type,
        "source_id": item.source_id,
        "source_path": item.metadata.get("source_path", ""),
        "content_hash": content_hash,
        "title": item.title,
        "ingested_at": item.ingested_at,
        "chunk_index": chunk_index,
        "offset": chunk_index,
    }

    url = item.metadata.get("url")
    if url:
        meta["url"] = url
    source_version = item.metadata.get("source_version")
    if source_version:
        meta["source_version"] = source_version
    labels = item.metadata.get("labels")
    if labels:
        meta["labels"] = (
            join_list_metadata([str(v) for v in labels]) if isinstance(labels, list) else str(labels)
        )
    hierarchy_path = item.metadata.get("hierarchy_path")
    if not hierarchy_path:
        hierarchy = item.metadata.get("hierarchy")
        if isinstance(hierarchy, dict):
            hierarchy_path = hierarchy.get("section_path")
    if hierarchy_path:
        meta["hierarchy_path"] = hierarchy_path

    kr_doc_id = item.metadata.get("doc_id")
    if kr_doc_id is not None:
        meta["kr_doc_id"] = kr_doc_id
    for key in _KR_METADATA_KEYS:
        value = item.metadata.get(key)
        if value is not None:
            meta[key] = value

    if structural_meta:
        for k, v in structural_meta.items():
            if isinstance(v, bool) or isinstance(v, (int, float)):
                meta[k] = v
            elif isinstance(v, str):
                meta[k] = v[:_MAX_META_FIELD_CHARS]
            elif isinstance(v, list):
                meta[k] = ", ".join(str(x) for x in v)[:_MAX_META_FIELD_CHARS]
    return meta


class DocumentPreparer:
    """Chunk (via a ``ChunkPipeline``) -> embed children -> assemble
    Index-Contract-v2 records for children and stored-only parents.
    """

    def __init__(
        self,
        pipeline: ChunkPipelineLike,
        llm: LLMClient,
        config: ContextConfig,
        observer: ContextObserver | None = None,
    ) -> None:
        self._pipeline = pipeline
        self._llm = llm
        self._config = config
        self._observer = observer or NullObserver()

    async def prepare(
        self, item: ContextItem, *, snapshot_version: str
    ) -> PreparedDocument:
        """Chunk, embed and assemble one document's records.

        Raises :class:`~evoq_context.core.errors.ChunkLimitExceeded` when a
        document would need more than
        :data:`~evoq_context.core.limits.MAX_CHUNKS_PER_DOCUMENT` records,
        and :class:`~evoq_context.core.errors.MetadataContractViolation`
        (after emitting a ``GuardrailEvent``) when an assembled record does
        not conform to the Index Contract — both are per-document failures
        for the caller (``VectorWriter``) to isolate, never letting one bad
        document abort the whole write.
        """
        file_path = Path(str(item.metadata.get("source_path", "unknown")))
        profile_hints = item.metadata.get("profile_hints")
        if not isinstance(profile_hints, dict):
            profile_hints = None

        chunk_result = await asyncio.to_thread(
            self._pipeline.run,
            item.body,
            source_type=item.source_type,
            source_id=item.source_id,
            title=item.title,
            file_path=file_path,
            metadata=item.metadata,
            profile_hints=profile_hints,
        )

        doc_id = item.id
        decision = chunk_result.decision
        strategy = getattr(decision, "strategy", "")
        reason = getattr(decision, "reason", "")

        all_chunks = chunk_result.all_chunks
        n_children = len(chunk_result.children)
        if len(all_chunks) > MAX_CHUNKS_PER_DOCUMENT:
            raise ChunkLimitExceeded(
                f"{doc_id} would need {len(all_chunks)} records "
                f"(chunk_key allows at most {MAX_CHUNKS_PER_DOCUMENT})"
            )

        if not all_chunks:
            return PreparedDocument(
                doc_id=doc_id, source_id=item.source_id, strategy=strategy,
                decision_reason=reason, stats=chunk_result.stats,
            )

        child_texts = [sc.text for sc in chunk_result.children]
        vectors = await self._llm.embed(
            child_texts, observer=self._observer, caller=f"vector.embedder:{doc_id}",
        )

        vector_chunks: list[VectorChunk] = []
        stored_chunks: list[StoredChunk] = []
        for i, sc in enumerate(all_chunks):
            is_parent = i >= n_children
            meta = build_enriched_metadata(item, chunk_index=i, structural_meta=sc.metadata)
            chunk_key = vector_key(doc_id, item.content_hash, i)
            text_truncated = len(sc.text) > MAX_META_TEXT_CHARS
            meta.update({
                "chunk_key": chunk_key,
                "snapshot_version": snapshot_version,
                "index_contract": INDEX_CONTRACT_VERSION,
                "text_truncated": text_truncated,
                "text": sc.text[:MAX_META_TEXT_CHARS],
            })
            meta.setdefault(
                "chunk_level", CHUNK_LEVEL_PARENT if is_parent else CHUNK_LEVEL_CHILD,
            )
            try:
                validate_metadata(meta)
            except MetadataContractViolation as exc:
                self._observer.on_guardrail_event(
                    GuardrailEvent(
                        name="metadata_contract", subject=chunk_key,
                        outcome="rejected", detail=str(exc),
                    )
                )
                raise
            if is_parent:
                stored_chunks.append(
                    StoredChunk(key=chunk_key, chunk_index=i, text=sc.text, metadata=meta)
                )
            else:
                vector_chunks.append(
                    VectorChunk(chunk_id=chunk_key, text=sc.text, embedding=vectors[i], metadata=meta)
                )

        manifest_entry = {
            "content_hash": vector_chunks[0].metadata.get("content_hash", "") if vector_chunks
            else (stored_chunks[0].metadata.get("content_hash", "") if stored_chunks else ""),
            "chunk_count": len(vector_chunks),
            "parent_count": len(stored_chunks),
            "keys": [c.chunk_id for c in vector_chunks],
            "chunk_strategy": strategy,
            "index_fingerprint": self._config.index_fingerprint(),
        }

        logger.info(
            "Prepared %d child chunk(s) + %d parent(s) for %s (strategy=%s)",
            len(vector_chunks), len(stored_chunks), doc_id, strategy,
        )
        return PreparedDocument(
            doc_id=doc_id, source_id=item.source_id, strategy=strategy,
            decision_reason=reason, vector_chunks=vector_chunks,
            stored_chunks=stored_chunks, manifest_entry=manifest_entry,
            stats=chunk_result.stats,
        )

