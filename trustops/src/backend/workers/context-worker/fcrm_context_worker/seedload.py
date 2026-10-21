"""Seed the vector index from ``fcrm_seed``'s corpus, with zero AWS dependency.

Under ``VECTOR_BACKEND=memory`` + ``EMBEDDING_PROVIDER=dummy`` (the defaults,
see :mod:`fcrm_context_worker.config`) this gives a demo or test run real,
searchable policy content: every clause the seed carries becomes one
``clause_units`` vector in the ``policy_clauses`` shape design A §6.3
describes. The same code path upserts into S3 Vectors with Bedrock Titan
embeddings when the two switches say so.

What the seed holds (corpus ``v14``): the nine source descriptors and the four
clauses the demo case FCRM-2026-0151 cites, each with its verbatim quote and
``ref``. The other 193 clause texts live in P7's ``clauses.json`` (ADR-29,
generated once by ``fcrm_seed.gen_clauses``, every clause marked synthetic in its
provenance), which :func:`seed_clauses` uses **when it exists** and otherwise
falls back to the four seed clauses, so nothing breaks before the live
generation is committed. Per-clause ``risk_areas`` / ``trigger_types`` /
``jurisdictions`` come from ``clauses.json``; :func:`clause_records` maps them
when a clause dict carries them, and omits the keys otherwise rather than
inventing values.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping
from typing import Any, Optional, Protocol

from evoq_context.core.config import ContextConfig
from evoq_context.llm.client import LLMClient
from evoq_context.vector.contract import (
    INDEX_CONTRACT_VERSION,
    content_hash_of,
    join_list_metadata,
    validate_metadata,
    vector_key,
)
from evoq_context.vector.factory import create_vector_store
from evoq_context.vector.interface import VectorChunk, VectorStore
from fcrm_seed.loader import generated_clauses, load_raw

from fcrm_context_worker.config import FCRM_PROJECT

logger = logging.getLogger(__name__)

SOURCE_TYPE = "policy"
CHUNK_STRATEGY = "clause_units"
INDEX_KIND = "clause"

#: clauses.json list fields -> Index Contract filter keys (comma-joined).
_LIST_FIELDS: tuple[tuple[str, str], ...] = (
    ("risk_areas", "risk_area"),
    ("trigger_types", "trigger_type"),
    ("jurisdictions", "jurisdiction"),
)


class SeedCorpusError(RuntimeError):
    """The seed has no corpus clauses to load."""


def seed_corpus_version() -> str:
    """The corpus version the seed pins (``v14``)."""
    return str(load_raw()["corpus"]["version"])


def seed_clauses() -> list[dict[str, Any]]:
    """The corpus clauses, normalised: ``clauses.json``'s 197 when it exists,
    else the four ``seed.json`` carries (``demo.clauses``).

    Each is a dict with ``{clause_id, source_id, title, text, ref}``; ``text`` is
    the verbatim clause text (Guard 4 compares quotes against it). Clauses from
    ``clauses.json`` also carry ``page``, ``line_start``, ``line_end``,
    ``risk_areas``, ``trigger_types``, ``jurisdictions``, ``quotable`` and
    ``provenance_kind``.
    """
    generated = generated_clauses()
    if generated is not None:
        return [_from_generated(c) for c in generated]
    raw = load_raw()["demo"].get("clauses") or []
    return [
        {
            "clause_id": c["clause_id"],
            "source_id": c["src"],
            "title": c["title"],
            "text": c["quote"],
            "ref": c["ref"],
            "quotable": True,
            "provenance_kind": "seed_verbatim",
        }
        for c in raw
    ]


def _from_generated(clause: Mapping[str, Any]) -> dict[str, Any]:
    """One ``clauses.json`` clause, flattened to what the indexes read."""
    keys = (
        "clause_id",
        "source_id",
        "title",
        "text",
        "ref",
        "page",
        "line_start",
        "line_end",
        "risk_areas",
        "trigger_types",
        "jurisdictions",
        "quotable",
    )
    flat = {key: clause[key] for key in keys}
    flat["provenance_kind"] = clause["provenance"]["kind"]
    return flat


def _doc_id(corpus_version: str, clause_id: str) -> str:
    # Index Contract chunk keys allow [A-Za-z0-9] in the id part only.
    return f"{SOURCE_TYPE}:{re.sub(r'[^A-Za-z0-9]', '', corpus_version + clause_id)}"


def clause_metadata(clause: Mapping[str, Any], corpus_version: str) -> dict[str, Any]:
    """Index Contract v2 metadata for one clause vector (validated)."""
    doc_id = _doc_id(corpus_version, str(clause["clause_id"]))
    content_hash = content_hash_of(str(clause["text"]).encode("utf-8"))
    meta: dict[str, Any] = {
        "doc_id": doc_id,
        "project": FCRM_PROJECT,
        "source_type": SOURCE_TYPE,
        "source_id": str(clause["source_id"]),
        "content_hash": content_hash,
        "chunk_key": vector_key(doc_id, content_hash, 0),
        "chunk_index": 0,
        "offset": 0,
        "chunk_strategy": CHUNK_STRATEGY,
        "type": "markdown",
        "snapshot_version": "v1",
        "index_contract": INDEX_CONTRACT_VERSION,
        "text_truncated": False,
        "text": str(clause["text"]),
        "title": str(clause.get("title", "")),
        "section": str(clause.get("ref", "")),
        "index_kind": INDEX_KIND,
        "corpus_version": corpus_version,
        "clause_id": str(clause["clause_id"]),
    }
    if clause.get("page") is not None:
        meta["page"] = int(clause["page"])
    for source_key, meta_key in _LIST_FIELDS:
        values = clause.get(source_key)
        if values:
            meta[meta_key] = join_list_metadata([str(v) for v in values])
    validate_metadata(meta)
    return meta


def clause_records(
    clauses: Optional[Iterable[Mapping[str, Any]]] = None,
    *,
    corpus_version: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Metadata for every clause (default: the seed's own clauses)."""
    version = corpus_version or seed_corpus_version()
    chosen = list(clauses) if clauses is not None else seed_clauses()
    if not chosen:
        raise SeedCorpusError("seed.json carries no corpus clauses")
    return [clause_metadata(c, version) for c in chosen]


class EmbeddingCache(Protocol):
    """Embeddings kept across restarts, keyed by embedding model and chunk key.

    A chunk key carries the clause text's content hash, so a changed clause
    misses the cache and is embedded again; a different model misses too.
    """

    async def get_many(self, model: str, keys: list[str]) -> dict[str, list[float]]:
        """The cached vectors among ``keys`` for ``model`` (missing keys absent)."""
        ...

    async def put_many(self, model: str, vectors: Mapping[str, list[float]]) -> None:
        """Store ``vectors`` (chunk key -> vector) for ``model``."""
        ...


def embedding_model_key(llm: LLMClient) -> str:
    """What a cached vector is only valid for: provider, model and dimension."""
    return f"{llm.embed_provider}/{llm.embed_model}/{llm.embed_dimension}"


async def _embeddings(
    records: list[dict[str, Any]], llm: LLMClient, cache: Optional[EmbeddingCache]
) -> list[list[float]]:
    """One vector per record: from ``cache`` where present, the rest from ``llm``
    (and then written back to ``cache``)."""
    if cache is None:
        return await llm.embed([r["text"] for r in records])
    model = embedding_model_key(llm)
    keys = [str(r["chunk_key"]) for r in records]
    found = await cache.get_many(model, keys)
    missing = [r for r in records if r["chunk_key"] not in found]
    if missing:
        fresh = await llm.embed([r["text"] for r in missing])
        new = {str(r["chunk_key"]): v for r, v in zip(missing, fresh, strict=True)}
        await cache.put_many(model, new)
        found = {**found, **new}
    logger.info(
        "seedload: %d embedding(s) reused from cache, %d embedded",
        len(records) - len(missing),
        len(missing),
    )
    return [found[k] for k in keys]


async def load_clauses(
    store: VectorStore,
    llm: LLMClient,
    clauses: Optional[Iterable[Mapping[str, Any]]] = None,
    *,
    corpus_version: Optional[str] = None,
    cache: Optional[EmbeddingCache] = None,
) -> int:
    """Embed and upsert clause vectors into ``store``; returns the count.

    With ``cache``, a clause whose embedding is already cached for this model
    is not sent to the embedding provider again.
    """
    records = clause_records(clauses, corpus_version=corpus_version)
    embeddings = await _embeddings(records, llm, cache)
    chunks = [
        VectorChunk(chunk_id=r["chunk_key"], text=r["text"], embedding=e, metadata=r)
        for r, e in zip(records, embeddings, strict=True)
    ]
    await store.ensure_index()
    result = await store.upsert(chunks)
    logger.info(
        "seedload: %d clause vector(s) into %s (corpus %s)",
        result.upserted_count,
        store.index_name,
        records[0]["corpus_version"],
    )
    return result.upserted_count


async def seeded_store(config: ContextConfig) -> tuple[VectorStore, LLMClient]:
    """Build the configured store, load the seed clauses, return store + client."""
    store = create_vector_store(config, FCRM_PROJECT)
    if store is None:
        raise SeedCorpusError("VECTOR_BACKEND=none has no index to seed")
    llm = LLMClient.from_config(config)
    await load_clauses(store, llm)
    return store, llm
