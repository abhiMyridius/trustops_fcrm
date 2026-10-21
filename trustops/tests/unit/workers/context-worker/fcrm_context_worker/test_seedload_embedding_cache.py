"""``load_clauses`` with an embedding cache (``SKIP_EMBED_IF_PRESENT``): cached
clauses are not embedded again; new or changed ones are, and are cached."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from evoq_context.llm.client import LLMClient
from evoq_context.vector.factory import create_vector_store
from evoq_context.vector.interface import VectorStore
from fcrm_context_worker.config import FCRM_PROJECT, context_config_from_env
from fcrm_context_worker.seedload import (
    embedding_model_key,
    load_clauses,
    seed_clauses,
)

pytestmark = pytest.mark.usefixtures("four_clause_corpus")


class _DictCache:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], list[float]] = {}

    async def get_many(self, model: str, keys: list[str]) -> dict[str, list[float]]:
        return {k: self.rows[(model, k)] for k in keys if (model, k) in self.rows}

    async def put_many(self, model: str, vectors: Mapping[str, list[float]]) -> None:
        for key, vector in vectors.items():
            self.rows[(model, key)] = list(vector)


class _CountingLLM:
    """Wraps a real (dummy-provider) client and records every text embedded."""

    def __init__(self, inner: LLMClient) -> None:
        self._inner = inner
        self.embed_provider = inner.embed_provider
        self.embed_model = inner.embed_model
        self.embed_dimension = inner.embed_dimension
        self.embedded: list[str] = []

    async def embed(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        self.embedded.extend(texts)
        return await self._inner.embed(texts, **kwargs)


def _setup(tmp_path: Path) -> tuple[VectorStore, _CountingLLM]:
    config = context_config_from_env(
        {"VECTOR_BACKEND": "memory", "EMBEDDING_DIMENSION": "64"},
        projects_root=tmp_path,
    )
    store = create_vector_store(config, FCRM_PROJECT)
    assert store is not None
    return store, _CountingLLM(LLMClient.from_config(config))


async def test_a_second_start_reuses_every_cached_embedding(tmp_path: Path) -> None:
    cache = _DictCache()
    store, llm = _setup(tmp_path)
    assert await load_clauses(store, llm, cache=cache) == 4  # type: ignore[arg-type]
    assert len(llm.embedded) == 4
    assert len(cache.rows) == 4

    store, llm = _setup(tmp_path)
    assert await load_clauses(store, llm, cache=cache) == 4  # type: ignore[arg-type]
    assert llm.embedded == []


async def test_only_a_changed_clause_is_embedded_again(tmp_path: Path) -> None:
    cache = _DictCache()
    store, llm = _setup(tmp_path)
    await load_clauses(store, llm, cache=cache)  # type: ignore[arg-type]

    clauses = seed_clauses()
    clauses[0] = {**clauses[0], "text": clauses[0]["text"] + " Amended."}
    store, llm = _setup(tmp_path)
    assert await load_clauses(store, llm, clauses, cache=cache) == 4  # type: ignore[arg-type]
    assert llm.embedded == [clauses[0]["text"]]


async def test_a_different_model_does_not_reuse_the_cache(tmp_path: Path) -> None:
    cache = _DictCache()
    store, llm = _setup(tmp_path)
    await load_clauses(store, llm, cache=cache)  # type: ignore[arg-type]
    first_model = embedding_model_key(llm)  # type: ignore[arg-type]

    store, llm = _setup(tmp_path)
    llm.embed_model = "another-model"
    await load_clauses(store, llm, cache=cache)  # type: ignore[arg-type]
    assert len(llm.embedded) == 4
    assert embedding_model_key(llm) != first_model  # type: ignore[arg-type]


async def test_without_a_cache_every_clause_is_embedded(tmp_path: Path) -> None:
    store, llm = _setup(tmp_path)
    await load_clauses(store, llm)  # type: ignore[arg-type]
    store, llm = _setup(tmp_path)
    await load_clauses(store, llm)  # type: ignore[arg-type]
    assert len(llm.embedded) == 4
