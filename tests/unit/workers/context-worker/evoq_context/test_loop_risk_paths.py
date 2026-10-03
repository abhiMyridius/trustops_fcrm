from __future__ import annotations

from pathlib import Path

from evoq_context.chunking.budget import ChunkBudget
from evoq_context.chunking.index import RowSpan, TableBlock
from evoq_context.chunking.pipeline import smart_chunk
from evoq_context.chunking.tables import batch_rows
from evoq_context.chunking.tokens import get_estimator
from evoq_context.core.config import ContextConfig
from evoq_context.vector.s3_vectors import S3VectorsStore


def test_chunking_large_plain_text_is_bounded(tmp_path: Path) -> None:
    text = "\n\n".join([f"Paragraph {i} " + ("word " * 120) for i in range(300)])
    chunks = smart_chunk(
        text=text,
        file_path=tmp_path / "big.txt",
        max_tokens=200,
        max_rows_per_chunk=10,
    )

    assert chunks
    assert len(chunks) < 1200
    assert all(chunk.text for chunk in chunks)


def test_tabular_chunking_batches_are_bounded() -> None:
    """Superseded ``_chunk_rows`` (deleted, lane C-C) -> ``tables.batch_rows``
    (lane C-B), the real row-batcher every segmenter routes tables through."""
    data_rows = tuple(
        RowSpan(start=i, end=i + 1, cells=(str(i), str(i + 1))) for i in range(501)
    )
    table = TableBlock(
        start=0,
        end=502,
        header_line="| c1 | c2 |",
        sep_line="| --- | --- |",
        header_cells=("c1", "c2"),
        data_rows=data_rows,
    )
    budget = ChunkBudget(target=100_000, overlap=0, min=0, hard_max=200_000)
    batches = batch_rows(
        table, budget, max_rows=100, estimator=get_estimator("tiktoken")
    )

    assert len(batches) == 6
    assert all(batch.row_count <= 100 for batch in batches)


def test_metadata_shrinking_converges_under_limit(tmp_path: Path) -> None:
    config = ContextConfig(
        projects_root=tmp_path,
        vector_backend="memory",
        s3v_bucket="bucket",
        s3v_region="us-east-1",
        s3v_index_prefix="context",
        embedding_model="amazon.titan-embed-text-v2:0",
        embedding_provider="dummy",
        embedding_dimension=8,
        embedding_max_input_tokens=50_000,
        embedding_input_safety=1.0,
        chunk_target_tokens=5000,
        chunk_hard_max_tokens=50_000,
        chunk_max_rows=500,
        llm_max_tokens=15000,
        analysis_model="dummy",
        analysis_provider="dummy",
        synthesis_model="dummy",
        synthesis_provider="dummy",
    )
    store = S3VectorsStore(config, project="proj")
    metadata = {
        "text": "x" * 80_000,
        "title": "y" * 20_000,
        "source_path": "z" * 20_000,
        "section_path": "w" * 20_000,
        "doc_id": "jira:SRC1",
        "project": "proj",
    }

    shrunk = store._shrink_metadata(metadata)
    size = store._metadata_size_bytes(shrunk)

    assert size <= 40_960
    assert shrunk.get("doc_id") == "jira:SRC1"
