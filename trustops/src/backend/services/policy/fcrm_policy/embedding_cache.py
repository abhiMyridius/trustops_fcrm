"""Clause embeddings kept in Mongo, so a restart does not re-embed the corpus.

``SKIP_EMBED_IF_PRESENT=true`` hands this cache to
:func:`fcrm_context_worker.seedload.load_clauses`: a clause whose vector is
already stored for the configured embedding model is upserted from here, and
only new or changed clauses (their chunk key carries the text's content hash)
go to the embedding provider. One document per model and chunk key in
``fcrm_policy.embedding_cache``; drop the collection to force a full re-embed.
"""

from __future__ import annotations

from collections.abc import Mapping

from fcrm_common.mongo import Document
from fcrm_common.time import utcnow
from pymongo import UpdateOne
from pymongo.asynchronous.collection import AsyncCollection


def _doc_id(model: str, key: str) -> str:
    return f"{model}|{key}"


class MongoEmbeddingCache:
    """:class:`~fcrm_context_worker.seedload.EmbeddingCache` over one collection."""

    def __init__(self, collection: AsyncCollection[Document]) -> None:
        self._collection = collection

    async def get_many(self, model: str, keys: list[str]) -> dict[str, list[float]]:
        """The cached vectors among ``keys`` for ``model``."""
        if not keys:
            return {}
        cursor = self._collection.find(
            {"_id": {"$in": [_doc_id(model, k) for k in keys]}},
            projection={"chunk_key": 1, "embedding": 1},
        )
        return {
            str(doc["chunk_key"]): [float(x) for x in doc["embedding"]]
            async for doc in cursor
        }

    async def put_many(self, model: str, vectors: Mapping[str, list[float]]) -> None:
        """Store ``vectors`` for ``model``; an existing entry is replaced."""
        if not vectors:
            return
        now = utcnow()
        await self._collection.bulk_write(
            [
                UpdateOne(
                    {"_id": _doc_id(model, key)},
                    {
                        "$set": {
                            "model": model,
                            "chunk_key": key,
                            "embedding": list(vector),
                            "stored_at": now,
                        }
                    },
                    upsert=True,
                )
                for key, vector in vectors.items()
            ],
            ordered=False,
        )
