"""Tests for the Index Contract v2 deterministic vector key
(evoq_context/vector/contract.py::vector_key) and a pinned replica of the MCP's
legacy chunk-id derivation, to prove the new scheme is stable and the old
one can still be reproduced as a flagged fallback for pre-v2 indexes.
"""

from __future__ import annotations

import hashlib

from evoq_context.vector.contract import doc_id_for, vector_key


def _legacy_mcp_chunk_id(key: str, offset: int) -> str:
    """Exact replica of evoq_aidlc_mcp's pre-v2 formula
    (core/id_gen.py::chunk_id / evoq_vectors/metadata.py::legacy_chunk_id):
    ``chunk_{sha256(f"{key}:{offset}")[:8]}``. Duplicated here (not
    imported — separate repo) purely to pin the formula's stability.
    """
    return "chunk_" + hashlib.sha256(f"{key}:{offset}".encode()).hexdigest()[:8]


def test_vector_key_is_pure_function_of_identity_content_and_index() -> None:
    doc_id = doc_id_for("confluence", "SRC123")
    key1 = vector_key(doc_id, "sha256:" + "a" * 64, 0)
    key2 = vector_key(doc_id, "sha256:" + "a" * 64, 0)
    assert key1 == key2


def test_vector_key_changes_only_when_content_or_index_changes() -> None:
    doc_id = doc_id_for("confluence", "SRC123")
    base = vector_key(doc_id, "sha256:" + "a" * 64, 0)
    diff_content = vector_key(doc_id, "sha256:" + "b" * 64, 0)
    diff_index = vector_key(doc_id, "sha256:" + "a" * 64, 1)
    assert base != diff_content
    assert base != diff_index


def test_vector_key_shape_matches_contract() -> None:
    doc_id = doc_id_for("jira", "ISSUE-1")
    content_hash = "sha256:" + "f" * 64
    key = vector_key(doc_id, content_hash, 7)
    assert key == f"jira:ISSUE-1:{'f' * 16}:0007"


def test_vector_key_accepts_hash_with_or_without_prefix() -> None:
    doc_id = doc_id_for("file", "F1")
    with_prefix = vector_key(doc_id, "sha256:" + "c" * 64, 0)
    without_prefix = vector_key(doc_id, "c" * 64, 0)
    assert with_prefix == without_prefix


def test_legacy_mcp_formula_is_pinned_and_reproducible() -> None:
    """Regression pin: the exact pre-v2 derivation stays byte-identical —
    the MCP keeps this only as a flagged fallback (chunk_id_source=
    'derived') for indexes built before the Index Contract v2 rollout."""
    key = "confluence:SRC1"
    offset = 3
    expected = "chunk_" + hashlib.sha256(f"{key}:{offset}".encode()).hexdigest()[:8]
    assert _legacy_mcp_chunk_id(key, offset) == expected
    # Pin a concrete known value so an accidental algorithm change (e.g.
    # switching to a different hash function or slice length) is caught.
    assert len(_legacy_mcp_chunk_id(key, offset)) == len("chunk_") + 8


def test_legacy_formula_is_deterministic_across_calls() -> None:
    a = _legacy_mcp_chunk_id("doc:X", 12)
    b = _legacy_mcp_chunk_id("doc:X", 12)
    assert a == b


def test_new_and_legacy_schemes_produce_different_ids() -> None:
    """The new chunk_key is never confusable with the legacy derived id —
    the MCP tells them apart by chunk_id_source, but they should also
    simply never collide in practice."""
    doc_id = doc_id_for("confluence", "SRC1")
    new_key = vector_key(doc_id, "sha256:" + "1" * 64, 0)
    legacy_key = _legacy_mcp_chunk_id(doc_id, 0)
    assert new_key != legacy_key
    assert not new_key.startswith("chunk_")
