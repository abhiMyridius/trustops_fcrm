"""Building one index: read its rows from S3, embed them, upsert the vectors,
and (under the ``memory`` backend) dump the fixture S3 will load.

No LLM synthesis runs here: a build embeds stored text and nothing else. The
engine's chat path is never called (``synthesis_enabled`` is off in every config
the worker builds), and a test with a failing fake chat model pins it.

**Memory backend: every build is a full build.** Each build starts from an empty
in-memory store, so the fixture it dumps is exactly the index at that version
(an ``incremental`` request is served the same way: S3 returns every row either
way). **S3 Vectors backend: a build upserts** into the shared index by
deterministic record key, so unchanged rows are rewritten identically and an
edited row gets a new key; a row S3 has since deleted would linger (a full
delete-by-manifest is not implemented, see ``UPSTREAM.md``).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Optional

from evoq_context.core.config import ContextConfig
from evoq_context.llm.client import LLMClient
from evoq_context.vector.factory import create_vector_store
from evoq_context.vector.interface import VectorChunk, VectorStore

from fcrm_context_worker.config import FCRM_PROJECT
from fcrm_context_worker.fixture import FixtureSource, encode_fixture
from fcrm_context_worker.indexes import (
    CONTROLS,
    INDEXES,
    POLICY_CLAUSES,
    PRECEDENT_CASES,
    IndexRecord,
    clause_records,
    control_records,
    precedent_records,
)
from fcrm_context_worker.sources import RecordsNotFoundError, RecordSource

logger = logging.getLogger(__name__)

StoreFactory = Callable[[], Optional[VectorStore]]


class UnknownIndexError(ValueError):
    """The request names an index H4 does not build."""


class NoVectorBackendError(RuntimeError):
    """``VECTOR_BACKEND=none``: there is nowhere to put vectors."""


@dataclass(frozen=True)
class BuildResult:
    """What a build produced: how many vectors, and the fixture bytes when the
    backend can dump one (the ``memory`` backend; ``None`` for S3 Vectors).
    """

    vector_count: int
    fixture: Optional[bytes]


class IndexBuilder:
    """Builds the three indexes from S3's rows.

    Args:
        source: Where the rows come from.
        llm: The embedding client (the engine's; the dummy provider offline).
        config: The engine config (backend, embedding provider and dimension).
        make_store: A new store per build; the configured backend by default.
    """

    def __init__(
        self,
        source: RecordSource,
        llm: LLMClient,
        config: ContextConfig,
        make_store: Optional[StoreFactory] = None,
    ) -> None:
        self._source = source
        self._llm = llm
        self._config = config
        self._make_store = make_store or self._configured_store

    def _configured_store(self) -> Optional[VectorStore]:
        """A store of the configured backend (``None`` for ``none``)."""
        return create_vector_store(self._config, FCRM_PROJECT)

    async def _records(self, index: str, version: int) -> list[IndexRecord]:
        """The index's rows from S3, as records.

        Raises:
            UnknownIndexError: ``index`` is not one of the three.
            RecordsNotFoundError: S3 has no rows to build it from.
        """
        if index == POLICY_CLAUSES:
            return clause_records(await self._source.clauses(version))
        if index == PRECEDENT_CASES:
            return precedent_records(await self._source.precedents())
        if index == CONTROLS:
            return control_records(await self._source.controls(version), f"v{version}")
        raise UnknownIndexError(f"no index {index!r}; expected one of {INDEXES}")

    async def build(self, index: str, version: int) -> BuildResult:
        """Build ``index`` at ``version``.

        Raises:
            UnknownIndexError: ``index`` is not one of the three.
            RecordsNotFoundError: S3 has no rows to build it from.
            NoVectorBackendError: no vector backend is configured.
            evoq_context.llm.client.EmbeddingInputTooLarge: a row is too long
                to embed (never truncated).
            fcrm_common.errors.UpstreamUnavailableError: S3 could not be read.
        """
        records = await self._records(index, version)
        if not records:
            raise RecordsNotFoundError(f"{index} has no rows at version {version}")
        store = self._make_store()
        if store is None:
            raise NoVectorBackendError("VECTOR_BACKEND=none has nowhere to put vectors")
        embeddings = await self._llm.embed([r.text for r in records])
        chunks = _chunks(records, embeddings)
        await store.ensure_index()
        result = await store.upsert(chunks)
        fixture = (
            # JSON-encoding ~200k floats is CPU work: off the event loop.
            await asyncio.to_thread(encode_fixture, store)
            if isinstance(store, FixtureSource)
            else None
        )
        logger.info(
            "index built",
            extra={
                "index": index,
                "version": version,
                "vectors": result.upserted_count,
                "fixture": fixture is not None,
            },
        )
        return BuildResult(vector_count=result.upserted_count, fixture=fixture)


def _chunks(
    records: Sequence[IndexRecord], embeddings: Sequence[Sequence[float]]
) -> list[VectorChunk]:
    """One chunk per record, keyed by the record's own ``chunk_key``."""
    return [
        VectorChunk(
            chunk_id=record.metadata["chunk_key"],
            text=record.text,
            embedding=list(embedding),
            metadata=record.metadata,
        )
        for record, embedding in zip(records, embeddings, strict=True)
    ]
