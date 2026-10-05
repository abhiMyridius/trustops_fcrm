"""Tests for evoq_context/vector/memory_store.py — InMemoryVectorStore."""

from __future__ import annotations

import pytest
from evoq_context.core.config import ContextConfig
from evoq_context.vector.interface import VectorChunk
from evoq_context.vector.memory_store import InMemoryVectorStore, matches_filter


def _config(tmp_path) -> ContextConfig:
    return ContextConfig(
        projects_root=tmp_path,
        vector_backend="memory",
        s3v_bucket="",
        s3v_region="us-east-1",
        s3v_index_prefix="context",
        embedding_model="amazon.titan-embed-text-v2:0",
        embedding_provider="dummy",
        embedding_dimension=4,
        embedding_max_input_tokens=50_000,
        embedding_input_safety=1.0,
        chunk_target_tokens=700,
        chunk_hard_max_tokens=2000,
        chunk_max_rows=500,
        llm_max_tokens=15000,
        analysis_model="dummy",
        analysis_provider="dummy",
        synthesis_model="dummy",
        synthesis_provider="dummy",
    )


def _chunk(
    chunk_id: str, doc_id: str, chunk_index: int, embedding: list[float], **extra
) -> VectorChunk:
    meta = {
        "doc_id": doc_id,
        "project": "proj",
        "source_type": "file",
        "source_id": doc_id.split(":")[-1],
        "content_hash": "sha256:" + "a" * 64,
        "chunk_key": chunk_id,
        "chunk_index": chunk_index,
        "offset": chunk_index,
        "chunk_strategy": "paragraph_windows",
        "type": "plain_text",
        "snapshot_version": "v1",
        "index_contract": 2,
        "text_truncated": False,
        "text": f"text for {chunk_id}",
    }
    meta.update(extra)
    return VectorChunk(
        chunk_id=chunk_id, text=meta["text"], embedding=embedding, metadata=meta
    )


@pytest.mark.asyncio
async def test_upsert_and_query_returns_similarity_descending(tmp_path) -> None:
    store = InMemoryVectorStore(_config(tmp_path), "proj")
    await store.upsert(
        [
            _chunk("file:D1:aaaaaaaaaaaaaaaa:0000", "file:D1", 0, [1.0, 0.0, 0.0, 0.0]),
            _chunk("file:D1:aaaaaaaaaaaaaaaa:0001", "file:D1", 1, [0.0, 1.0, 0.0, 0.0]),
        ]
    )
    results = await store.query([1.0, 0.0, 0.0, 0.0], top_k=2)
    assert len(results) == 2
    assert results[0].chunk_id == "file:D1:aaaaaaaaaaaaaaaa:0000"
    assert results[0].score > results[1].score
    assert results[0].distance < results[1].distance


@pytest.mark.asyncio
async def test_delete_by_doc_id_without_known_keys_scans(tmp_path) -> None:
    store = InMemoryVectorStore(_config(tmp_path), "proj")
    await store.upsert(
        [_chunk("file:D1:aaaaaaaaaaaaaaaa:0000", "file:D1", 0, [1.0, 0, 0, 0])]
    )
    deleted = await store.delete_by_doc_id("file:D1")
    assert deleted == 1
    assert await store.query([1.0, 0, 0, 0], top_k=5) == []


@pytest.mark.asyncio
async def test_delete_by_doc_id_with_known_keys_is_key_addressed(tmp_path) -> None:
    store = InMemoryVectorStore(_config(tmp_path), "proj")
    await store.upsert(
        [
            _chunk("file:D1:aaaaaaaaaaaaaaaa:0000", "file:D1", 0, [1.0, 0, 0, 0]),
            _chunk("file:D2:bbbbbbbbbbbbbbbb:0000", "file:D2", 0, [0, 1.0, 0, 0]),
        ]
    )
    deleted = await store.delete_by_doc_id(
        "file:D1", known_keys=["file:D1:aaaaaaaaaaaaaaaa:0000"]
    )
    assert deleted == 1
    remaining = await store.query([0, 1.0, 0, 0], top_k=5)
    assert len(remaining) == 1
    assert remaining[0].chunk_id == "file:D2:bbbbbbbbbbbbbbbb:0000"


@pytest.mark.asyncio
async def test_query_filters_eq_and_project_scope(tmp_path) -> None:
    store = InMemoryVectorStore(_config(tmp_path), "proj")
    await store.upsert(
        [
            _chunk(
                "file:D1:aaaaaaaaaaaaaaaa:0000",
                "file:D1",
                0,
                [1.0, 0, 0, 0],
                source_type="jira",
            ),
            _chunk(
                "file:D2:bbbbbbbbbbbbbbbb:0000",
                "file:D2",
                0,
                [1.0, 0, 0, 0],
                source_type="confluence",
            ),
        ]
    )
    results = await store.query(
        [1.0, 0, 0, 0], top_k=5, filters={"source_type": "jira"}
    )
    assert len(results) == 1
    assert results[0].metadata["source_type"] == "jira"


def test_matches_filter_operators() -> None:
    meta = {"chunk_index": 5, "doc_id": "d1", "labels": "a,b"}
    assert matches_filter(meta, {"chunk_index": {"$eq": 5}})
    assert not matches_filter(meta, {"chunk_index": {"$ne": 5}})
    assert matches_filter(meta, {"doc_id": {"$in": ["d1", "d2"]}})
    assert not matches_filter(meta, {"doc_id": {"$nin": ["d1"]}})
    assert matches_filter(meta, {"chunk_index": {"$gte": 5, "$lte": 5}})
    assert not matches_filter(meta, {"chunk_index": {"$gt": 5}})
    assert matches_filter(
        meta, {"$and": [{"doc_id": "d1"}, {"chunk_index": {"$gte": 0}}]}
    )
    assert matches_filter(meta, {"$or": [{"doc_id": "nope"}, {"doc_id": "d1"}]})
    assert matches_filter(meta, {"missing_key": {"$exists": False}})
    assert not matches_filter(meta, {"doc_id": {"$exists": False}})


@pytest.mark.asyncio
async def test_shrink_metadata_honours_byte_cap(tmp_path) -> None:
    store = InMemoryVectorStore(_config(tmp_path), "proj")
    huge_meta = {
        "text": "x" * 12_000,
        "title": "y" * 30_000,
        "source_path": "z" * 30_000,
        "doc_id": "file:D1",
        "project": "proj",
    }
    shrunk = store._shrink_metadata(huge_meta)
    from evoq_context.vector.contract import metadata_size_bytes

    assert metadata_size_bytes(shrunk) <= 40_960
    assert shrunk["doc_id"] == "file:D1"


@pytest.mark.asyncio
async def test_dump_fixture_matches_contract_shape(tmp_path) -> None:
    store = InMemoryVectorStore(_config(tmp_path), "proj")
    await store.upsert(
        [_chunk("file:D1:aaaaaaaaaaaaaaaa:0000", "file:D1", 0, [1.0, 0, 0, 0])]
    )
    fixture = store.dump_fixture()
    assert fixture["fixture_version"] == 1
    assert fixture["backend"] == "memory"
    assert fixture["distance_metric"] == "cosine"
    assert "dimension" in fixture
    assert isinstance(fixture["vectors"], list)
    assert fixture["vectors"][0]["key"] == "file:D1:aaaaaaaaaaaaaaaa:0000"
    assert "metadata" in fixture["vectors"][0]
    assert "vector" in fixture["vectors"][0]


@pytest.mark.asyncio
async def test_describe_and_healthcheck_and_delete_index(tmp_path) -> None:
    store = InMemoryVectorStore(_config(tmp_path), "proj")
    await store.upsert(
        [_chunk("file:D1:aaaaaaaaaaaaaaaa:0000", "file:D1", 0, [1.0, 0, 0, 0])]
    )
    info = await store.describe()
    assert info["vector_count"] == 1
    assert await store.healthcheck() is True
    assert await store.delete_index() is True
    info2 = await store.describe()
    assert info2["vector_count"] == 0
