"""Rewritten for the ``ChunkPipeline``/``smart_chunk`` façade (lane C-C).

The old strategy-function-level tests (``heading_sections``,
``paragraph_windows``, ``table_aware``, ...) moved to lane C-B's
``test_segmenters.py``/``test_tables.py``, which test the real
``Segmenter`` classes directly. What belongs here — the façade's own
contract — is: (1) there is no file-extension override left anywhere (the
original bug this whole effort exists to fix), (2) the hard-max guarantee
holds end-to-end through the full pipeline even on whitespace-free
CJK/base64 content, and (3) ``smart_chunk``'s pre-redesign positional/
keyword signature still works unchanged for existing callers.
"""

from __future__ import annotations

from pathlib import Path

from evoq_context.chunking import smart_chunk
from evoq_context.chunking.tokens import get_estimator

ESTIMATOR = get_estimator("tiktoken")


# ---------------------------------------------------------------------------
# No extension-based override anywhere (P0-4 / the original bug)
# ---------------------------------------------------------------------------


def test_pdf_extension_does_not_force_page_windows_for_non_page_heavy_body() -> None:
    """A ``.pdf`` file_path with a body that has no page markers and few
    headings must be chunked exactly like any other document — the
    selector's decision is final, never overridden by the extension."""
    body = "Just a short paragraph of ordinary prose with no structure at all. " * 3
    chunks = smart_chunk(body, Path("report.pdf"), max_tokens=200, overlap_tokens=0)

    assert chunks
    assert all(c.metadata["chunk_strategy"] != "page_windows" for c in chunks)
    assert all(c.metadata["chunk_strategy"] == "paragraph_windows" for c in chunks)


def test_xlsx_extension_does_not_force_table_aware_for_non_tabular_body() -> None:
    """A ``.xlsx`` file_path whose body has no tables/sheet markers must not
    be forced into ``table_aware`` just because of the extension."""
    body = "Prose describing something, with no table markup whatsoever. " * 3
    chunks = smart_chunk(body, Path("book.xlsx"), max_tokens=200, overlap_tokens=0)

    assert chunks
    assert all(c.metadata["chunk_strategy"] != "table_aware" for c in chunks)


def test_no_extension_branch_exists_in_pipeline_module() -> None:
    """Static guard: no extension-forced-strategy code anywhere in the new
    façade — the whole point of this lane. Checked via the AST (not a text
    grep) so this module's own docstring mentioning ``.pdf`` in prose,
    while explaining what was deleted, doesn't trip the check."""
    import ast
    import inspect

    from evoq_context.chunking import pipeline

    tree = ast.parse(inspect.getsource(pipeline))
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert "_PDF_EXTENSIONS" not in names
    assert "_TABULAR_EXTENSIONS" not in names
    string_literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert ".pdf" not in string_literals
    assert ".xlsx" not in string_literals


# ---------------------------------------------------------------------------
# Hard-max guarantee end-to-end through the real pipeline (C-A's HardMaxGuard)
# ---------------------------------------------------------------------------


def test_smart_chunk_cjk_whitespace_free_respects_hard_max() -> None:
    body = "这是一个没有空格的中文测试文档用来验证硬上限。" * 400
    chunks = smart_chunk(
        body,
        Path("doc.md"),
        max_tokens=50,
        overlap_tokens=0,
        min_tokens=0,
        hard_max_tokens=80,
    )

    assert chunks
    assert all(ESTIMATOR.count(c.text) <= 80 for c in chunks)


def test_smart_chunk_base64_blob_respects_hard_max() -> None:
    body = (
        "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVphYmNkZWZnaGlqa2xtbm9wcXJzdHV2d3h5eg==" * 300
    )
    chunks = smart_chunk(
        body,
        Path("doc.md"),
        max_tokens=50,
        overlap_tokens=0,
        min_tokens=0,
        hard_max_tokens=80,
    )

    assert chunks
    assert all(ESTIMATOR.count(c.text) <= 80 for c in chunks)


# ---------------------------------------------------------------------------
# smart_chunk's exact old signature still works
# ---------------------------------------------------------------------------


def test_smart_chunk_old_signature_still_works() -> None:
    body = "# Doc\n\nSome text here for a plain document with no special structure."
    chunks = smart_chunk(body, Path("doc.md"), max_tokens=100, max_rows_per_chunk=10)

    assert chunks
    for c in chunks:
        assert c.metadata.get("chunk_strategy")
        assert c.metadata.get("strategy_reason")
        assert c.metadata.get("type")


def test_smart_chunk_all_keyword_args_still_accepted() -> None:
    body = "# Doc\n\n" + ("word " * 200)
    chunks = smart_chunk(
        body,
        Path("doc.md"),
        100,
        10,
        source_type="file",
        metadata={"k": "v"},
        profile_hints=None,
        overlap_tokens=10,
        min_tokens=5,
        hard_max_tokens=150,
        tokenizer="tiktoken",
        requirement_id_pattern=r"\b([A-Z]+-\d+)\b",
        rules_json=None,
    )
    assert chunks
    assert all(ESTIMATOR.count(c.text) <= 150 for c in chunks)


def test_smart_chunk_empty_body_returns_no_chunks() -> None:
    assert smart_chunk("", Path("doc.md")) == []
    assert smart_chunk("   \n\n  ", Path("doc.md")) == []
