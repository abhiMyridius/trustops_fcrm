"""Reciprocal rank fusion: combine the S3 Vectors semantic ranking and the
Mongo text-search keyword ranking into one result order, without needing
either source's raw scores to be on a comparable scale (design A §6.2's
"hybrid search ... RRF").

Formula: for a document ``d``, ``score(d) = sum over every ranking r that
contains d of 1 / (k + rank_r(d))``, where ``rank_r(d)`` is ``d``'s
1-indexed position in ranking ``r`` and ``k`` is a damping constant (60 is
the standard default from the original RRF paper and what this module
uses unless a caller overrides it). A document missing from a ranking
contributes nothing from that ranking, not a penalty — appearing in only
one list is still rewarded, just less than appearing near the top of two.
"""

from __future__ import annotations

DEFAULT_K = 60


def reciprocal_rank_fusion(
    rankings: list[list[str]], k: int = DEFAULT_K
) -> list[tuple[str, float]]:
    """``rankings`` is one ranked id list per retrieval source, best match
    first. Returns ``(id, fused_score)`` pairs sorted by fused score
    descending (ties broken by id, for a reproducible order); the caller
    truncates to ``top_k`` after this if it wants fewer results than the
    union of every source produced.
    """
    scores: dict[str, float] = {}
    for ranking in rankings:
        for position, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + position)

    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


def top_k(rankings: list[list[str]], k: int, *, rrf_k: int = DEFAULT_K) -> list[str]:
    """Convenience wrapper: just the top ``k`` fused ids, no scores."""
    fused = reciprocal_rank_fusion(rankings, k=rrf_k)
    return [doc_id for doc_id, _ in fused[:k]]
