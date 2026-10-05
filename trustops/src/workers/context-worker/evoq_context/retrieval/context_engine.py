"""Context engine — high-level + deep context retrieval (async).

Scope & functionality
----------------------
Retrieval is enriched, not re-ranked: ``get_deep_context`` returns
whatever order the backend's ``query()`` already returns (similarity
descending), and matched chunks keep that exact relevance order in the
final result — no downstream ``sorted(...)`` by ``doc_id``/``chunk_index``
that used to silently discard it. Sibling chunks from the same document
section are then *appended* (not interleaved) for extra topic context,
each tagged ``is_match=False``, ``sibling_of`` (the matched ``chunk_id``
it's adjacent to) and ``sibling_distance`` (its ``chunk_index`` distance
from that match) — fetched with a ``chunk_index`` range filter when the
backend supports it. ``search`` is the text-in/text-out entry point: it
embeds the query via the same ``LLMClient`` the pipeline uses and returns
a :class:`ContextResult`, exported from ``context/run.py``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from evoq_context.core.config import ContextConfig
from evoq_context.vector.contract import validate_filter_keys
from evoq_context.vector.interface import VectorSearchResult, VectorStore

if TYPE_CHECKING:  # pragma: no cover - typing only
    from evoq_context.llm.client import LLMClient
    from evoq_context.storage.layout import SnapshotLayout

logger = logging.getLogger(__name__)


@dataclass
class ContextResult:
    """Combined context returned to the caller / agent."""

    high_level: str
    retrieved_chunks: list[dict[str, Any]] = field(default_factory=list)


def _read_context_md_sync(project: str, config: ContextConfig) -> str:
    md_path = config.resolve_context_md_path(project)
    if not md_path.exists():
        return ""
    return md_path.read_text(encoding="utf-8")


async def get_high_level_context(project: str, config: ContextConfig) -> str:
    """Read the project's context.md (non-blocking)."""
    return await asyncio.to_thread(_read_context_md_sync, project, config)


def scoped_filters(project: str, filters: dict[str, Any] | None) -> dict[str, Any]:
    """Merge caller ``filters`` with the project scope (TrustOps addition).

    Keys are checked against the Index Contract's filterable set first
    (:func:`~evoq_context.vector.contract.validate_filter_keys`), so a typo
    raises instead of silently matching nothing. The project scope always
    wins: a caller cannot widen a search to another project via ``filters``.
    """
    if not filters:
        return {"project": project}
    validate_filter_keys(filters)
    return {**filters, "project": project}


async def get_deep_context(
    project: str,
    query_embedding: list[float],
    vector_store: VectorStore,
    top_k: int = 5,
    filters: dict[str, Any] | None = None,
) -> list[VectorSearchResult]:
    """Query the vector store for relevant chunks scoped to a project.

    ``filters`` (TrustOps addition) narrows the query with metadata
    conditions in the in-memory store's syntax (``{key: value}`` or
    ``{key: {"$eq"|"$in"|"$gte"|"$lte": ...}}``, ``$and``/``$or``), merged
    with — never replacing — the project scope. Returns exactly the
    backend's own ordering (similarity descending).
    """
    return await vector_store.query(
        embedding=query_embedding, top_k=top_k, filters=scoped_filters(project, filters),
    )


def _hit_to_dict(
    r: VectorSearchResult,
    *,
    is_match: bool = True,
    sibling_of: str | None = None,
    sibling_distance: int | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "chunk_id": r.chunk_id,
        "text": r.text,
        "score": r.score,
        "distance": r.distance,
        "metadata": r.metadata,
        "is_match": is_match,
    }
    if sibling_of is not None:
        out["sibling_of"] = sibling_of
    if sibling_distance is not None:
        out["sibling_distance"] = sibling_distance
    return out


async def _enrich_with_siblings(
    hits: list[VectorSearchResult],
    query_embedding: list[float],
    vector_store: VectorStore,
    project: str,
    max_siblings: int = 3,
) -> list[dict[str, Any]]:
    """Matches keep the backend's relevance order; siblings are appended
    after, never interleaved or re-sorted in with the matches.
    """
    matches = [_hit_to_dict(r, is_match=True) for r in hits]
    seen_ids: set[str] = {m["chunk_id"] for m in matches}
    siblings: list[dict[str, Any]] = []
    doc_ids_done: set[str] = set()

    for r in hits:
        meta = r.metadata
        doc_id = meta.get("doc_id", "")
        chunk_index = meta.get("chunk_index")
        if not doc_id or chunk_index is None or doc_id in doc_ids_done:
            continue
        doc_ids_done.add(doc_id)
        min_idx = max(0, chunk_index - max_siblings)
        max_idx = chunk_index + max_siblings

        try:
            neighbors = await vector_store.query(
                embedding=query_embedding,
                top_k=(max_siblings * 2) + 5,
                filters={
                    "doc_id": doc_id,
                    "project": project,
                    "chunk_index": {"$gte": min_idx, "$lte": max_idx},
                },
            )
        except Exception:
            logger.debug("Sibling fetch failed for %s", doc_id)
            continue

        for sr in neighbors:
            if sr.chunk_id in seen_ids:
                continue
            seen_ids.add(sr.chunk_id)
            sr_idx = sr.metadata.get("chunk_index", chunk_index)
            siblings.append(_hit_to_dict(
                sr, is_match=False, sibling_of=r.chunk_id,
                sibling_distance=abs(sr_idx - chunk_index),
            ))

    return matches + siblings


async def get_context(
    project: str,
    config: ContextConfig,
    vector_store: VectorStore | None = None,
    query_embedding: list[float] | None = None,
    include_high_level: bool = True,
    top_k: int = 5,
    enrich_siblings: bool = True,
    max_siblings: int = 3,
    filters: dict[str, Any] | None = None,
) -> ContextResult:
    """Combined entry point: high-level context.md + optional deep search.

    Returns a ContextResult and swallows backend failures; the one thing it
    raises (TrustOps addition) is ``MetadataContractViolation`` for a filter
    naming a non-filterable key — a caller bug, checked before any I/O.
    """
    if filters:
        validate_filter_keys(filters)
    high_level = ""
    if include_high_level:
        try:
            high_level = await get_high_level_context(project, config)
        except Exception:
            logger.exception("Failed to read context.md for %s", project)

    chunks: list[dict[str, Any]] = []
    if vector_store and query_embedding:
        try:
            results = await get_deep_context(
                project, query_embedding, vector_store, top_k, filters=filters,
            )
        except Exception:
            logger.exception("Deep context query failed for %s", project)
            results = []

        if enrich_siblings and results:
            try:
                chunks = await _enrich_with_siblings(
                    results, query_embedding, vector_store, project, max_siblings,
                )
            except Exception:
                logger.exception("Sibling enrichment failed for %s", project)
                chunks = [_hit_to_dict(r) for r in results]
        else:
            chunks = [_hit_to_dict(r) for r in results]

    return ContextResult(high_level=high_level, retrieved_chunks=chunks)


async def search(
    project: str,
    query_text: str,
    config: ContextConfig,
    llm: LLMClient,
    vector_store: VectorStore | None,
    *,
    top_k: int = 5,
    include_high_level: bool = False,
    enrich_siblings: bool = True,
    max_siblings: int = 3,
    filters: dict[str, Any] | None = None,
) -> ContextResult:
    """Text-in/text-out retrieval: embeds ``query_text`` via ``llm`` (the
    same client the pipeline uses to embed chunks) then delegates to
    :func:`get_context`.
    """
    if filters:
        validate_filter_keys(filters)  # fail before spending an embedding call
    query_embedding: list[float] | None = None
    if vector_store is not None and query_text.strip():
        try:
            vectors = await llm.embed([query_text])
            query_embedding = vectors[0] if vectors else None
        except Exception:
            logger.exception("Failed to embed query for search(project=%s)", project)

    return await get_context(
        project, config, vector_store=vector_store, query_embedding=query_embedding,
        include_high_level=include_high_level, top_k=top_k,
        enrich_siblings=enrich_siblings, max_siblings=max_siblings, filters=filters,
    )


# ---------------------------------------------------------------------------
# expand_parents — Index Contract v2.1 small-to-big (structural parents)
# ---------------------------------------------------------------------------


def _find_parent_text_in_file(path: Path, wanted_keys: set[str]) -> dict[str, str]:
    """Scan one ``chunks/{source_id}.jsonl`` for the given keys (sync — run
    via ``asyncio.to_thread``). A single pass finds every wanted key in that
    one file, so this is at most one file open per distinct
    ``(project, source_id, snapshot_version)`` a batch of hits touches.
    """
    found: dict[str, str] = {}
    if not path.exists() or not wanted_keys:
        return found
    with path.open("r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except Exception:
                continue
            key = record.get("key")
            if key in wanted_keys:
                found[key] = str(record.get("text", ""))
                if len(found) == len(wanted_keys):
                    break
    return found


async def _parent_text_via_store(
    store: VectorStore, parent_key: str
) -> str | None:
    """Fallback when no local snapshot ``layout`` is available: children of
    ``parent_key`` are themselves embedded (``chunk_level=1``) and carry
    ``parent_key`` in their metadata, so filtering by it and concatenating
    the results in ``chunk_index`` order reconstructs the parent's text —
    the parent record itself is never a vector (stored-only), so there is
    nothing to fetch by key alone. One ``query`` call per distinct parent.
    """
    try:
        # The embedding only ranks; every child of one parent is wanted
        # regardless of similarity, so an empty query vector is used
        # purely to carry the metadata filter (VectorSearchResult does not
        # expose the stored embedding to re-use one from a hit).
        results = await store.query(
            embedding=[], top_k=64, filters={"parent_key": {"$eq": parent_key}},
        )
    except Exception:
        logger.warning("expand_parents: store.query fallback failed for parent_key=%s", parent_key)
        return None
    if not results:
        return None
    ordered = sorted(results, key=lambda r: r.metadata.get("chunk_index", 0))
    return "\n\n".join(r.text for r in ordered if r.text)


async def expand_parents(
    hits: list[VectorSearchResult],
    store: VectorStore | None,
    layout: SnapshotLayout | None = None,
) -> list[dict[str, Any]]:
    """Attach each matched child's structural parent text (Index Contract
    v2.1 small-to-big), preserving the hits' original relevance order.

    Hits are grouped by ``parent_key`` (hits with none are left alone);
    ONE lookup happens per **distinct** parent, not per hit — via a single
    scan of that document's ``chunks/{source_id}.jsonl`` when ``layout`` is
    given (the parent record is right there, stored-only), or one
    ``store.query`` filtered by ``parent_key`` reconstructing the parent
    from its children when it is not. Parents never get their own
    similarity score — they are context for the matched children, appended
    to each hit's dict as ``parent_key``/``parent_text``, never reordering
    or replacing the matches themselves.
    """
    ordered = [_hit_to_dict(r, is_match=True) for r in hits]
    if store is None and layout is None:
        return ordered

    # Group by (project, source_id, snapshot_version) so a snapshot lookup
    # opens each contributing document's chunks file at most once.
    by_file: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for r in hits:
        parent_key = r.metadata.get("parent_key")
        if not parent_key:
            continue
        project = str(r.metadata.get("project", ""))
        source_id = str(r.metadata.get("source_id", ""))
        version = str(r.metadata.get("snapshot_version", ""))
        if project and source_id and version:
            by_file[(project, source_id, version)].add(str(parent_key))

    parent_text: dict[str, str] = {}
    if layout is not None:
        for (project, source_id, version), keys in by_file.items():
            path = layout.chunks_path(project, source_id, version=version)
            try:
                found = await asyncio.to_thread(_find_parent_text_in_file, path, keys)
            except Exception:
                logger.warning(
                    "expand_parents: failed reading %s for parent lookup", path,
                )
                found = {}
            parent_text.update(found)

    missing = {k for keys in by_file.values() for k in keys} - set(parent_text)
    if missing and store is not None:
        for parent_key in missing:
            text = await _parent_text_via_store(store, parent_key)
            if text is not None:
                parent_text[parent_key] = text

    for hit_dict, r in zip(ordered, hits):
        parent_key = r.metadata.get("parent_key")
        if parent_key and parent_key in parent_text:
            hit_dict["parent_key"] = parent_key
            hit_dict["parent_text"] = parent_text[parent_key]

    return ordered
