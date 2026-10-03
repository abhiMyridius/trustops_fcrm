"""Tests for evoq_context/vector/s3_vectors.py (S3VectorsStore) with a stubbed
aioboto3 client — no real AWS calls."""

from __future__ import annotations

from typing import Any

import pytest
from evoq_context.core.config import ContextConfig
from evoq_context.vector.interface import VectorChunk
from evoq_context.vector.s3_vectors import MissingDistanceError, S3VectorsStore


def _config(tmp_path) -> ContextConfig:
    return ContextConfig(
        projects_root=tmp_path,
        vector_backend="s3vectors",
        s3v_bucket="my-bucket",
        s3v_region="us-east-1",
        s3v_index_prefix="context",
        embedding_model="amazon.titan-embed-text-v2:0",
        embedding_provider="bedrock",
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


class _FakeClient:
    """Minimal async S3 Vectors client stub."""

    def __init__(self, state: dict[str, Any]) -> None:
        self._state = state

    async def create_index(self, **kwargs):
        if self._state.get("create_index_error"):
            raise self._state["create_index_error"]
        self._state["index_created"] = True
        self._state["create_index_kwargs"] = kwargs

    async def get_index(self, **kwargs):
        if self._state.get("healthcheck_fails"):
            raise RuntimeError("not found")
        return {"index": {}}

    async def delete_index(self, **kwargs):
        self._state["index_deleted"] = True

    async def put_vectors(self, **kwargs):
        if self._state.get("put_error"):
            raise self._state["put_error"]
        self._state.setdefault("put_calls", []).append(kwargs["vectors"])

    async def delete_vectors(self, **kwargs):
        self._state.setdefault("deleted_keys", []).extend(kwargs["keys"])

    async def list_vectors(self, **kwargs):
        return self._state.get("list_vectors_response", {"vectors": []})

    async def query_vectors(self, **kwargs):
        return self._state.get("query_vectors_response", {"vectors": []})


class _FakeClientCtx:
    def __init__(self, state: dict[str, Any]) -> None:
        self._state = state

    async def __aenter__(self):
        return _FakeClient(self._state)

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    def __init__(self, state: dict[str, Any]) -> None:
        self._state = state

    def client(self, *args, **kwargs):
        return _FakeClientCtx(self._state)


def _install_fake_session(
    monkeypatch, store: S3VectorsStore, state: dict[str, Any]
) -> None:
    monkeypatch.setattr(store, "_get_session", lambda: _FakeSession(state))


def _chunk(key: str, embedding: list[float]) -> VectorChunk:
    return VectorChunk(
        chunk_id=key,
        text="hello",
        embedding=embedding,
        metadata={
            "doc_id": "file:D1",
            "project": "proj",
            "source_type": "file",
            "source_id": "D1",
            "content_hash": "sha256:" + "a" * 64,
            "chunk_key": key,
            "chunk_index": 0,
            "offset": 0,
            "chunk_strategy": "paragraph_windows",
            "type": "plain_text",
            "snapshot_version": "v1",
            "index_contract": 2,
            "text_truncated": False,
            "text": "hello",
        },
    )


@pytest.mark.asyncio
async def test_ensure_index_creates_with_declared_non_filterable_keys(
    tmp_path, monkeypatch
) -> None:
    store = S3VectorsStore(_config(tmp_path), "proj")
    state: dict[str, Any] = {}
    _install_fake_session(monkeypatch, store, state)

    ok = await store.ensure_index()
    assert ok is True
    assert state["index_created"] is True
    non_filterable = state["create_index_kwargs"]["metadataConfiguration"][
        "nonFilterableMetadataKeys"
    ]
    assert "text" in non_filterable
    assert len(non_filterable) <= 10


@pytest.mark.asyncio
async def test_ensure_index_swallows_already_exists(tmp_path, monkeypatch) -> None:
    store = S3VectorsStore(_config(tmp_path), "proj")
    state: dict[str, Any] = {"create_index_error": RuntimeError("Index already exists")}
    _install_fake_session(monkeypatch, store, state)
    assert await store.ensure_index() is True


@pytest.mark.asyncio
async def test_ensure_index_raises_on_real_failure(tmp_path, monkeypatch) -> None:
    store = S3VectorsStore(_config(tmp_path), "proj")
    state: dict[str, Any] = {"create_index_error": RuntimeError("AccessDenied")}
    _install_fake_session(monkeypatch, store, state)
    with pytest.raises(RuntimeError):
        await store.ensure_index()


@pytest.mark.asyncio
async def test_upsert_raises_on_failure_never_returns_zero_silently(
    tmp_path, monkeypatch
) -> None:
    store = S3VectorsStore(_config(tmp_path), "proj")
    state: dict[str, Any] = {"put_error": RuntimeError("ServiceUnavailable")}
    _install_fake_session(monkeypatch, store, state)
    with pytest.raises(RuntimeError):
        await store.upsert([_chunk("file:D1:aaaaaaaaaaaaaaaa:0000", [1.0, 0, 0, 0])])


@pytest.mark.asyncio
async def test_upsert_returns_upsert_result_with_keys(tmp_path, monkeypatch) -> None:
    store = S3VectorsStore(_config(tmp_path), "proj")
    state: dict[str, Any] = {}
    _install_fake_session(monkeypatch, store, state)
    result = await store.upsert(
        [_chunk("file:D1:aaaaaaaaaaaaaaaa:0000", [1.0, 0, 0, 0])]
    )
    assert result.upserted_count == 1
    assert result.keys == ["file:D1:aaaaaaaaaaaaaaaa:0000"]


@pytest.mark.asyncio
async def test_query_raises_on_missing_distance_never_defaults_zero(
    tmp_path, monkeypatch
) -> None:
    store = S3VectorsStore(_config(tmp_path), "proj")
    state: dict[str, Any] = {
        "query_vectors_response": {
            "vectors": [{"key": "k1", "metadata": {"text": "t"}}]
        },
    }
    _install_fake_session(monkeypatch, store, state)
    with pytest.raises(MissingDistanceError):
        await store.query([1.0, 0, 0, 0], top_k=5)


@pytest.mark.asyncio
async def test_query_converts_distance_to_similarity(tmp_path, monkeypatch) -> None:
    store = S3VectorsStore(_config(tmp_path), "proj")
    state: dict[str, Any] = {
        "query_vectors_response": {
            "vectors": [{"key": "k1", "distance": 0.2, "metadata": {"text": "t"}}]
        },
    }
    _install_fake_session(monkeypatch, store, state)
    results = await store.query([1.0, 0, 0, 0], top_k=5)
    assert results[0].distance == 0.2
    assert results[0].score == pytest.approx(0.8)


@pytest.mark.asyncio
async def test_delete_by_doc_id_with_known_keys_is_key_addressed_no_scan(
    tmp_path, monkeypatch
) -> None:
    store = S3VectorsStore(_config(tmp_path), "proj")
    state: dict[str, Any] = {}
    _install_fake_session(monkeypatch, store, state)
    deleted = await store.delete_by_doc_id("file:D1", known_keys=["k1", "k2"])
    assert deleted == 2
    assert set(state["deleted_keys"]) == {"k1", "k2"}
    assert "list_vectors_response" not in state or "vectors" not in state.get(
        "list_vectors_response", {}
    )


@pytest.mark.asyncio
async def test_delete_by_doc_id_without_known_keys_falls_back_to_scan(
    tmp_path, monkeypatch
) -> None:
    store = S3VectorsStore(_config(tmp_path), "proj")
    state: dict[str, Any] = {
        "list_vectors_response": {
            "vectors": [
                {"key": "k1", "metadata": {"doc_id": "file:D1"}},
                {"key": "k2", "metadata": {"doc_id": "file:D2"}},
            ]
        },
    }
    _install_fake_session(monkeypatch, store, state)
    deleted = await store.delete_by_doc_id("file:D1")
    assert deleted == 1
    assert state["deleted_keys"] == ["k1"]


@pytest.mark.asyncio
async def test_full_scan_delete_raises_on_failure(tmp_path, monkeypatch) -> None:
    store = S3VectorsStore(_config(tmp_path), "proj")

    class _FailingClient(_FakeClient):
        async def list_vectors(self, **kwargs):
            raise RuntimeError("throttled")

    class _FailingCtx(_FakeClientCtx):
        async def __aenter__(self):
            return _FailingClient(self._state)

    class _FailingSession(_FakeSession):
        def client(self, *a, **kw):
            return _FailingCtx(self._state)

    monkeypatch.setattr(store, "_get_session", lambda: _FailingSession({}))
    with pytest.raises(RuntimeError):
        await store.delete_by_doc_id("file:D1")


@pytest.mark.asyncio
async def test_shrink_metadata_never_pops_text_until_last_resort(tmp_path) -> None:
    store = S3VectorsStore(_config(tmp_path), "proj")
    meta = {"text": "x" * 12_000, "strategy_reason": "y" * 30_000, "doc_id": "file:D1"}
    shrunk = store._shrink_metadata(meta)
    assert "text" in shrunk
    assert shrunk["text"]


def test_capabilities_declares_no_filter_delete_and_byte_cap(tmp_path) -> None:
    store = S3VectorsStore(_config(tmp_path), "proj")
    caps = store.capabilities()
    assert caps.supports_filter_delete is False
    assert caps.max_metadata_bytes == 40_960
