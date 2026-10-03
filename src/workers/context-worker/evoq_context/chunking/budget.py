"""Token budget and the packer that fills it.

Scope & functionality
---------------------
``ChunkBudget`` is the validated ``(target, overlap, min, hard_max)`` tuple
every segmenter receives. ``TokenBudgetPacker`` accumulates ``Span`` units
into chunks: it flushes when adding the next unit would exceed **target**
(the old ``<= target or <= hard`` tautology that let every chunk grow to
the hard max is gone), seeds the next chunk with a sentence-aligned
overlap tail carrying its **real** start offset (chunk text is always an
exact slice of the source, so ``char_start``/``char_end`` are correct on
overlapped chunks), forces a flush at hard boundaries (topic cuts,
sub-headings, markers), emits a unit larger than ``hard_max`` whole but
flagged ``oversized`` for ``HardMaxGuard``, and merges a trailing buffer
smaller than ``min`` into the previous chunk when the result stays within
``hard_max`` (that is what ``chunk_min_tokens`` does).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from evoq_context.chunking.text_ops import split_sentences
from evoq_context.chunking.tokens import TokenEstimator
from evoq_context.chunking.types import Span
from evoq_context.core.errors import ConfigError

_OVERLAP_KINDS = frozenset({"prose", "list"})


@dataclass(frozen=True, slots=True)
class ChunkBudget:
    """Token budget for one document: ``0 <= overlap < target <= hard_max``, ``min <= target``."""

    target: int
    overlap: int
    min: int
    hard_max: int

    def validate(self) -> ChunkBudget:
        """Return ``self`` or raise ``ConfigError`` naming the violated rule."""
        if self.target < 1:
            raise ConfigError(f"CHUNK_TARGET_TOKENS must be >= 1 (got {self.target})")
        if self.hard_max < self.target:
            raise ConfigError(
                f"CHUNK_HARD_MAX_TOKENS ({self.hard_max}) must be >= CHUNK_TARGET_TOKENS ({self.target})"
            )
        if not 0 <= self.overlap < self.target:
            raise ConfigError(
                f"CHUNK_OVERLAP_TOKENS ({self.overlap}) must satisfy 0 <= overlap < target ({self.target})"
            )
        if not 0 <= self.min <= self.target:
            raise ConfigError(
                f"CHUNK_MIN_TOKENS ({self.min}) must satisfy 0 <= min <= target ({self.target})"
            )
        return self


@dataclass(frozen=True, slots=True)
class Packed:
    """One packed chunk: ``text == prefix + source[start:end]``.

    ``seed_end`` is where the overlap seed ends inside ``[start, end)``
    (``== start`` when the chunk has no overlap); ``oversized`` marks a single
    unit above ``hard_max`` left whole for the guard.
    """

    text: str
    start: int
    end: int
    token_estimate: int
    oversized: bool = False
    seed_end: int = 0


class TokenBudgetPacker:
    """Pack ordered ``Span`` units of one segment into token-budgeted chunks.

    ``source`` is the document text the spans index into; ``prefix`` (the
    segment's heading line, typically) is prepended to every chunk and
    counted in its budget; ``hard_boundaries`` are span start offsets in
    front of which a flush is forced (the overlap seed is dropped there —
    no overlap across a topic or structural boundary).
    """

    def __init__(
        self,
        estimator: TokenEstimator,
        budget: ChunkBudget,
        *,
        source: str,
        prefix: str = "",
        hard_boundaries: Iterable[int] = (),
    ) -> None:
        self._est = estimator
        self._budget = budget.validate()
        self._source = source
        self._prefix = prefix
        self._boundaries = frozenset(hard_boundaries)
        self._buf: list[Span] = []
        self._seed: Span | None = None
        self._out: list[Packed] = []
        self._boundary_after: set[int] = set()
        self._last_end = 0

    # -- public -------------------------------------------------------------

    def add(self, span: Span) -> None:
        """Add the next unit (document order is required)."""
        if not span.text.strip():
            return
        if span.start < self._last_end:
            raise ValueError(
                f"spans must be added in document order (got start={span.start} < {self._last_end})"
            )
        if span.start in self._boundaries:
            self.flush()
        span_tokens = self._est.count(self._prefix + span.text)
        if span_tokens > self._budget.hard_max:
            self._emit_buffer()
            self._seed = None
            self._out.append(
                Packed(
                    text=self._prefix + span.text,
                    start=span.start,
                    end=span.end,
                    token_estimate=span_tokens,
                    oversized=True,
                    seed_end=span.start,
                )
            )
            self._last_end = span.end
            return
        if self._buf and self._count(self._range_start(), span.end) > self._budget.target:
            self._emit_buffer()
        if (
            not self._buf
            and self._seed is not None
            and self._count(self._seed.start, span.end) > self._budget.hard_max
        ):
            # An overlap seed must never push a chunk past the hard cap.
            self._seed = None
        self._buf.append(span)
        self._last_end = span.end

    def flush(self) -> None:
        """Force a chunk boundary here (topic cut / structural boundary); no overlap across it."""
        self._emit_buffer()
        self._seed = None
        if self._out:
            self._boundary_after.add(len(self._out) - 1)

    def finish(self) -> list[Packed]:
        """Emit what is buffered, apply the trailing ``min`` merge, return all chunks."""
        self._emit_buffer()
        self._merge_trailing_min()
        return list(self._out)

    # -- internals ----------------------------------------------------------

    def _text(self, start: int, end: int) -> str:
        return self._prefix + self._source[start:end]

    def _count(self, start: int, end: int) -> int:
        return self._est.count(self._text(start, end))

    def _range_start(self) -> int:
        if self._seed is not None:
            return self._seed.start
        return self._buf[0].start

    def _emit_buffer(self) -> None:
        if not self._buf:
            return
        start = self._range_start()
        end = self._buf[-1].end
        text = self._text(start, end)
        self._out.append(
            Packed(
                text=text,
                start=start,
                end=end,
                token_estimate=self._est.count(text),
                seed_end=self._seed.end if self._seed is not None else start,
            )
        )
        self._seed = self._overlap_seed(self._buf)
        self._buf = []

    def _overlap_seed(self, buf: list[Span]) -> Span | None:
        """Sentence-aligned tail of the last unit, at most ``overlap`` tokens, real offsets."""
        if self._budget.overlap <= 0:
            return None
        last = buf[-1]
        if last.kind not in _OVERLAP_KINDS:
            return None
        chosen: int | None = None
        for sentence in reversed(split_sentences(last.text, base=last.start)):
            if self._est.count(self._source[sentence.start : last.end]) > self._budget.overlap:
                break
            chosen = sentence.start
        if chosen is None:
            return None
        return Span(chosen, last.end, self._source[chosen : last.end], "prose")

    def _merge_trailing_min(self) -> None:
        if len(self._out) < 2:
            return
        last, prev = self._out[-1], self._out[-2]
        if last.oversized or prev.oversized or last.token_estimate >= self._budget.min:
            return
        if len(self._out) - 2 in self._boundary_after:
            return
        merged_text = self._text(prev.start, last.end)
        merged_tokens = self._est.count(merged_text)
        if merged_tokens > self._budget.hard_max:
            return
        self._out[-2:] = [
            Packed(
                text=merged_text,
                start=prev.start,
                end=last.end,
                token_estimate=merged_tokens,
                seed_end=prev.seed_end,
            )
        ]
