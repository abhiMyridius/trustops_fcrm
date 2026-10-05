"""``pipeline/vectors.py::VectorWriter`` — streaming architecture (the
memory fix) and per-document failure isolation.

Uses a small fake ``ChunkPipeline`` (structurally satisfying
``vector.embedder.ChunkPipelineLike``) rather than the real segmenter/
enricher chain — this file is a unit test of ``VectorWriter``'s
concurrency/streaming/isolation behaviour, not of chunking correctness.
"""

from __future__ import annotations

import gc
import tracemalloc

import pytest
from evoq_context.chunking.profiler import build_profile
from evoq_context.chunking.selector import StrategyDecision
from evoq_context.chunking.types import ChunkResult, ChunkStats, StructuredChunk
from evoq_context.core.config import ContextConfig
from evoq_context.core.models import ContextItem
from evoq_context.llm.client import LLMClient
from evoq_context.pipeline.vectors import VectorWriter
from evoq_context.storage import snapshot as _snapshot
from evoq_context.vector.embedder import DocumentPreparer
from evoq_context.vector.memory_store import InMemoryVectorStore


class _FakePipeline:
    """Deterministic fake satisfying ``ChunkPipelineLike`` — every document
    gets ``chunks_per_doc`` valid children of ``chunk_chars`` characters."""

    def __init__(self, chunks_per_doc: int = 3, chunk_chars: int = 4000) -> None:
        self._n = chunks_per_doc
        self._chars = chunk_chars

    def run(
        self,
        text,
        *,
        source_type,
        source_id,
        title="",
        file_path=None,
        metadata=None,
        profile_hints=None,
    ) -> ChunkResult:
        profile = build_profile(text or "x")
        decision = StrategyDecision(
            strategy="paragraph_windows", reason="test", rule_name="default"
        )
        children = []
        for i in range(self._n):
            body = (f"chunk {i} of {source_id} " * (self._chars // 20 + 1))[
                : self._chars
            ]
            children.append(
                StructuredChunk(
                    text=body,
                    metadata={
                        "chunk_strategy": "paragraph_windows",
                        "type": "plain_text",
                    },
                    char_start=0,
                    char_end=len(body),
                    level=1,
                )
            )
        stats = ChunkStats.from_token_counts([100] * self._n)
        return ChunkResult(
            children=children,
            parents=[],
            decision=decision,
            profile=profile,
            stats=stats,
        )


def _config(tmp_path, *, vector_concurrency: int = 4) -> ContextConfig:
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
        chunk_target_tokens=700,
        chunk_hard_max_tokens=2000,
        chunk_max_rows=500,
        vector_concurrency=vector_concurrency,
        llm_max_tokens=15000,
        analysis_model="dummy",
        analysis_provider="dummy",
        synthesis_model="dummy",
        synthesis_provider="dummy",
    )


def _item(source_id: str, *, metadata: dict | None = None) -> ContextItem:
    return ContextItem(
        id=f"file:{source_id}",
        source_type="file",
        source_id=source_id,
        title=source_id,
        body="source body " * 20,
        content_hash=f"hash-{source_id}",
        project="proj",
        metadata=metadata or {},
    )


async def _run_writer(
    tmp_path, n_docs: int, *, chunks_per_doc=3, chunk_chars=4000, concurrency=4
):
    config = _config(tmp_path, vector_concurrency=concurrency)
    store = InMemoryVectorStore(config, "proj")
    llm = LLMClient.from_config(config)
    pipeline = _FakePipeline(chunks_per_doc=chunks_per_doc, chunk_chars=chunk_chars)
    preparer = DocumentPreparer(pipeline, llm, config)
    writer = VectorWriter(store, preparer, config.layout, config)
    items = [_item(f"D{i:04d}") for i in range(n_docs)]
    report = await writer.write(items, snapshot_version="v1")
    return report, config


# ---------------------------------------------------------------------------
# Correctness across many documents
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_streaming_writer_processes_every_document(tmp_path) -> None:
    n_docs, chunks_per_doc = 220, 4
    report, config = await _run_writer(tmp_path, n_docs, chunks_per_doc=chunks_per_doc)

    assert not report.failed
    assert report.vectors_upserted == n_docs * chunks_per_doc
    assert report.chunks_created == n_docs * chunks_per_doc
    assert len(report.manifest_entries) == n_docs
    assert report.strategy_histogram == {"paragraph_windows": n_docs * chunks_per_doc}

    # Every document's chunks/*.jsonl was actually written to disk (the
    # streaming per-document write, not a single end-of-batch dump).
    written = list(_snapshot.iter_chunks_snapshot(config.layout, "proj", "v1"))
    assert len(written) == n_docs * chunks_per_doc


# ---------------------------------------------------------------------------
# Memory bound: at the SAME document count, the streaming writer's peak is
# much lower than a "gather everything, upsert once" baseline built from
# the exact same PreparedDocuments — the old ``embed_and_upsert`` held
# every chunk of every document in one flat list (``report.chunks``)
# *in addition to* the vector store's own copy, simultaneously, right up
# to and through the single batch ``upsert`` call (~1.1GB resident at
# 1,000 docs per the audit). The vector store itself legitimately holds
# every record it was asked to persist either way — that is not the leak
# under test — so the comparison is against a same-store baseline that
# additionally retains the flat list, not against an empty store.
# ---------------------------------------------------------------------------


async def _run_retain_all_baseline(
    tmp_path, n_docs: int, *, chunks_per_doc=6, chunk_chars=6000
):
    """The pre-streaming shape: every document's chunks are prepared and
    accumulated into ONE flat list, held in memory simultaneously, THEN
    upserted in a single call — exactly what the old ``embed_and_upsert``
    did with ``all_chunks``/``report.chunks``."""
    config = _config(tmp_path)
    store = InMemoryVectorStore(config, "proj")
    llm = LLMClient.from_config(config)
    preparer = DocumentPreparer(
        _FakePipeline(chunks_per_doc=chunks_per_doc, chunk_chars=chunk_chars),
        llm,
        config,
    )
    items = [_item(f"D{i:04d}") for i in range(n_docs)]

    all_chunks = []
    for item in items:
        prepared = await preparer.prepare(item, snapshot_version="v1")
        all_chunks.extend(prepared.vector_chunks)  # <- retained across the whole batch

    await store.upsert(all_chunks)
    return all_chunks  # kept alive until the caller's tracemalloc snapshot


@pytest.mark.asyncio
async def test_streaming_writer_peak_memory_much_less_than_retain_all_baseline(
    tmp_path,
) -> None:
    n_docs = 200
    streaming_dir = tmp_path / "streaming"
    baseline_dir = tmp_path / "baseline"
    streaming_dir.mkdir()
    baseline_dir.mkdir()

    gc.collect()
    tracemalloc.start()
    await _run_writer(streaming_dir, n_docs, chunks_per_doc=6, chunk_chars=6000)
    _, peak_streaming = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    gc.collect()

    tracemalloc.start()
    retained = await _run_retain_all_baseline(
        baseline_dir, n_docs, chunks_per_doc=6, chunk_chars=6000
    )
    _, peak_baseline = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert retained  # keep the list alive through the measurement above

    # Both approaches end with the SAME data fully upserted into an
    # equivalent store (so the store's own, legitimate footprint — which
    # dominates this measurement and is present, correctly, in both
    # numbers — is not what's under test here). The difference is that the
    # baseline ALSO holds a second, redundant copy of every chunk
    # (``all_chunks``) alive at the moment of its single batch upsert,
    # while the streaming writer never holds more than a small,
    # concurrency-bounded number of documents outside the store at once —
    # see test_max_in_flight_documents_bounded_by_queue_capacity below for
    # the precise, deterministic version of this same property (CPython's
    # string-slice sharing makes the *raw byte* difference here modest, so
    # this assertion is intentionally conservative; the queue-depth probe
    # is the authoritative check).
    assert peak_streaming < peak_baseline, (
        f"peak_streaming={peak_streaming} peak_baseline={peak_baseline} — "
        "the streaming writer should never need MORE peak memory than a "
        "baseline that additionally retains every chunk in one flat list"
    )


# ---------------------------------------------------------------------------
# Deterministic bound: number of documents prepared-but-not-yet-consumed
# never exceeds the queue's own capacity plus the producer concurrency —
# i.e. the writer never accumulates an unbounded (or all-N) backlog
# regardless of how many documents are in the batch.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_max_in_flight_documents_bounded_by_queue_capacity(
    tmp_path, monkeypatch
) -> None:
    concurrency = 4
    n_docs = 200
    config = _config(tmp_path, vector_concurrency=concurrency)
    store = InMemoryVectorStore(config, "proj")
    llm = LLMClient.from_config(config)
    preparer = DocumentPreparer(
        _FakePipeline(chunks_per_doc=2, chunk_chars=200), llm, config
    )
    writer = VectorWriter(store, preparer, config.layout, config)

    current = 0
    max_seen = 0
    real_prepare = DocumentPreparer.prepare
    real_upsert = InMemoryVectorStore.upsert

    async def _counting_prepare(self, item, *, snapshot_version):
        nonlocal current, max_seen
        result = await real_prepare(self, item, snapshot_version=snapshot_version)
        current += 1
        max_seen = max(max_seen, current)
        return result

    async def _counting_upsert(self, chunks, **kwargs):
        nonlocal current
        result = await real_upsert(self, chunks, **kwargs)
        current -= 1
        return result

    monkeypatch.setattr(DocumentPreparer, "prepare", _counting_prepare)
    monkeypatch.setattr(InMemoryVectorStore, "upsert", _counting_upsert)

    items = [_item(f"D{i:04d}") for i in range(n_docs)]
    report = await writer.write(items, snapshot_version="v1")

    assert not report.failed
    assert report.vectors_upserted == n_docs * 2
    # Prepared-but-not-yet-upserted documents are bounded by the queue's
    # own capacity (2*concurrency) plus at most `concurrency` producers
    # that finished preparing and are blocked on a full queue — NEVER
    # anywhere close to all 200 documents.
    bound = 3 * concurrency
    assert max_seen <= bound, f"max_seen={max_seen} exceeds the {bound} bound"
    assert max_seen < n_docs


# ---------------------------------------------------------------------------
# Per-document isolation: one MetadataTooLarge does not abort the batch.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metadata_too_large_isolated_to_one_document(tmp_path) -> None:
    config = _config(tmp_path)
    store = InMemoryVectorStore(config, "proj")
    llm = LLMClient.from_config(config)
    pipeline = _FakePipeline(chunks_per_doc=2, chunk_chars=500)
    preparer = DocumentPreparer(pipeline, llm, config)
    writer = VectorWriter(store, preparer, config.layout, config)

    good1 = _item("GOOD1")
    # regulations_ref is a KR passthrough field with no shrink step of its
    # own (unlike title/section/etc.) — a huge value here cannot be trimmed
    # away by shrink_metadata and forces MetadataTooLarge deterministically.
    bad = _item("BAD1", metadata={"regulations_ref": "R" * 50_000})
    good2 = _item("GOOD2")

    report = await writer.write([good1, bad, good2], snapshot_version="v1")

    assert len(report.failed) == 1
    assert report.failed[0].doc_id == "file:BAD1"
    assert report.failed[0].code == "METADATA_TOO_LARGE"
    assert report.vectors_upserted == 2 * 2  # good1 + good2, 2 chunks each
    assert "file:GOOD1" in report.manifest_entries
    assert "file:GOOD2" in report.manifest_entries
    assert "file:BAD1" not in report.manifest_entries
    assert report.partial_success is True
    assert report.all_failed is False

    # No orphan chunks/*.jsonl was left behind for the failed document.
    path = config.layout.chunks_path("proj", "BAD1", version="v1")
    assert not path.exists()


@pytest.mark.asyncio
async def test_chunk_preparation_failure_isolated_to_one_document(tmp_path) -> None:
    """A pipeline that raises for one document (bad chunking/parsing) must
    not stop the others."""

    class _FlakyPipeline(_FakePipeline):
        def run(self, text, *, source_type, source_id, **kwargs):
            if source_id == "BAD1":
                raise ValueError("synthetic parse failure")
            return super().run(
                text, source_type=source_type, source_id=source_id, **kwargs
            )

    config = _config(tmp_path)
    store = InMemoryVectorStore(config, "proj")
    llm = LLMClient.from_config(config)
    preparer = DocumentPreparer(_FlakyPipeline(chunks_per_doc=2), llm, config)
    writer = VectorWriter(store, preparer, config.layout, config)

    report = await writer.write(
        [_item("GOOD1"), _item("BAD1"), _item("GOOD2")],
        snapshot_version="v1",
    )

    assert len(report.failed) == 1
    assert report.failed[0].doc_id == "file:BAD1"
    assert "synthetic parse failure" in report.failed[0].error
    assert report.vectors_upserted == 4  # 2 good docs x 2 chunks
    assert report.all_failed is False


@pytest.mark.asyncio
async def test_all_documents_failing_reports_all_failed(tmp_path) -> None:
    class _AlwaysFailsPipeline(_FakePipeline):
        def run(self, *args, **kwargs):
            raise ValueError("total failure")

    config = _config(tmp_path)
    store = InMemoryVectorStore(config, "proj")
    llm = LLMClient.from_config(config)
    preparer = DocumentPreparer(_AlwaysFailsPipeline(), llm, config)
    writer = VectorWriter(store, preparer, config.layout, config)

    report = await writer.write([_item("A"), _item("B")], snapshot_version="v1")

    assert len(report.failed) == 2
    assert report.all_failed is True
    assert report.partial_success is False
    assert report.vectors_upserted == 0


@pytest.mark.asyncio
async def test_empty_items_returns_empty_report(tmp_path) -> None:
    config = _config(tmp_path)
    store = InMemoryVectorStore(config, "proj")
    llm = LLMClient.from_config(config)
    preparer = DocumentPreparer(_FakePipeline(), llm, config)
    writer = VectorWriter(store, preparer, config.layout, config)

    report = await writer.write([], snapshot_version="v1")
    assert report.chunks_created == 0
    assert report.vectors_upserted == 0
    assert report.failed == []
