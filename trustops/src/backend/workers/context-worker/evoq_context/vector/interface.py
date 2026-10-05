"""Vector store abstraction — the producer side of the Index Contract v2.

Scope & functionality
----------------------
``VectorStore`` is a structural ``Protocol`` every backend (S3 Vectors,
in-memory, ...) implements; nothing in this module knows which backend is
in use — see ``evoq_context/vector/factory.py`` for that wiring. ``VectorChunk``
is one chunk to upsert; ``VectorSearchResult`` is one query hit with
similarity already converted at the query edge (``score``) alongside the
raw ``distance`` kept for diagnostics — a backend must never default a
missing distance to ``0.0``. ``UpsertResult``/``StoreCapabilities`` let
callers (the pipeline) find out what happened and what the backend can do
without importing a concrete backend module.

Backends never swallow failures into an empty list or a zero count — they
raise, so per-document failure isolation happens once, in the pipeline,
not silently at every call site.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class VectorChunk:
    """A single chunk to be stored in the vector DB."""

    chunk_id: str
    text: str
    embedding: list[float]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class StoredChunk:
    """A stored-only, non-embedded record — the Index Contract v2.1
    structural parent (``chunk_level=0``).

    Written to ``chunks/{source_id}.jsonl`` alongside its embedded
    ``VectorChunk`` children (same file, same key formula) but never passed
    to ``VectorStore.upsert`` — a parent has no place in the similarity
    index, only in the snapshot, fetched by key (``parent_key``) when a
    matched child needs its enclosing context.
    """

    key: str
    chunk_index: int
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class VectorSearchResult:
    """One result returned from a vector query.

    ``score`` is a similarity (higher is better), computed at the query
    edge from the backend's raw ``distance`` — kept here too, for
    diagnostics. A backend that cannot report a real distance must raise,
    never fill this in with ``0.0``.
    """

    chunk_id: str
    text: str
    score: float
    distance: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class UpsertResult:
    """Outcome of one ``upsert`` call."""

    upserted_count: int
    keys: list[str] = field(default_factory=list)


@dataclass
class StoreCapabilities:
    """What a backend can and cannot do — re-verified per backend in WP1."""

    supports_filter_delete: bool = False
    max_metadata_bytes: int = 40_960
    max_non_filterable_keys: int = 10
    max_top_k: int = 30


@runtime_checkable
class VectorStore(Protocol):
    """Async interface every vector backend implements."""

    @property
    def index_name(self) -> str: ...

    async def ensure_index(self) -> bool:
        """Create the index if missing. Returns True once it's ready."""
        ...

    async def describe(self) -> dict[str, Any]:
        """Backend-neutral description (bucket/index/region/dimension/...)."""
        ...

    async def healthcheck(self) -> bool:
        """Cheapest real call against the backend; never raises."""
        ...

    async def upsert(self, chunks: list[VectorChunk]) -> UpsertResult:
        """Upsert chunks. Raises on failure — never returns a partial 0."""
        ...

    async def delete_keys(self, keys: list[str]) -> int:
        """Delete vectors by their exact keys. Returns count deleted."""
        ...

    async def delete_by_doc_id(
        self, doc_id: str, known_keys: list[str] | None = None
    ) -> int:
        """Delete every vector for ``doc_id``.

        ``known_keys`` (from ``vector_manifest.json``) enables a
        key-addressed delete with no index scan; omitted only falls back to
        a full ``list_vectors`` scan, which raises on failure rather than
        swallowing it.
        """
        ...

    async def delete_index(self) -> bool:
        """Drop the entire index (full re-embed path on index-config drift)."""
        ...

    async def query(
        self,
        embedding: list[float],
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[VectorSearchResult]:
        """Query for similar chunks. Optional metadata filter."""
        ...

    def capabilities(self) -> StoreCapabilities:
        """Declared limits for this backend."""
        ...
