"""S3 Vectors implementation of ``VectorStore`` (async via aioboto3).

Scope & functionality
----------------------
The only production vector backend. Every method raises on failure instead
of swallowing exceptions into an empty list or a zero count — per-document
failure isolation is the pipeline's job (``evoq_context/pipeline/vectors.py``),
not something silently hidden here. Similarity conversion happens at the
query edge (``score = 1 - distance`` for the pinned ``cosine`` metric); a
response with no ``distance`` is an error. Deletes are key-addressed via
the snapshot's ``vector_manifest.json`` whenever the caller has
``known_keys`` — the full ``list_vectors`` scan is kept only as a legacy
fallback for snapshots that predate the manifest, and it too raises on
failure rather than returning ``0``.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from evoq_context.core.observer import ContextObserver
from evoq_context.vector.contract import (
    MAX_METADATA_BYTES,
    NON_FILTERABLE_KEYS,
    metadata_size_bytes,
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

logger = logging.getLogger(__name__)

_PUT_BATCH_SIZE = 500
_DELETE_BATCH_SIZE = 500
_MAX_LIST_PAGES = 10_000


class MissingDistanceError(RuntimeError):
    """A query hit had no ``distance`` — never defaulted to 0.0."""


class S3VectorsDependencyMissingError(RuntimeError):
    """``aioboto3`` is not installed. It is the ``s3vectors`` extra, not a
    hard dependency of this package (the default ``memory`` vector backend
    never needs it, and aioboto3's pinned dependency chain conflicts with
    ``fcrm-agents``' ``pydantic-ai-slim[bedrock]`` in this workspace's
    single uv lock). Install ``fcrm-context-worker[s3vectors]`` on any host
    that sets ``VECTOR_BACKEND=s3vectors``.
    """


class S3VectorsStore:
    """Vector store backed by Amazon S3 Vectors.

    One index per project (name = ``{prefix}-{project}``). Each vector
    carries the Index Contract v2 metadata for traceability.
    """

    @staticmethod
    def _sanitize_index_name(name: str) -> str:
        sanitized = re.sub(r"[^a-z0-9\-.]", "-", name.lower())
        sanitized = re.sub(r"-{2,}", "-", sanitized).strip("-.")
        return sanitized[:63] or "default"

    @staticmethod
    def _metadata_size_bytes(metadata: dict[str, Any]) -> int:
        return metadata_size_bytes(metadata)

    def _shrink_metadata(
        self, metadata: dict[str, Any], *, observer: ContextObserver | None = None
    ) -> dict[str, Any]:
        """Trim metadata to fit S3 Vectors' per-record metadata size limit.

        Delegates to the single ``contract.shrink_metadata`` implementation
        (deterministic ``SHRINK_ORDER``/``SECONDARY_SHRINK_ORDER``, ``text``
        never popped) — kept as a method (not just a free function) since
        it is part of this store's public-ish test surface
        (``test_vector_store_s3.py``). Passing ``observer`` reports a
        ``GuardrailEvent`` whenever a record actually needed trimming.
        """
        return shrink_metadata(metadata, max_bytes=MAX_METADATA_BYTES, observer=observer)

    def __init__(self, config: ContextConfig, project: str) -> None:
        self._config = config
        self._index_name = self._sanitize_index_name(
            f"{config.s3v_index_prefix}-{project}"
        )
        self._session: Any = None

    def _get_session(self) -> Any:
        if self._session is None:
            try:
                import aioboto3  # type: ignore[import-not-found]
            except ImportError as exc:
                raise S3VectorsDependencyMissingError from exc
            self._session = aioboto3.Session()
        return self._session

    @property
    def index_name(self) -> str:
        return self._index_name

    def capabilities(self) -> StoreCapabilities:
        return StoreCapabilities(
            supports_filter_delete=False,
            max_metadata_bytes=MAX_METADATA_BYTES,
            max_non_filterable_keys=len(NON_FILTERABLE_KEYS),
            max_top_k=30,
        )

    async def ensure_index(self) -> bool:
        """Create the vector index if it doesn't exist. Raises on real failure."""
        try:
            async with self._get_session().client(
                "s3vectors",
                region_name=self._config.s3v_region,
            ) as client:
                await client.create_index(
                    vectorBucketName=self._config.s3v_bucket,
                    indexName=self._index_name,
                    dataType="float32",
                    dimension=self._config.embedding_dimension,
                    distanceMetric="cosine",
                    metadataConfiguration={
                        "nonFilterableMetadataKeys": list(NON_FILTERABLE_KEYS),
                    },
                )
                logger.info("Created S3 Vectors index %s", self._index_name)
        except Exception as exc:
            msg = str(exc).lower()
            if "already exists" not in msg and "conflict" not in msg:
                raise
        return True

    async def describe(self) -> dict[str, Any]:
        return {
            "backend": "s3vectors",
            "store": self._config.s3v_bucket,
            "index": self._index_name,
            "region": self._config.s3v_region,
            "dimension": self._config.embedding_dimension,
            "distance_metric": "cosine",
        }

    async def healthcheck(self) -> bool:
        try:
            async with self._get_session().client(
                "s3vectors", region_name=self._config.s3v_region
            ) as client:
                await client.get_index(
                    vectorBucketName=self._config.s3v_bucket,
                    indexName=self._index_name,
                )
            return True
        except Exception:
            logger.exception("S3 Vectors healthcheck failed")
            return False

    async def delete_index(self) -> bool:
        async with self._get_session().client(
            "s3vectors", region_name=self._config.s3v_region
        ) as client:
            await client.delete_index(
                vectorBucketName=self._config.s3v_bucket,
                indexName=self._index_name,
            )
        logger.info("Deleted S3 Vectors index %s", self._index_name)
        return True

    async def upsert(
        self, chunks: list[VectorChunk], *, observer: ContextObserver | None = None
    ) -> UpsertResult:
        await self.ensure_index()

        keys: list[str] = []
        async with self._get_session().client(
            "s3vectors", region_name=self._config.s3v_region
        ) as client:
            for offset in range(0, len(chunks), _PUT_BATCH_SIZE):
                batch = chunks[offset: offset + _PUT_BATCH_SIZE]
                payload = [
                    {
                        "key": c.chunk_id,
                        "data": {"float32": c.embedding},
                        "metadata": self._shrink_metadata(c.metadata, observer=observer),
                    }
                    for c in batch
                ]
                await client.put_vectors(
                    vectorBucketName=self._config.s3v_bucket,
                    indexName=self._index_name,
                    vectors=payload,
                )
                keys.extend(c.chunk_id for c in batch)
        logger.info("Upserted %d/%d chunks", len(keys), len(chunks))
        return UpsertResult(upserted_count=len(keys), keys=keys)

    async def delete_keys(self, keys: list[str]) -> int:
        if not keys:
            return 0
        deleted = 0
        async with self._get_session().client(
            "s3vectors", region_name=self._config.s3v_region
        ) as client:
            for offset in range(0, len(keys), _DELETE_BATCH_SIZE):
                batch = keys[offset: offset + _DELETE_BATCH_SIZE]
                await client.delete_vectors(
                    vectorBucketName=self._config.s3v_bucket,
                    indexName=self._index_name,
                    keys=batch,
                )
                deleted += len(batch)
        return deleted

    async def _delete_by_doc_id_full_scan(self, doc_id: str) -> int:
        """Legacy fallback for snapshots with no vector_manifest.json.

        Raises on failure — a scan that can't complete must not report a
        silent 0-deleted success.
        """
        logger.warning(
            "delete_by_doc_id(%s) has no known_keys — falling back to a "
            "full list_vectors scan (legacy path for pre-manifest snapshots)",
            doc_id,
        )
        async with self._get_session().client(
            "s3vectors", region_name=self._config.s3v_region,
        ) as client:
            keys: list[str] = []
            next_token: str | None = None
            page_count = 0
            while True:
                page_count += 1
                if page_count > _MAX_LIST_PAGES:
                    raise RuntimeError(
                        f"delete_by_doc_id({doc_id}) exceeded {_MAX_LIST_PAGES} "
                        "list_vectors pages without finishing the scan"
                    )
                kwargs: dict[str, Any] = {
                    "vectorBucketName": self._config.s3v_bucket,
                    "indexName": self._index_name,
                    "returnMetadata": True,
                }
                if next_token:
                    kwargs["nextToken"] = next_token
                resp = await client.list_vectors(**kwargs)
                for v in resp.get("vectors", []):
                    if v.get("metadata", {}).get("doc_id") == doc_id:
                        keys.append(v["key"])
                next_token = resp.get("nextToken")
                if not next_token:
                    break

        if keys:
            await self.delete_keys(keys)
        logger.info("Deleted %d chunk(s) for doc_id=%s (full scan)", len(keys), doc_id)
        return len(keys)

    async def delete_by_doc_id(
        self, doc_id: str, known_keys: list[str] | None = None
    ) -> int:
        if known_keys:
            return await self.delete_keys(known_keys)
        return await self._delete_by_doc_id_full_scan(doc_id)

    async def query(
        self,
        embedding: list[float],
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[VectorSearchResult]:
        async with self._get_session().client(
            "s3vectors",
            region_name=self._config.s3v_region,
        ) as client:
            kwargs: dict[str, Any] = {
                "vectorBucketName": self._config.s3v_bucket,
                "indexName": self._index_name,
                "queryVector": {"float32": embedding},
                "topK": top_k,
                "returnMetadata": True,
                "returnDistance": True,
            }
            if filters:
                kwargs["filter"] = filters
            resp = await client.query_vectors(**kwargs)

        results: list[VectorSearchResult] = []
        for v in resp.get("vectors", []):
            distance = v.get("distance")
            if distance is None:
                raise MissingDistanceError(
                    f"S3 Vectors query response for key={v.get('key')!r} had "
                    "no 'distance' — a missing distance is an error, not 0.0."
                )
            distance = float(distance)
            meta = v.get("metadata", {})
            results.append(
                VectorSearchResult(
                    chunk_id=v["key"],
                    text=meta.get("text", ""),
                    score=1.0 - distance,
                    distance=distance,
                    metadata=meta,
                )
            )
        return results
