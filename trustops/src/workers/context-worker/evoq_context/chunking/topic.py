"""``TopicBoundaryRefiner`` — deterministic, lexical-cohesion topic cuts.

Scope & functionality
----------------------
A pure, offline TextTiling-style boundary detector: no embeddings, no LLM
call, same input always yields the same cuts (fully deterministic — stable
sort order, no dict-iteration-order dependence). Each prose unit is
tokenised (lowercased, punctuation-stripped, a ~120-word English stoplist
and tokens under 3 characters dropped, crude suffix stemming
``s|es|ed|ing``) into a term-frequency vector. For every gap between
consecutive units, the cosine similarity of the TF-sum of the ``window``
units before it against the ``window`` units after it is one "similarity
score"; a gap's *depth* is how far that score dips below the nearest local
similarity peak on each side. A gap becomes a cut when its depth exceeds
``mean(depths) + depth_k * std(depths)`` — an above-average dip, scaled by
how noisy the similarity curve is. Refinement is skipped entirely for a
short window (``len(units) < min_paragraphs``) and per-gap when either
side's window holds fewer than 20 tokens (too little signal to trust a
cosine score).

``TopicConfig`` lives in ``core/config.py`` (imported here, not
redeclared) since it is also part of ``ContextConfig.index_fingerprint()``.
Cuts become hard boundaries for the packer; the pipeline assigns
``topic_id = f"t{n:03d}"`` sequentially per document — children of the same
topic share an id, and a parent inherits its first child's topic_id (see
``pipeline.py``/``parent_child.py``).
"""

from __future__ import annotations

import math
import re
import statistics
from collections import Counter
from typing import TYPE_CHECKING

from evoq_context.core.config import TopicConfig

if TYPE_CHECKING:
    from evoq_context.chunking.types import Span

__all__ = ["TopicConfig", "TopicBoundaryRefiner"]

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_MIN_TOKEN_LEN = 3
_MIN_WINDOW_TOKENS = 20

#: A small, deliberately unglamorous English stoplist (~120 words) — no
#: external dependency, and stable across environments/versions.
_STOPWORDS_TEXT = """
a about above after again against all am an and any are aren't as at
be because been before being below between both but by
can't cannot could couldn't
did didn't do does doesn't doing don't down during
each few for from further
had hadn't has hasn't have haven't having he he'd he'll he's her here
here's hers herself him himself his how how's
i i'd i'll i'm i've if in into is isn't it it's its itself
let's me more most mustn't my myself
no nor not
of off on once only or other ought our ours ourselves out over own
same shan't she she'd she'll she's should shouldn't so some such
than that that's the their theirs them themselves then there there's
these they they'd they'll they're they've this those through to too
under until up
very
was wasn't we we'd we'll we're we've were weren't what what's when
when's where where's which while who who's whom why why's with won't
would wouldn't
you you'd you'll you're you've your yours yourself yourselves
"""
_STOPWORDS: frozenset[str] = frozenset(_STOPWORDS_TEXT.split())


def _stem(word: str) -> str:
    """Crude suffix stemming — good enough for a lexical-cohesion signal."""
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) > len(suffix) + 2:
            return word[: -len(suffix)]
    return word


def _tokenize(text: str) -> list[str]:
    tokens = []
    for raw in _TOKEN_RE.findall(text.lower()):
        if len(raw) < _MIN_TOKEN_LEN or raw in _STOPWORDS:
            continue
        tokens.append(_stem(raw))
    return tokens


def _cosine(a: Counter[str], b: Counter[str]) -> float:
    if not a or not b:
        return 0.0
    common = sorted(set(a) & set(b))
    dot: int = sum(a[k] * b[k] for k in common)
    if dot == 0:
        return 0.0
    norm_a = math.sqrt(sum(v * v for v in a.values()))
    norm_b = math.sqrt(sum(v * v for v in b.values()))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _peak(scores: list[float], i: int, step: int) -> float:
    """Nearest local peak from index ``i`` walking in ``step`` (+1/-1)."""
    peak = scores[i]
    j = i
    n = len(scores)
    while 0 <= j + step < n and scores[j + step] >= scores[j]:
        j += step
        peak = scores[j]
    return peak


class TopicBoundaryRefiner:
    """Deterministic lexical-cohesion boundary detector (see module docstring)."""

    def __init__(self, cfg: TopicConfig) -> None:
        """Bind the refiner to its config."""
        self._cfg = cfg

    def refine(self, units: list[Span]) -> list[int]:
        """Cut indices ``i`` meaning "cut between ``units[i-1]`` and ``units[i]``"."""
        cfg = self._cfg
        n = len(units)
        if not cfg.enabled or n < cfg.min_paragraphs:
            return []

        term_freqs = [Counter(_tokenize(u.text)) for u in units]
        token_counts = [sum(tf.values()) for tf in term_freqs]

        scores: list[float | None] = [None] * n  # index i -> score at gap i (between i-1, i)
        for i in range(1, n):
            before = range(max(0, i - cfg.window), i)
            after = range(i, min(n, i + cfg.window))
            before_tokens = sum(token_counts[k] for k in before)
            after_tokens = sum(token_counts[k] for k in after)
            if before_tokens < _MIN_WINDOW_TOKENS or after_tokens < _MIN_WINDOW_TOKENS:
                continue
            before_tf: Counter[str] = Counter()
            for k in before:
                before_tf.update(term_freqs[k])
            after_tf: Counter[str] = Counter()
            for k in after:
                after_tf.update(term_freqs[k])
            scores[i] = _cosine(before_tf, after_tf)

        valid_indices = [i for i in range(1, n) if scores[i] is not None]
        if len(valid_indices) < 2:
            return []

        dense_scores: list[float] = [score for score in scores if score is not None]
        depths_by_index: dict[int, float] = {}
        for pos, i in enumerate(valid_indices):
            left_peak = _peak(dense_scores, pos, -1)
            right_peak = _peak(dense_scores, pos, +1)
            score = dense_scores[pos]
            depths_by_index[i] = (left_peak - score) + (right_peak - score)

        depth_values = list(depths_by_index.values())
        mean_depth = statistics.fmean(depth_values)
        std_depth = statistics.pstdev(depth_values) if len(depth_values) > 1 else 0.0
        threshold = mean_depth + cfg.depth_k * std_depth

        cuts = sorted(i for i, depth in depths_by_index.items() if depth > threshold)
        return cuts
