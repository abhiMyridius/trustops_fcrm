"""Per-document embedding failure isolation (evoq_context/pipeline/vectors.py).

A document that fails chunking/embedding must never abort the whole
batch — it's reported in VectorWriteReport.failed and every other
document still gets embedded and upserted.

Migrated (lane C-F) from the deleted legacy ``embed_and_upsert``/
``prepare_chunks`` free functions onto the streaming ``VectorWriter`` +
``DocumentPreparer`` architecture that superseded them — see
``test_vector_writer_streaming.py`` for the fuller isolation/streaming
test suite this one deliberately keeps overlapping with (this file is
kept for its original, narrower "one bad ContextItem among real ones"
scenario using the real ``ChunkPipeline``, not a fake one).
"""

from __future__ import annotations

import pytest
from evoq_context.chunking.pipeline import ChunkPipeline
from evoq_context.core.config import ContextConfig
from evoq_context.core.models import ContextItem
from evoq_context.llm.client import LLMClient
from evoq_context.pipeline.vectors import VectorWriter
from evoq_context.vector.embedder import DocumentPreparer
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
        embedding_dimension=8,
        embedding_max_input_tokens=50_000,
        embedding_input_safety=1.0,
        chunk_target_tokens=200,
        chunk_hard_max_tokens=400,
        chunk_max_rows=500,
        llm_max_tokens=15000,
        analysis_model="dummy",
        analysis_provider="dummy",
        synthesis_model="dummy",
        synthesis_provider="dummy",
    )


def _item(source_id: str, body: str, metadata: dict | None = None) -> ContextItem:
    return ContextItem(
        id=f"file:{source_id}",
        source_type="file",
        source_id=source_id,
        title=source_id,
        body=body,
        content_hash=f"hash-{source_id}",
        project="proj",
        metadata=metadata or {},
    )


def _writer(config: ContextConfig, store: InMemoryVectorStore) -> VectorWriter:
    llm = LLMClient.from_config(config)
    preparer = DocumentPreparer(ChunkPipeline(config.chunking()), llm, config)
    return VectorWriter(store, preparer, config.layout, config)


@pytest.mark.asyncio
async def test_one_failing_document_does_not_block_the_others(
    tmp_path, monkeypatch
) -> None:
    good1 = _item("GOOD1", "A perfectly normal document body about the system.")
    bad = _item("BAD1", "This one will fail to chunk.")
    good2 = _item("GOOD2", "Another perfectly normal document body about the system.")

    config = _config(tmp_path)
    store = InMemoryVectorStore(config, "proj")
    writer = _writer(config, store)

    real_run = ChunkPipeline.run

    def _flaky_run(self, text, *, source_id, **kwargs):
        if source_id == "BAD1":
            raise RuntimeError("boom: simulated chunking failure")
        return real_run(self, text, source_id=source_id, **kwargs)

    monkeypatch.setattr(ChunkPipeline, "run", _flaky_run)

    report = await writer.write([good1, bad, good2], snapshot_version="v1")

    assert len(report.failed) == 1
    assert report.failed[0].doc_id == "file:BAD1"
    assert "boom" in report.failed[0].error
    assert report.partial_success is True
    assert report.all_failed is False
    assert report.vectors_upserted > 0
    assert "file:GOOD1" in report.manifest_entries
    assert "file:GOOD2" in report.manifest_entries
    assert "file:BAD1" not in report.manifest_entries


@pytest.mark.asyncio
async def test_all_documents_failing_reports_all_failed(tmp_path, monkeypatch) -> None:
    def _always_fails(self, text, **kwargs):
        raise RuntimeError("total failure")

    monkeypatch.setattr(ChunkPipeline, "run", _always_fails)

    config = _config(tmp_path)
    store = InMemoryVectorStore(config, "proj")
    writer = _writer(config, store)

    report = await writer.write(
        [_item("A", "body a"), _item("B", "body b")],
        snapshot_version="v1",
    )

    assert len(report.failed) == 2
    assert report.all_failed is True
    assert report.partial_success is False
    assert report.vectors_upserted == 0


@pytest.mark.asyncio
async def test_empty_items_returns_empty_report(tmp_path) -> None:
    config = _config(tmp_path)
    store = InMemoryVectorStore(config, "proj")
    writer = _writer(config, store)
    report = await writer.write([], snapshot_version="v1")
    assert report.chunks_created == 0
    assert report.vectors_upserted == 0
    assert report.failed == []
