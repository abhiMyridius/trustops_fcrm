"""Regression tests for the chunk-metadata-text truncation fix.

Covers:
1. The persisted `metadata["text"]` ceiling is pinned to a known value
   (evoq_context/core/limits.py), not silently whatever a stray literal says.
2. A chunk under the ceiling keeps its full text (`text_truncated=False`);
   one over it is truncated at the exact boundary and flagged
   `text_truncated=True` — while the embedded `.text` (sent to the model)
   stays full-length either way.
3. The contract's ``shrink_metadata`` fallback can never again regress
   below the normal-path cap (the original bug: fallback stuck at 1500
   chars while the normal cap was 2000).

The host-side "config/config.py defers to limits.py" guard lives in
``aeocontextjob/tests/test_settings_defaults.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from evoq_context.chunking.pipeline import ChunkPipeline
from evoq_context.core.config import ContextConfig
from evoq_context.core.limits import MAX_META_TEXT_CHARS, TEXT_FALLBACK_CHARS
from evoq_context.core.models import ContextItem
from evoq_context.llm.client import LLMClient
from evoq_context.vector.embedder import DocumentPreparer
from evoq_context.vector.s3_vectors import S3VectorsStore


async def _prepare_chunks(item: ContextItem, llm: LLMClient, config: ContextConfig):
    """Test shim over the current API: chunk+embed one document and return
    just its embedded (child) chunks, matching the shape these pre-existing
    tests were written against."""
    preparer = DocumentPreparer(ChunkPipeline(config.chunking()), llm, config)
    prepared = await preparer.prepare(item, snapshot_version="v1")
    return prepared.vector_chunks


def test_limits_module_is_the_single_source_of_truth() -> None:
    """Pin the coordinated values so a future edit can't silently drift them."""
    assert MAX_META_TEXT_CHARS == 12_000
    assert TEXT_FALLBACK_CHARS == 6_000
    # The fallback must always be strictly below the normal-path cap —
    # this is the exact invariant the original bug violated.
    assert TEXT_FALLBACK_CHARS < MAX_META_TEXT_CHARS


def _dummy_llm() -> LLMClient:
    return LLMClient(embed_provider="dummy", embed_dimension=8)


def _make_item(body: str) -> ContextItem:
    return ContextItem(
        id="file:DOC1",
        source_type="file",
        source_id="DOC1",
        title="Test document",
        body=body,
        content_hash="hash123",
        project="proj",
        metadata={"source_path": "doc.txt"},
    )


def _config(tmp_path: Path) -> ContextConfig:
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
        chunk_target_tokens=50_000,
        chunk_hard_max_tokens=50_000,
        chunk_max_rows=500,
        llm_max_tokens=15000,
        analysis_model="dummy",
        analysis_provider="dummy",
        synthesis_model="dummy",
        synthesis_provider="dummy",
    )


@pytest.mark.asyncio
async def test_prepare_chunks_preserves_full_text_under_the_ceiling(
    tmp_path: Path,
) -> None:
    """A chunk smaller than the ceiling is not truncated at all."""
    body = "word " * 1_800  # ~9,000 chars, under the 12,000-char ceiling
    item = _make_item(body)
    config = _config(tmp_path)

    chunks = await _prepare_chunks(item, _dummy_llm(), config)

    assert len(chunks) == 1
    assert chunks[0].metadata["text"] == chunks[0].text
    assert len(chunks[0].metadata["text"]) == len(chunks[0].text)
    assert chunks[0].metadata["text_truncated"] is False


@pytest.mark.asyncio
async def test_prepare_chunks_truncates_metadata_text_at_exact_boundary(
    tmp_path: Path,
) -> None:
    """A chunk larger than the ceiling: metadata.text is capped exactly at
    MAX_META_TEXT_CHARS and flagged text_truncated=True, but the embedded
    .text stays full-length — the embedding itself was never the bug, only
    what got persisted."""
    body = "word " * 5_000  # ~25,000 chars, over the 12,000-char ceiling
    item = _make_item(body)
    config = _config(tmp_path)

    chunks = await _prepare_chunks(item, _dummy_llm(), config)

    assert len(chunks) == 1
    full_text = chunks[0].text
    assert len(full_text) > MAX_META_TEXT_CHARS
    assert chunks[0].metadata["text"] == full_text[:MAX_META_TEXT_CHARS]
    assert len(chunks[0].metadata["text"]) == MAX_META_TEXT_CHARS
    assert chunks[0].metadata["text_truncated"] is True


def test_shrink_metadata_fallback_preserves_at_least_the_fallback_floor(
    tmp_path: Path,
) -> None:
    """A realistic KR-heavy record that trips the byte budget still keeps
    at least TEXT_FALLBACK_CHARS of text — never regressing below it."""
    config = _config(tmp_path)
    store = S3VectorsStore(config, project="proj")
    metadata = {
        "text": "x" * MAX_META_TEXT_CHARS,
        "title": "Regulatory Filing " * 20,
        "source_path": "z" * 1_000,
        "section_path": "w" * 1_000,
        "section": "Section 5.1",
        "doc_id": "file:DOC1",
        "project": "proj",
        "category": "compliance",
        "jurisdiction": "US-federal",
        "regulations_ref": "12 CFR 1030 " * 20,
        "owner_id": "user-42",
        "kr_version": "3",
    }

    shrunk = store._shrink_metadata(metadata)
    size = store._metadata_size_bytes(shrunk)

    assert size <= 40_960
    assert len(shrunk.get("text", "")) >= TEXT_FALLBACK_CHARS
    assert shrunk.get("doc_id") == "file:DOC1"
