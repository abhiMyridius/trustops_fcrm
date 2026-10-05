"""Tests for ``chunking/parent_child.py`` and the Finalizer's two-pass keys.

Contiguity (children ``0..N-1``, parents ``N..N+P-1``) and ``parent_key``
resolving via the frozen ``vector_key`` formula are exercised end-to-end
through ``smart_chunk``/``ChunkPipeline`` (the guarantee the MCP's
small-to-big retrieval relies on); ``ParentChildSplitter`` itself is
exercised directly with synthetic segments for the no-parent and
oversized-split cases, which are easiest to construct precisely by hand.
"""

from __future__ import annotations

import pytest
from evoq_context.chunking.budget import ChunkBudget
from evoq_context.chunking.enrichers.finalize import Finalizer
from evoq_context.chunking.index import DocumentIndex
from evoq_context.chunking.markers import compile_requirement_pattern
from evoq_context.chunking.parent_child import ParentChildConfig, ParentChildSplitter
from evoq_context.chunking.pipeline import ChunkPipeline
from evoq_context.chunking.selector import StrategyDecision
from evoq_context.chunking.tokens import get_estimator
from evoq_context.chunking.topic import TopicConfig
from evoq_context.chunking.types import Segment, SourceDocument, StructuredChunk
from evoq_context.core import limits
from evoq_context.core.config import ChunkingConfig
from evoq_context.core.errors import ChunkLimitExceeded
from evoq_context.core.models import compute_content_hash
from evoq_context.vector.contract import vector_key

REQ_PATTERN = compile_requirement_pattern(limits.DEFAULT_REQUIREMENT_ID_PATTERN)
ESTIMATOR = get_estimator("tiktoken")


def _doc(text: str) -> SourceDocument:
    return SourceDocument(
        text=text,
        source_type="file",
        source_id="doc-1",
        title="",
        file_path=None,
        metadata={},
        profile=None,  # type: ignore[arg-type]
        index=DocumentIndex.build(text, REQ_PATTERN),
        requirement_pattern=REQ_PATTERN,
    )


def _child(
    text: str, start: int, end: int, *, segment_ordinal: int = 0
) -> StructuredChunk:
    return StructuredChunk(
        text=text, char_start=start, char_end=end, segment_ordinal=segment_ordinal
    )


# ---------------------------------------------------------------------------
# ParentChildSplitter — direct, synthetic-segment tests
# ---------------------------------------------------------------------------


def test_single_identical_child_produces_no_parent() -> None:
    text = "A short section that fits in one child chunk verbatim."
    segment = Segment(0, len(text), text, "prose", parent_kind="section")
    child = _child(text, 0, len(text))

    parents = ParentChildSplitter(ParentChildConfig(), ESTIMATOR).split(
        [(segment, [child])]
    )

    assert parents == []
    assert "_parent_ordinal" not in child.metadata


def test_normal_segment_gets_one_parent_linking_its_children() -> None:
    text = "First half of the section. " * 20 + "Second half of the section. " * 20
    segment = Segment(0, len(text), text, "prose", parent_kind="section")
    mid = len(text) // 2
    child_a = _child(text[:mid], 0, mid)
    child_b = _child(text[mid:], mid, len(text))

    parents = ParentChildSplitter(
        ParentChildConfig(parent_max_tokens=5000), ESTIMATOR
    ).split([(segment, [child_a, child_b])])

    assert len(parents) == 1
    assert parents[0].text == text
    assert parents[0].level == 0
    assert child_a.metadata["_parent_ordinal"] == 0
    assert child_b.metadata["_parent_ordinal"] == 0


def test_oversized_segment_splits_into_multiple_parents_at_child_boundaries() -> None:
    # Each child ~ a few hundred tokens; parent_max_tokens is small enough
    # that the segment must become more than one parent, and every parent
    # boundary must land exactly on a child boundary (never mid-child).
    unit = "word " * 60  # ~60 tokens
    children_texts = [unit for _ in range(20)]
    full_text = "".join(children_texts)
    segment = Segment(0, len(full_text), full_text, "prose", parent_kind="section")

    children: list[StructuredChunk] = []
    cursor = 0
    for t in children_texts:
        children.append(_child(t, cursor, cursor + len(t)))
        cursor += len(t)

    cfg = ParentChildConfig(parent_max_tokens=300)  # forces multiple parents
    parents = ParentChildSplitter(cfg, ESTIMATOR).split([(segment, children)])

    assert len(parents) > 1
    # Every parent's span starts/ends exactly on a child's start/end.
    child_starts = {c.char_start for c in children}
    child_ends = {c.char_end for c in children}
    for parent in parents:
        assert parent.char_start in child_starts
        assert parent.char_end in child_ends
    # Parents are contiguous and cover the whole segment.
    ordered = sorted(parents, key=lambda p: p.char_start)
    assert ordered[0].char_start == segment.start
    assert ordered[-1].char_end == segment.end
    for prev, nxt in zip(ordered, ordered[1:], strict=False):
        assert prev.char_end == nxt.char_start
    # Every child is linked to exactly one parent, in order.
    ordinals = [c.metadata["_parent_ordinal"] for c in children]
    assert ordinals == sorted(ordinals)


def test_disabled_config_produces_no_parents() -> None:
    text = "Some section text that would otherwise get a parent."
    segment = Segment(0, len(text), text, "prose", parent_kind="section")
    child = _child(text, 0, len(text))

    parents = ParentChildSplitter(ParentChildConfig(enabled=False), ESTIMATOR).split(
        [(segment, [child])]
    )

    assert parents == []


# ---------------------------------------------------------------------------
# Finalizer — ChunkLimitExceeded
# ---------------------------------------------------------------------------


def test_chunk_limit_exceeded_raised_for_too_many_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(limits, "MAX_CHUNKS_PER_DOCUMENT", 10)
    doc = _doc("irrelevant")
    decision = StrategyDecision(
        strategy="paragraph_windows", reason="default", rule_name="default"
    )
    children = [_child(f"c{i}", 0, 2) for i in range(6)]
    parents = [_child(f"p{i}", 0, 2) for i in range(6)]
    finalizer = Finalizer(doc, decision, ESTIMATOR, content_hash="deadbeef")

    with pytest.raises(ChunkLimitExceeded):
        finalizer.finalize(children, parents)


# ---------------------------------------------------------------------------
# End-to-end via smart_chunk: contiguity + parent_key resolution
# ---------------------------------------------------------------------------


def test_children_contiguous_and_parent_keys_resolve_via_vector_key() -> None:
    sections = [f"## Section {i}\n\n" + ("word " * 120) for i in range(4)]
    body = "# Title\n\n" + "\n\n".join(sections)

    cfg = ChunkingConfig(
        tokenizer="tiktoken",
        tokenizer_fallback="error",
        budget=ChunkBudget(target=40, overlap=0, min=0, hard_max=200).validate(),
        max_rows=500,
        rules_json="",
        requirement_pattern=REQ_PATTERN,
        parent_child=ParentChildConfig(parent_max_tokens=150),
        topic=TopicConfig(),
    )
    result = ChunkPipeline(cfg).run(body, source_type="file", source_id="doc.md")
    children, parents = result.children, result.parents
    assert parents, (
        "expected at least one stored-only parent for this oversized document"
    )

    child_indices = sorted(int(c.metadata["chunk_index"]) for c in children)
    assert child_indices == list(range(len(children)))

    parent_indices = sorted(int(p.metadata["chunk_index"]) for p in parents)
    assert parent_indices == list(range(len(children), len(children) + len(parents)))

    doc_id = "file:doc.md"
    content_hash = compute_content_hash(body.encode("utf-8"))
    parent_by_key = {
        vector_key(doc_id, content_hash, int(p.metadata["chunk_index"])): p
        for p in parents
    }
    linked = [c for c in children if c.metadata.get("parent_key")]
    assert linked, "expected at least one child to carry a parent_key"
    for child in linked:
        assert child.metadata["parent_key"] in parent_by_key
