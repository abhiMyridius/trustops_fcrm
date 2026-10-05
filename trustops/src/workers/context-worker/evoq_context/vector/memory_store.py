"""In-memory ``VectorStore`` — the test double + fixture producer.

Scope & functionality
----------------------
A dependency-free stand-in for S3 Vectors used by ``VECTOR_BACKEND=memory``
(offline tests, ``run_local.py``/``run_vectors_only.py`` without AWS
credentials) and by :meth:`InMemoryVectorStore.dump_fixture`, which
produces the exact shape the MCP's ``evoq_vectors.in_memory.
InMemoryVectorClient.from_fixture`` loads (``AEO/contracts/README.md``
§Index Contract v2 fixture). Ranking is brute-force cosine; the metadata
byte cap and ``_shrink_metadata`` behaviour mirror ``S3VectorsStore`` so a
test exercising both backends sees the same contract enforcement. Filters
support the same Mongo-style operator subset the S3 Vectors backend
declares: ``$eq``, ``$ne``, ``$in``, ``$nin``, ``$and``, ``$or``,
``$exists``, plus the ``{field: value}`` equality shorthand.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from evoq_context.core.observer import ContextObserver
from evoq_context.vector.contract import (
    MAX_METADATA_BYTES,
    shrink_metadata,
)
from evoq_context.vector.interface import (
    StoreCapabilities,
    UpsertResult,
    VectorChunk,
    VectorSearchResult,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from evoq_context.core.config import ContextConfig


@dataclass
class _Record:
    key: str
    embedding: list[float]
    metadata: dict[str, Any] = field(default_factory=dict)


def _cosine_distance(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 1.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 1.0
    return 1.0 - dot / (na * nb)


def _match_clause(meta: Mapping[str, Any], key: str, cond: Any) -> bool:
    if isinstance(cond, Mapping):
        have = key in meta
        value = meta.get(key)
        for op, val in cond.items():
            if op == "$eq" and value != val:
                return False
            if op == "$ne" and value == val:
                return False
            if op == "$in" and value not in (val or []):
                return False
            if op == "$nin" and value in (val or []):
                return False
            if op == "$exists" and bool(val) != have:
                return False
            if op == "$gt" and not (have and value is not None and value > val):
                return False
            if op == "$gte" and not (have and value is not None and value >= val):
                return False
            if op == "$lt" and not (have and value is not None and value < val):
                return False
            if op == "$lte" and not (have and value is not None and value <= val):
                return False
        return True
    return bool(meta.get(key) == cond)


def matches_filter(meta: Mapping[str, Any], filters: Mapping[str, Any]) -> bool:
    """Evaluate a Mongo-style filter (``$and``/``$or`` + per-field ops)."""
    for key, cond in filters.items():
        if key == "$and":
            if not all(matches_filter(meta, f) for f in cond or []):
                return False
        elif key == "$or":
            if not any(matches_filter(meta, f) for f in cond or []):
                return False
        elif not _match_clause(meta, key, cond):
            return False
    return True


class InMemoryVectorStore:
    """Deterministic in-memory backend implementing the ``VectorStore`` protocol."""

    def __init__(self, config: ContextConfig, project: str) -> None:
        self._config = config
        self._project = project
        self._index_name = f"{config.s3v_index_prefix or 'context'}-{project}".lower()
        self._records: dict[str, _Record] = {}

    @property
    def index_name(self) -> str:
        return self._index_name

    def capabilities(self) -> StoreCapabilities:
        return StoreCapabilities(
            supports_filter_delete=True,
            max_metadata_bytes=MAX_METADATA_BYTES,
            max_non_filterable_keys=10,
            max_top_k=1000,
        )

    async def ensure_index(self) -> bool:
        return True

    async def describe(self) -> dict[str, Any]:
        return {
            "backend": "memory",
            "store": "memory",
            "index": self._index_name,
            "region": "",
            "dimension": self._config.embedding_dimension,
            "distance_metric": "cosine",
            "vector_count": len(self._records),
        }

    async def healthcheck(self) -> bool:
        return True

    async def delete_index(self) -> bool:
        self._records.clear()
        return True

    def _shrink_metadata(
        self, metadata: Mapping[str, Any], *, observer: ContextObserver | None = None
    ) -> dict[str, Any]:
        """Same trim strategy as ``S3VectorsStore`` — delegates to
        ``contract.shrink_metadata``. Passing ``observer`` reports a
        ``GuardrailEvent`` whenever a record actually needed trimming.
        """
        return shrink_metadata(metadata, max_bytes=MAX_METADATA_BYTES, observer=observer)

    async def upsert(
        self, chunks: list[VectorChunk], *, observer: ContextObserver | None = None
    ) -> UpsertResult:
        keys: list[str] = []
        for chunk in chunks:
            meta = self._shrink_metadata(chunk.metadata, observer=observer)
            self._records[chunk.chunk_id] = _Record(
                key=chunk.chunk_id, embedding=list(chunk.embedding), metadata=meta,
            )
            keys.append(chunk.chunk_id)
        return UpsertResult(upserted_count=len(keys), keys=keys)

    async def delete_keys(self, keys: list[str]) -> int:
        deleted = 0
        for key in keys:
            if self._records.pop(key, None) is not None:
                deleted += 1
        return deleted

    async def delete_by_doc_id(
        self, doc_id: str, known_keys: list[str] | None = None
    ) -> int:
        if known_keys:
            return await self.delete_keys(known_keys)
        stale = [
            key
            for key, rec in self._records.items()
            if rec.metadata.get("doc_id") == doc_id
        ]
        return await self.delete_keys(stale)

    async def query(
        self,
        embedding: list[float],
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[VectorSearchResult]:
        scored: list[tuple[float, _Record]] = []
        for rec in self._records.values():
            if filters and not matches_filter(rec.metadata, filters):
                continue
            dist = _cosine_distance(embedding, rec.embedding)
            scored.append((dist, rec))
        scored.sort(key=lambda item: (item[0], item[1].key))
        out: list[VectorSearchResult] = []
        for dist, rec in scored[:top_k]:
            out.append(
                VectorSearchResult(
                    chunk_id=rec.key,
                    text=str(rec.metadata.get("text", "")),
                    score=1.0 - dist,
                    distance=dist,
                    metadata=rec.metadata,
                )
            )
        return out

    # ------------------------------------------------------------------
    # Fixture dump (AEO/contracts/fixtures/vectors_fixture.json)
    # ------------------------------------------------------------------

    def dump_fixture(self) -> dict[str, Any]:
        """Serialise every stored record to the shared contract fixture shape.

        Matches ``evoq_vectors.in_memory.InMemoryVectorClient.dump_fixture``
        on the MCP side field-for-field, so either side can load what the
        other wrote.
        """
        return {
            "fixture_version": 1,
            "backend": "memory",
            "store": "memory",
            "index": self._index_name,
            "distance_metric": "cosine",
            "dimension": self._config.embedding_dimension,
            "vectors": [
                {"key": rec.key, "metadata": rec.metadata, "vector": rec.embedding}
                for rec in self._records.values()
            ],
        }
