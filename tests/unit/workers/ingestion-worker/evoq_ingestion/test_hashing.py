"""Tests for core/hashing.py — canonicalize, content_hash, source_id, metadata_hash."""

from __future__ import annotations

from evoq_ingestion.core.hashing import (
    build_source_key,
    canonicalize,
    compute_content_hash,
    compute_metadata_hash,
    compute_source_id,
)


class TestCanonicalize:
    def test_crlf_to_lf(self):
        assert canonicalize("a\r\nb\r\n") == "a\nb\n"

    def test_rstrips_each_line(self):
        assert canonicalize("a   \nb\t\n") == "a\nb\n"

    def test_exactly_one_trailing_newline(self):
        assert canonicalize("a\n\n\n") == "a\n"
        assert canonicalize("a") == "a\n"

    def test_empty_string(self):
        assert canonicalize("") == "\n"


class TestContentHash:
    def test_stable_for_equivalent_whitespace(self):
        h1 = compute_content_hash("hello\r\nworld  \n")
        h2 = compute_content_hash("hello\nworld\n\n\n")
        assert h1 == h2

    def test_changes_when_content_changes(self):
        assert compute_content_hash("a") != compute_content_hash("b")

    def test_prefix(self):
        assert compute_content_hash("x").startswith("sha256:")


class TestSourceId:
    def test_stable_across_calls(self):
        a = compute_source_id("PRJ1", "confluence:123")
        b = compute_source_id("PRJ1", "confluence:123")
        assert a == b

    def test_prefix_and_length(self):
        sid = compute_source_id("PRJ1", "confluence:123")
        assert sid.startswith("SRC")
        assert len(sid) == len("SRC") + 32

    def test_differs_by_origin(self):
        a = compute_source_id("PRJ1", "confluence:123")
        b = compute_source_id("PRJ2", "confluence:123")
        assert a != b


class TestMetadataHash:
    def test_stable_for_same_payload(self):
        kwargs = {
            "labels": ["a", "b"],
            "hierarchy": {"x": 1},
            "authors": [{"name": "A"}],
            "source_version": 3,
            "metadata": {"k": "v"},
        }
        assert compute_metadata_hash(**kwargs) == compute_metadata_hash(**kwargs)

    def test_order_independent_within_dict(self):
        h1 = compute_metadata_hash(
            labels=[],
            hierarchy={"a": 1, "b": 2},
            authors=[],
            source_version=1,
            metadata={},
        )
        h2 = compute_metadata_hash(
            labels=[],
            hierarchy={"b": 2, "a": 1},
            authors=[],
            source_version=1,
            metadata={},
        )
        assert h1 == h2

    def test_changes_when_labels_change(self):
        h1 = compute_metadata_hash(
            labels=["a"], hierarchy={}, authors=[], source_version=1, metadata={}
        )
        h2 = compute_metadata_hash(
            labels=["b"], hierarchy={}, authors=[], source_version=1, metadata={}
        )
        assert h1 != h2


class TestBuildSourceKey:
    def test_confluence(self):
        assert build_source_key("confluence", page_id="123") == "confluence:123"

    def test_jira(self):
        assert build_source_key("jira", issue_id="10001") == "jira:10001"

    def test_file_with_doc_id(self):
        assert build_source_key("file", doc_id="ANB-1") == "file:ANB-1"

    def test_file_without_doc_id_uses_name(self):
        assert build_source_key("file", file_name="a.txt") == "file:name:a.txt"

    def test_missing_required_raises(self):
        import pytest

        with pytest.raises(ValueError):
            build_source_key("confluence")
        with pytest.raises(ValueError):
            build_source_key("file")
        with pytest.raises(ValueError):
            build_source_key("unknown_type", doc_id="x")
