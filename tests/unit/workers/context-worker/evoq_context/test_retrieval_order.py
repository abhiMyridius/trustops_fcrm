"""Matches keep the backend's relevance order; siblings are appended, never
interleaved or re-sorted in (evoq_context/retrieval/context_engine.py)."""

from __future__ import annotations

import json

import pytest
from evoq_context.core.config import ContextConfig
from evoq_context.retrieval.context_engine import expand_parents, get_context, search
from evoq_context.storage.layout import SnapshotLayout
from evoq_context.vector.interface import VectorChunk, VectorSearchResult
from evoq_context.vector.memory_store import InMemoryVectorStore


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


def _chunk(idx: int, embedding: list[float], doc_id: str = "file:D1") -> VectorChunk:
    key = f"{doc_id}:aaaaaaaaaaaaaaaa:{idx:04d}"
    return VectorChunk(
        chunk_id=key,
        text=f"chunk {idx}",
        embedding=embedding,
        metadata={
            "doc_id": doc_id,
            "project": "proj",
            "chunk_index": idx,
            "source_type": "file",
            "source_id": doc_id.split(":")[-1],
            "content_hash": "sha256:" + "a" * 64,
            "chunk_key": key,
            "offset": idx,
            "chunk_strategy": "paragraph_windows",
            "type": "plain_text",
            "snapshot_version": "v1",
            "index_contract": 2,
            "text_truncated": False,
            "text": f"chunk {idx}",
        },
    )


@pytest.mark.asyncio
async def test_matches_keep_backend_relevance_order_not_resorted(tmp_path) -> None:
    config = _config(tmp_path)
    store = InMemoryVectorStore(config, "proj")
    # Chunk 5 is the closest match to the query vector, chunk 1 is second,
    # chunk 10 is a poor match — deliberately out of doc_id/chunk_index order.
    await store.upsert(
        [
            _chunk(10, [0.1, 0.9, 0.0, 0.0]),
            _chunk(5, [1.0, 0.0, 0.0, 0.0]),
            _chunk(1, [0.9, 0.1, 0.0, 0.0]),
        ]
    )

    result = await get_context(
        project="proj",
        config=config,
        vector_store=store,
        query_embedding=[1.0, 0.0, 0.0, 0.0],
        include_high_level=False,
        top_k=3,
        enrich_siblings=False,
    )
    ordered_indices = [c["metadata"]["chunk_index"] for c in result.retrieved_chunks]
    # Best match (5) first, then (1), then the weak one (10) — never
    # re-sorted by chunk_index (which would give 1, 5, 10).
    assert ordered_indices == [5, 1, 10]
    assert all(c["is_match"] for c in result.retrieved_chunks)


@pytest.mark.asyncio
async def test_siblings_are_appended_after_matches_and_flagged(tmp_path) -> None:
    config = _config(tmp_path)
    store = InMemoryVectorStore(config, "proj")
    await store.upsert(
        [
            _chunk(5, [1.0, 0.0, 0.0, 0.0]),  # the match
            _chunk(
                4, [0.0, 1.0, 0.0, 0.0]
            ),  # sibling (adjacent index, poor match on its own)
            _chunk(6, [0.0, 0.0, 1.0, 0.0]),  # sibling
            _chunk(50, [0.0, 0.0, 0.0, 1.0]),  # unrelated, far away index
        ]
    )

    result = await get_context(
        project="proj",
        config=config,
        vector_store=store,
        query_embedding=[1.0, 0.0, 0.0, 0.0],
        include_high_level=False,
        top_k=1,
        enrich_siblings=True,
        max_siblings=2,
    )
    chunks = result.retrieved_chunks
    assert chunks[0]["metadata"]["chunk_index"] == 5
    assert chunks[0]["is_match"] is True

    siblings = [c for c in chunks if not c["is_match"]]
    sibling_indices = {c["metadata"]["chunk_index"] for c in siblings}
    assert sibling_indices == {4, 6}
    assert all(c["sibling_of"] == chunks[0]["chunk_id"] for c in siblings)
    assert all("sibling_distance" in c for c in siblings)
    # The unrelated far-away chunk never appears as a sibling.
    assert 50 not in sibling_indices
    # Matches come first, siblings after — never interleaved.
    match_positions = [i for i, c in enumerate(chunks) if c["is_match"]]
    sibling_positions = [i for i, c in enumerate(chunks) if not c["is_match"]]
    assert max(match_positions) < min(sibling_positions)


@pytest.mark.asyncio
async def test_search_embeds_query_text_and_returns_context(tmp_path) -> None:
    from evoq_context.llm.client import LLMClient

    config = _config(tmp_path)
    store = InMemoryVectorStore(config, "proj")
    await store.upsert([_chunk(0, [1.0, 0.0, 0.0, 0.0])])
    llm = LLMClient.from_config(config)

    result = await search("proj", "some query text", config, llm, store, top_k=1)
    assert result.retrieved_chunks
    assert result.retrieved_chunks[0]["is_match"] is True


@pytest.mark.asyncio
async def test_deep_query_failure_still_returns_high_level_context(tmp_path) -> None:
    config = _config(tmp_path)

    class _BrokenStore:
        async def query(self, *args, **kwargs):
            raise RuntimeError("boom")

    result = await get_context(
        project="proj",
        config=config,
        vector_store=_BrokenStore(),
        query_embedding=[0.1, 0.2, 0.3, 0.4],
        include_high_level=True,
    )
    assert result.retrieved_chunks == []


# ---------------------------------------------------------------------------
# expand_parents (Index Contract v2.1 small-to-big) — relevance order
# preserved; parents never get their own score, ONE lookup per distinct
# parent (not one per hit).
# ---------------------------------------------------------------------------


def _hit(
    chunk_id: str,
    score: float,
    *,
    parent_key: str | None,
    project="proj",
    source_id="D1",
    version="v1",
    chunk_index: int = 0,
) -> VectorSearchResult:
    meta = {
        "doc_id": f"file:{source_id}",
        "project": project,
        "source_id": source_id,
        "snapshot_version": version,
        "chunk_index": chunk_index,
    }
    if parent_key:
        meta["parent_key"] = parent_key
    return VectorSearchResult(
        chunk_id=chunk_id,
        text=f"text {chunk_id}",
        score=score,
        distance=1 - score,
        metadata=meta,
    )


def _write_parent_jsonl(
    layout: SnapshotLayout,
    project: str,
    source_id: str,
    version: str,
    records: list[dict],
) -> None:
    path = layout.chunks_path(project, source_id, version=version)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


@pytest.mark.asyncio
async def test_expand_parents_preserves_relevance_order_and_attaches_parent_text(
    tmp_path,
) -> None:
    layout = SnapshotLayout(tmp_path)
    _write_parent_jsonl(
        layout,
        "proj",
        "D1",
        "v1",
        [
            {
                "key": "file:D1:aaaa:0005",
                "chunk_index": 5,
                "text": "the enclosing section text",
            }
        ],
    )

    # Deliberately out of doc_id/chunk_index order — best match (score 0.9)
    # first, second-best (0.7) second, weak match (0.2) third; two of the
    # three hits share the SAME parent_key.
    hits = [
        _hit("c1", 0.9, parent_key="file:D1:aaaa:0005", chunk_index=1),
        _hit("c2", 0.7, parent_key="file:D1:aaaa:0005", chunk_index=2),
        _hit("c3", 0.2, parent_key=None, chunk_index=9),
    ]

    result = await expand_parents(hits, store=None, layout=layout)

    # Relevance order preserved exactly — never resorted by chunk_index or
    # grouped by parent.
    assert [r["chunk_id"] for r in result] == ["c1", "c2", "c3"]
    assert result[0]["is_match"] is True
    assert result[1]["is_match"] is True

    # Both children of the same parent get the parent's text attached — the
    # parent never appears as its own separate entry with its own score.
    assert result[0]["parent_key"] == "file:D1:aaaa:0005"
    assert result[0]["parent_text"] == "the enclosing section text"
    assert result[1]["parent_key"] == "file:D1:aaaa:0005"
    assert result[1]["parent_text"] == "the enclosing section text"
    # The hit with no parent_key is untouched.
    assert "parent_key" not in result[2]
    assert "parent_text" not in result[2]


@pytest.mark.asyncio
async def test_expand_parents_reads_the_parent_file_at_most_once_per_distinct_parent(
    tmp_path,
) -> None:
    layout = SnapshotLayout(tmp_path)
    path = layout.chunks_path("proj", "D1", version="v1")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"key": "file:D1:aaaa:0009", "chunk_index": 9, "text": "parent body"}
        )
        + "\n",
        encoding="utf-8",
    )

    # Three hits, all sharing the same parent_key -> one distinct parent.
    hits = [
        _hit("c1", 0.9, parent_key="file:D1:aaaa:0009", chunk_index=1),
        _hit("c2", 0.8, parent_key="file:D1:aaaa:0009", chunk_index=2),
        _hit("c3", 0.7, parent_key="file:D1:aaaa:0009", chunk_index=3),
    ]

    import evoq_context.retrieval.context_engine as ce_module

    original = ce_module._find_parent_text_in_file
    calls = {"n": 0}

    def _counting(path_arg, wanted_keys):
        calls["n"] += 1
        return original(path_arg, wanted_keys)

    ce_module._find_parent_text_in_file = _counting
    try:
        result = await expand_parents(hits, store=None, layout=layout)
    finally:
        ce_module._find_parent_text_in_file = original

    assert calls["n"] == 1  # one file scan for all three hits' shared parent
    assert all(r["parent_text"] == "parent body" for r in result)


@pytest.mark.asyncio
async def test_expand_parents_falls_back_to_store_query_reconstruction(
    tmp_path,
) -> None:
    """No local snapshot access: children of the parent are themselves
    embedded and carry parent_key, so a filtered store query reconstructs
    the parent text from them, ordered by chunk_index."""
    config = ContextConfig(
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
    store = InMemoryVectorStore(config, "proj")
    parent_key = "file:D1:aaaa:0009"
    await store.upsert(
        [
            VectorChunk(
                chunk_id="file:D1:aaaa:0002",
                text="second half",
                embedding=[1.0, 0, 0, 0],
                metadata={
                    "parent_key": parent_key,
                    "chunk_index": 2,
                    "doc_id": "file:D1",
                    "text": "second half",
                },
            ),
            VectorChunk(
                chunk_id="file:D1:aaaa:0001",
                text="first half",
                embedding=[0, 1.0, 0, 0],
                metadata={
                    "parent_key": parent_key,
                    "chunk_index": 1,
                    "doc_id": "file:D1",
                    "text": "first half",
                },
            ),
        ]
    )

    hits = [_hit("c1", 0.9, parent_key=parent_key, chunk_index=0)]
    result = await expand_parents(hits, store=store, layout=None)

    assert result[0]["parent_key"] == parent_key
    # Reconstructed in chunk_index order, not upsert order.
    assert result[0]["parent_text"] == "first half\n\nsecond half"


@pytest.mark.asyncio
async def test_expand_parents_no_layout_or_store_returns_hits_unchanged() -> None:
    hits = [_hit("c1", 0.9, parent_key="file:D1:aaaa:0005")]
    result = await expand_parents(hits, store=None, layout=None)
    assert result[0]["chunk_id"] == "c1"
    assert "parent_text" not in result[0]
