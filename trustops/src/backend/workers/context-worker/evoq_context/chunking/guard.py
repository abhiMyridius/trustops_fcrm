"""``HardMaxGuard`` — the final safety net that cannot fail.

Scope & functionality
---------------------
No emitted chunk may exceed ``hard_max`` tokens, whatever the text looks
like. For an oversized chunk the guard first packs whole sentences up to
the cap; any sentence still over the cap (whitespace-free CJK, base64
blobs, ``"word" * 5000``) is cut on **character** boundaries by searching
for the largest prefix that fits — a pure function of the estimator's
``count`` that terminates for any input (a single character is the floor)
and never produces replacement characters, because it never splits inside
a code point. Every piece is an exact substring of the original body, so
``char_start``/``char_end``/``token_estimate`` are re-derived from real
offsets and nothing stale survives. Each correction is reported as a
``GuardrailEvent(name="hard_max_guard", outcome="corrected")`` through the
optional observer.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from evoq_context.chunking.text_ops import split_sentences
from evoq_context.chunking.tokens import TokenEstimator
from evoq_context.chunking.types import StructuredChunk
from evoq_context.core.errors import ConfigError
from evoq_context.core.observer import ContextObserver, GuardrailEvent, NullObserver

_LAST_WHITESPACE_RE = re.compile(r"\s(?!.*\s)", re.DOTALL)
_CHARS_PER_TOKEN_GUESS = 4


class HardMaxGuard:
    """Split every chunk above ``hard_max`` tokens into pieces that fit."""

    name = "hard_max_guard"

    def __init__(
        self,
        estimator: TokenEstimator,
        hard_max: int,
        *,
        observer: ContextObserver | None = None,
    ) -> None:
        if hard_max < 1:
            raise ConfigError(f"hard_max must be >= 1 (got {hard_max})")
        self._est = estimator
        self._hard_max = hard_max
        self._observer: ContextObserver = observer if observer is not None else NullObserver()
        self._hits = 0

    @property
    def hard_max(self) -> int:
        return self._hard_max

    @property
    def hits(self) -> int:
        """How many chunks this guard has had to correct so far."""
        return self._hits

    # -- public -------------------------------------------------------------

    def apply(self, chunks: Sequence[StructuredChunk], *, subject: str = "") -> list[StructuredChunk]:
        """Return ``chunks`` with every oversized one replaced by fitting pieces."""
        out: list[StructuredChunk] = []
        for index, chunk in enumerate(chunks):
            tokens = self._est.count(chunk.text)
            if tokens <= self._hard_max:
                out.append(chunk)
                continue
            prefix = chunk.injected_prefix
            if prefix and self._est.count(prefix) * 2 > self._hard_max:
                prefix = ""  # a prefix that starves the body is dropped from the pieces
            body = chunk.body
            pieces = [
                self._piece(chunk, body, prefix, start, end)
                for start, end in self.pieces(body, prefix=prefix)
            ]
            self._hits += 1
            largest = max(int(p.metadata["token_estimate"]) for p in pieces)
            self._observer.on_guardrail_event(
                GuardrailEvent(
                    name=self.name,
                    subject=subject or str(chunk.metadata.get("chunk_key") or f"chunk[{index}]"),
                    outcome="corrected",
                    detail=(
                        f"chunk token-sliced: {tokens} -> {len(pieces)} pieces "
                        f"(largest {largest}, cap {self._hard_max})"
                    ),
                )
            )
            out.extend(pieces)
        return out

    def pieces(self, body: str, *, prefix: str = "") -> list[tuple[int, int]]:
        """``(start, end)`` offsets into ``body`` such that every ``prefix + body[s:e]`` fits.

        Sentence-aligned where possible, character-sliced where a single
        sentence is itself over the cap. Leading/trailing whitespace of
        ``body`` belongs to no piece.
        """
        if not body.strip():
            return [(0, len(body))]
        sentences = split_sentences(body)
        ranges: list[tuple[int, int]] = []
        cur_start, cur_end = sentences[0].start, sentences[0].end
        for sentence in sentences[1:]:
            if self._fits(prefix, body, cur_start, sentence.end):
                cur_end = sentence.end
                continue
            ranges.extend(self._slice_if_needed(prefix, body, cur_start, cur_end))
            cur_start, cur_end = sentence.start, sentence.end
        ranges.extend(self._slice_if_needed(prefix, body, cur_start, cur_end))
        return ranges

    # -- internals ----------------------------------------------------------

    def _piece(
        self, chunk: StructuredChunk, body: str, prefix: str, start: int, end: int
    ) -> StructuredChunk:
        text = prefix + body[start:end]
        piece = StructuredChunk(
            text=text,
            metadata=dict(chunk.metadata),
            char_start=chunk.char_start + start,
            char_end=chunk.char_start + end,
            injected_prefix=prefix,
            level=chunk.level,
            segment_ordinal=chunk.segment_ordinal,
            topic_id=chunk.topic_id,
        )
        piece.metadata["char_start"] = piece.char_start
        piece.metadata["char_end"] = piece.char_end
        piece.metadata["token_estimate"] = self._est.count(text)
        return piece

    def _fits(self, prefix: str, body: str, start: int, end: int) -> bool:
        return self._est.count(prefix + body[start:end]) <= self._hard_max

    def _slice_if_needed(self, prefix: str, body: str, start: int, end: int) -> list[tuple[int, int]]:
        if self._fits(prefix, body, start, end):
            return [(start, end)]
        out: list[tuple[int, int]] = []
        pos = start
        while pos < end:
            cut = self._largest_fit(prefix, body, pos, end)
            if cut < end:
                cut = self._prefer_whitespace(body, pos, cut)
            out.append((pos, cut))
            pos = cut
            while pos < end and body[pos].isspace():
                pos += 1
        return out

    def _largest_fit(self, prefix: str, body: str, pos: int, limit: int) -> int:
        """Largest ``e`` in ``(pos, limit]`` with ``fits(pos, e)``; ``pos + 1`` is the floor."""
        if self._fits(prefix, body, pos, limit):
            return limit
        lo = pos + 1
        hi = min(limit, pos + max(8, self._hard_max * _CHARS_PER_TOKEN_GUESS))
        while hi < limit and self._fits(prefix, body, pos, hi):
            lo = hi
            hi = min(limit, pos + (hi - pos) * 2)
        if hi < limit or not self._fits(prefix, body, pos, hi):
            # fits(lo) is true (or lo is the one-char floor); fits(hi) is false
            while hi - lo > 1:
                mid = (lo + hi) // 2
                if self._fits(prefix, body, pos, mid):
                    lo = mid
                else:
                    hi = mid
            return lo
        return hi

    @staticmethod
    def _prefer_whitespace(body: str, pos: int, cut: int) -> int:
        """Pull a cut back to the last whitespace in the second half of the piece, if any."""
        half = pos + (cut - pos) // 2
        match = _LAST_WHITESPACE_RE.search(body, half, cut)
        if match is not None and match.start() > pos:
            return match.start()
        return cut
