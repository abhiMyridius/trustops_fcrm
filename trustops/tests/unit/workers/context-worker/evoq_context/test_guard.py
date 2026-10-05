"""Tests for ``chunking/guard.py`` — ``HardMaxGuard`` cannot fail (P0-2, P0-9).

Directly reproduces the audit's whitespace-free-text findings: a single
"word" of 5,000 repetitions, 400x CJK with no whitespace, and a base64-ish
blob repeated 3,000 times, all at small hard caps — none may ever exceed
the cap, unlike the old ``_split_by_words`` which returned the whole
oversized string unchanged when ``buf`` was empty on the first iteration.
"""

from __future__ import annotations

from evoq_context.chunking.guard import HardMaxGuard
from evoq_context.chunking.tokens import get_estimator
from evoq_context.chunking.types import StructuredChunk

ESTIMATOR = get_estimator("tiktoken")


def _guard(hard_max: int) -> HardMaxGuard:
    return HardMaxGuard(ESTIMATOR, hard_max)


def _chunk(text: str, **metadata) -> StructuredChunk:
    return StructuredChunk(
        text=text, metadata=dict(metadata), char_start=0, char_end=len(text)
    )


def test_chunk_within_cap_passes_through_unchanged() -> None:
    guard = _guard(1000)
    chunk = _chunk("A short chunk well under the cap.")
    out = guard.apply([chunk])
    assert out == [chunk]
    assert guard.hits == 0


def test_whitespace_free_word_never_exceeds_hard_max() -> None:
    """The exact P0-2 reproduction: a single 'word' with no punctuation/whitespace."""
    guard = _guard(80)
    chunk = _chunk("word" * 5000)
    out = guard.apply([chunk])
    assert len(out) > 1
    for piece in out:
        assert ESTIMATOR.count(piece.text) <= 80


def test_cjk_whitespace_free_never_exceeds_hard_max() -> None:
    guard = _guard(400)
    chunk = _chunk("本系统应当支持并发用户访问而不发生数据丢失。" * 400)
    out = guard.apply([chunk])
    for piece in out:
        assert ESTIMATOR.count(piece.text) <= 400


def test_base64_blob_never_exceeds_hard_max() -> None:
    guard = _guard(200)
    chunk = _chunk("A1b2C3d4E5f6G7h8I9j0" * 3000)
    out = guard.apply([chunk])
    assert len(out) > 1
    for piece in out:
        assert ESTIMATOR.count(piece.text) <= 200


def test_sentence_aligned_split_preferred_when_possible() -> None:
    guard = _guard(30)
    text = " ".join(f"Sentence number {i} has some words in it." for i in range(30))
    chunk = _chunk(text)
    out = guard.apply([chunk])
    for piece in out:
        assert ESTIMATOR.count(piece.text) <= 30
    # Reassembled (whitespace-collapsed) pieces cover the source.
    joined = " ".join(p.body.strip() for p in out)
    assert set(text.split()) <= set(joined.split())


def test_offsets_and_token_estimate_are_exact_on_split_pieces() -> None:
    """P1-16: char_start/char_end/token_estimate must never be stale after a split."""
    guard = _guard(60)
    text = "word" * 3000
    chunk = _chunk(text)
    out = guard.apply([chunk])
    for piece in out:
        assert text[piece.char_start : piece.char_end] == piece.body
        assert piece.metadata["char_start"] == piece.char_start
        assert piece.metadata["char_end"] == piece.char_end
        assert piece.metadata["token_estimate"] == ESTIMATOR.count(piece.text)


def test_injected_prefix_is_preserved_on_every_piece() -> None:
    guard = _guard(50)
    chunk = StructuredChunk(
        text="## Heading\n\n" + "word" * 2000,
        metadata={},
        char_start=0,
        char_end=len("## Heading\n\n" + "word" * 2000),
        injected_prefix="## Heading\n\n",
    )
    out = guard.apply([chunk])
    assert len(out) > 1
    for piece in out:
        assert piece.text.startswith("## Heading\n\n")
        assert ESTIMATOR.count(piece.text) <= 50


def test_guard_reports_a_corrected_guardrail_event() -> None:
    events = []

    class _Recorder:
        def on_guardrail_event(self, event):
            events.append(event)

        def on_llm_call(self, record):  # pragma: no cover - unused here
            pass

    guard = HardMaxGuard(ESTIMATOR, 40, observer=_Recorder())
    guard.apply([_chunk("word" * 1000, chunk_key="doc:aaaa:0000")])
    assert len(events) == 1
    assert events[0].name == "hard_max_guard"
    assert events[0].outcome == "corrected"
    assert events[0].subject == "doc:aaaa:0000"


def test_guard_counts_hits() -> None:
    guard = _guard(30)
    guard.apply([_chunk("word" * 500), _chunk("short chunk")])
    assert guard.hits == 1


def test_empty_chunk_is_left_alone() -> None:
    guard = _guard(10)
    chunk = _chunk("")
    out = guard.apply([chunk])
    assert out == [chunk]


def test_multiple_oversized_chunks_each_split_independently() -> None:
    guard = _guard(25)
    chunks = [_chunk("alpha" * 800), _chunk("beta" * 900)]
    out = guard.apply(chunks)
    assert guard.hits == 2
    for piece in out:
        assert ESTIMATOR.count(piece.text) <= 25
