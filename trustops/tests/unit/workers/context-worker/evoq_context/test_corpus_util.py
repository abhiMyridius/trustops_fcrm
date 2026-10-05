"""Tests for evoq_context/synthesis/corpus_util.py's proportional budget
allocation (execution-plan-bugfix-essentials.md, Bug fix #3).

Previously build_corpus_excerpt split its character budget evenly across
every document regardless of size — a single large document sharing space
with several small ones got the same tiny slice as everything else.
"""

from __future__ import annotations

from evoq_context.core.models import ContextItem
from evoq_context.synthesis.corpus_util import (
    build_corpus_excerpt,
)


def _item(source_id: str, body: str) -> ContextItem:
    return ContextItem(
        id=f"file:{source_id}",
        source_type="file",
        source_id=source_id,
        title=source_id,
        body=body,
        content_hash="hash",
        project="proj",
    )


def test_large_doc_gets_most_of_the_budget_not_an_even_slice() -> None:
    big_doc = _item("big", "x" * 49_000)
    small_docs = [_item(f"small{i}", "y" * 2_000) for i in range(9)]
    items = [big_doc, *small_docs]

    excerpt = build_corpus_excerpt(items, max_chars=60_000)

    # Old flat-split behavior would have given the big doc 60,000/10=6,000
    # chars. Proportional allocation should give it dramatically more.
    big_section = excerpt.split("### small0")[0]
    big_chars_included = big_section.count("x")
    assert big_chars_included > 30_000, (
        f"expected the large doc to get the bulk of the budget, "
        f"got only {big_chars_included} chars"
    )


def test_small_docs_still_get_their_floor_even_next_to_a_huge_doc() -> None:
    big_doc = _item("big", "x" * 200_000)
    small_doc = _item("small", "y" * 500)  # shorter than MIN_PER_DOC_CHARS
    items = [big_doc, small_doc]

    excerpt = build_corpus_excerpt(items, max_chars=60_000)

    assert "### small" in excerpt
    # Capped at its own length (500), not padded up to the floor with
    # nonexistent content.
    assert excerpt.count("y") == 500


def test_equal_size_docs_behave_like_an_even_split() -> None:
    items = [_item(f"doc{i}", "z" * 10_000) for i in range(5)]

    excerpt = build_corpus_excerpt(items, max_chars=30_000)

    # 30,000 / 5 = 6,000 chars each, same as the old flat-split formula
    # would have produced for equally-sized docs — no regression here.
    per_doc_z_count = excerpt.count("z") / 5
    assert 5_500 <= per_doc_z_count <= 6_500


def test_single_document_gets_close_to_the_full_budget() -> None:
    items = [_item("only", "w" * 200_000)]

    excerpt = build_corpus_excerpt(items, max_chars=60_000)

    assert excerpt.count("w") >= 59_000


def test_truncation_marker_present_when_a_doc_exceeds_its_allocation() -> None:
    items = [_item("only", "w" * 200_000)]

    excerpt = build_corpus_excerpt(items, max_chars=60_000)

    assert "truncated" in excerpt
    assert "200000 total chars" in excerpt


def test_empty_items_returns_empty_string() -> None:
    assert build_corpus_excerpt([]) == ""
