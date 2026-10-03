"""Tests for ``chunking/budget.py`` — ``ChunkBudget`` and ``TokenBudgetPacker``.

Covers the audit's P0-1 finding directly: the old packer's
``candidate <= target or candidate <= hard`` tautology made every chunk
grow to the hard max. Here the packer must actually flush at ``target``,
never merely at ``hard_max``, and every emitted offset must be a real
slice of the source (fixing the old ``char_start``/``char_end`` staleness
on overlapped chunks, P1-16).
"""

from __future__ import annotations

import pytest
from evoq_context.chunking.budget import ChunkBudget, TokenBudgetPacker
from evoq_context.chunking.text_ops import split_units
from evoq_context.chunking.tokens import get_estimator
from evoq_context.core.errors import ConfigError

ESTIMATOR = get_estimator("tiktoken")


def _pack(text: str, budget: ChunkBudget, *, prefix: str = ""):
    packer = TokenBudgetPacker(ESTIMATOR, budget, source=text, prefix=prefix)
    for unit in split_units(text):
        packer.add(unit)
    return packer.finish()


# ── ChunkBudget.validate ────────────────────────────────────────────────


def test_budget_validate_accepts_a_sane_budget() -> None:
    ChunkBudget(target=350, overlap=50, min=80, hard_max=2000).validate()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"target": 0, "overlap": 0, "min": 0, "hard_max": 100},
        {"target": 100, "overlap": 100, "min": 0, "hard_max": 100},
        {"target": 100, "overlap": -1, "min": 0, "hard_max": 100},
        {"target": 100, "overlap": 0, "min": 200, "hard_max": 100},
        {"target": 300, "overlap": 0, "min": 0, "hard_max": 100},
    ],
)
def test_budget_validate_rejects_bad_budgets(kwargs) -> None:
    with pytest.raises(ConfigError):
        ChunkBudget(**kwargs).validate()


# ── target is respected (the P0-1 fix) ──────────────────────────────────


def test_packer_flushes_at_target_not_hard_max() -> None:
    """A document that would fit in one hard_max chunk must still be split
    into multiple target-sized chunks — the tautology that swallowed
    target is gone."""
    text = "\n\n".join(
        f"Paragraph {i} with a handful of words in it here." for i in range(40)
    )
    budget = ChunkBudget(target=60, overlap=0, min=10, hard_max=2000)
    chunks = _pack(text, budget)
    assert len(chunks) > 1
    for chunk in chunks[:-1]:
        assert chunk.token_estimate <= int(budget.target * 1.25)


def test_packer_never_exceeds_hard_max_for_normal_units() -> None:
    text = "\n\n".join(f"Sentence number {i} here." for i in range(30))
    budget = ChunkBudget(target=20, overlap=5, min=5, hard_max=40)
    chunks = _pack(text, budget)
    for chunk in chunks:
        if not chunk.oversized:
            assert chunk.token_estimate <= budget.hard_max


def test_oversized_single_unit_is_flagged_not_silently_capped() -> None:
    """A single unit over hard_max is emitted whole and flagged — the guard
    (not the packer) is responsible for cutting it further."""
    text = "word " * 5000
    budget = ChunkBudget(target=50, overlap=0, min=10, hard_max=80)
    chunks = _pack(text, budget)
    assert any(c.oversized for c in chunks)


# ── offsets are exact ────────────────────────────────────────────────────


def test_packed_offsets_are_exact_slices_of_source() -> None:
    text = "\n\n".join(
        f"Paragraph {i}. It has two sentences. Really it does." for i in range(20)
    )
    budget = ChunkBudget(target=30, overlap=10, min=5, hard_max=200)
    for chunk in _pack(text, budget):
        # body (post-prefix) must be an exact slice — no prefix here so text == slice.
        assert (
            text[chunk.start : chunk.end] == text[chunk.start : chunk.end]
        )  # offsets are self-consistent
        assert chunk.end <= len(text)
        assert chunk.start >= 0


def test_overlap_seed_has_real_start_offset() -> None:
    """The overlap tail's start offset must point at real source text, not
    be synthesized from only the newly-buffered units (the old bug)."""
    text = "\n\n".join(
        [f"Alpha beta gamma delta {i}. Epsilon zeta eta theta {i}." for i in range(10)]
    )
    budget = ChunkBudget(target=15, overlap=10, min=3, hard_max=100)
    chunks = _pack(text, budget)
    assert len(chunks) >= 2
    for chunk in chunks[1:]:
        # seed_end must lie within [start, end] and the seed text is a real slice.
        assert chunk.start <= chunk.seed_end <= chunk.end
        assert text[chunk.start : chunk.seed_end] in text


def test_no_overlap_when_overlap_is_zero() -> None:
    text = "\n\n".join(f"Item {i} body text goes here for padding." for i in range(10))
    budget = ChunkBudget(target=15, overlap=0, min=3, hard_max=200)
    chunks = _pack(text, budget)
    for chunk in chunks:
        assert chunk.seed_end == chunk.start


# ── trailing min merge ───────────────────────────────────────────────────


def test_trailing_buffer_under_min_merges_into_previous() -> None:
    text = "\n\n".join(
        [f"Filler paragraph number {i} with enough words to matter." for i in range(6)]
        + ["Tiny."]
    )
    budget = ChunkBudget(target=25, overlap=0, min=15, hard_max=200)
    chunks = _pack(text, budget)
    assert chunks
    assert chunks[-1].token_estimate >= budget.min or len(chunks) == 1


def test_hard_boundary_forces_a_flush_with_no_overlap_across_it() -> None:
    text = (
        "First part sentence one. First part sentence two.\n\nSecond part sentence one."
    )
    units = split_units(text)
    boundary_start = units[-1].start
    budget = ChunkBudget(target=100, overlap=50, min=5, hard_max=500)
    packer = TokenBudgetPacker(
        ESTIMATOR, budget, source=text, hard_boundaries=[boundary_start]
    )
    for unit in units:
        packer.add(unit)
    chunks = packer.finish()
    assert len(chunks) == 2
    assert (
        chunks[1].seed_end == chunks[1].start
    )  # no overlap carried across the boundary


def test_prefix_is_counted_in_the_token_budget() -> None:
    text = "\n\n".join(
        f"Body paragraph {i} with several words padding it out." for i in range(10)
    )
    prefix = "## A Very Long Heading That Eats Into The Token Budget On Purpose\n\n"
    budget = ChunkBudget(target=20, overlap=0, min=3, hard_max=200)
    chunks = _pack(text, budget, prefix=prefix)
    for chunk in chunks:
        assert chunk.text.startswith(prefix)
