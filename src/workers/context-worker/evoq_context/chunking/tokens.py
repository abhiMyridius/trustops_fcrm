"""Token estimation for chunking, the hard-max guard and embedding limits.

Scope & functionality
---------------------
``TokenEstimator`` is the Protocol every budget/guard consumer takes:
``count`` for sizing, ``encode``/``decode`` for token-aligned work, and
``name``/``effective_name`` so the index fingerprint can record which
tokenizer *actually* ran. ``TiktokenEstimator`` (``cl100k_base``) is the
production tokenizer; it fails loudly with ``TokenizerUnavailable`` when the
BPE file cannot be loaded unless ``fallback="heuristic"`` was configured —
a silent fallback would change every chunk boundary and every ``chunk_key``
without changing the fingerprint. ``HeuristicEstimator`` (one token per 4
characters, exact round-trip ``encode``/``decode``) is that explicit
fallback and the offline test double. ``get_estimator`` is memoised so the
encoding is loaded once per process.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from functools import lru_cache
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

from evoq_context.core import limits
from evoq_context.core.errors import TokenizerUnavailable

if TYPE_CHECKING:
    import tiktoken

logger = logging.getLogger(__name__)

TokenizerFallback = Literal["error", "heuristic"]

_HEURISTIC_PIECE_TAG = b"\x01"  # preserves leading NUL bytes through int round-trips


@runtime_checkable
class TokenEstimator(Protocol):
    """Anything that can count, encode and decode tokens for a text span."""

    @property
    def name(self) -> str: ...

    @property
    def effective_name(self) -> str: ...

    def count(self, text: str) -> int: ...

    def encode(self, text: str) -> list[int]: ...

    def decode(self, ids: Sequence[int]) -> str: ...


class HeuristicEstimator:
    """One token per ``HEURISTIC_CHARS_PER_TOKEN`` characters; no dependency.

    ``encode`` splits the text into fixed-size character pieces and packs
    each piece's UTF-8 bytes into one int; ``decode`` reverses it exactly,
    so ``decode(encode(t)) == t`` and ``count(t) == len(encode(t))`` always.
    """

    _piece = limits.HEURISTIC_CHARS_PER_TOKEN

    @property
    def name(self) -> str:
        return "heuristic"

    @property
    def effective_name(self) -> str:
        return "heuristic"

    def count(self, text: str) -> int:
        if not text:
            return 0
        return math.ceil(len(text) / self._piece)

    def encode(self, text: str) -> list[int]:
        return [
            int.from_bytes(_HEURISTIC_PIECE_TAG + text[i : i + self._piece].encode("utf-8"), "big")
            for i in range(0, len(text), self._piece)
        ]

    def decode(self, ids: Sequence[int]) -> str:
        parts: list[str] = []
        for token in ids:
            raw = token.to_bytes((token.bit_length() + 7) // 8, "big")
            parts.append(raw[len(_HEURISTIC_PIECE_TAG) :].decode("utf-8", errors="replace"))
        return "".join(parts)


class TiktokenEstimator:
    """``cl100k_base`` via ``tiktoken``; loud by default when unavailable."""

    def __init__(
        self,
        *,
        fallback: TokenizerFallback = "error",
        encoding_name: str = "cl100k_base",
    ) -> None:
        self._heuristic: HeuristicEstimator | None = None
        self._encoding: tiktoken.Encoding | None
        try:
            import tiktoken

            self._encoding = tiktoken.get_encoding(encoding_name)
            self._effective = "tiktoken"
        except Exception as exc:
            if fallback != "heuristic":
                raise TokenizerUnavailable(
                    f"tiktoken encoding {encoding_name!r} could not be loaded ({exc}); "
                    "set CHUNK_TOKENIZER_FALLBACK=heuristic to degrade explicitly"
                ) from exc
            logger.warning(
                "tiktoken %s unavailable (%s); degrading to the heuristic chars/%d estimator "
                "— recorded as the effective tokenizer in the index fingerprint",
                encoding_name,
                exc,
                limits.HEURISTIC_CHARS_PER_TOKEN,
            )
            self._encoding = None
            self._heuristic = HeuristicEstimator()
            self._effective = "heuristic"

    @property
    def name(self) -> str:
        return "tiktoken"

    @property
    def effective_name(self) -> str:
        return self._effective

    def count(self, text: str) -> int:
        if not text:
            return 0
        if self._heuristic is not None:
            return self._heuristic.count(text)
        assert self._encoding is not None
        return len(self._encoding.encode(text, disallowed_special=()))

    def encode(self, text: str) -> list[int]:
        if self._heuristic is not None:
            return self._heuristic.encode(text)
        assert self._encoding is not None
        ids: list[int] = self._encoding.encode(text, disallowed_special=())
        return ids

    def decode(self, ids: Sequence[int]) -> str:
        if self._heuristic is not None:
            return self._heuristic.decode(ids)
        assert self._encoding is not None
        text: str = self._encoding.decode(list(ids), errors="replace")
        return text


_KNOWN_TOKENIZERS: frozenset[str] = frozenset({"tiktoken", "heuristic"})


def known_tokenizer(name: str) -> bool:
    """Is ``name`` a tokenizer ``get_estimator`` can resolve?"""
    return (name or "").strip().lower() in _KNOWN_TOKENIZERS


@lru_cache(maxsize=8)
def get_estimator(
    name: str = limits.DEFAULT_CHUNK_TOKENIZER,
    fallback: str = limits.DEFAULT_CHUNK_TOKENIZER_FALLBACK,
) -> TokenEstimator:
    """Resolve ``CHUNK_TOKENIZER`` to a memoised estimator instance.

    ``fallback`` applies to ``tiktoken`` only: ``"error"`` raises
    ``TokenizerUnavailable`` when the encoding cannot load, ``"heuristic"``
    degrades explicitly (and ``effective_name`` says so).
    """
    key = (name or limits.DEFAULT_CHUNK_TOKENIZER).strip().lower()
    if key == "heuristic":
        return HeuristicEstimator()
    mode: TokenizerFallback = "heuristic" if (fallback or "").strip().lower() == "heuristic" else "error"
    return TiktokenEstimator(fallback=mode)
