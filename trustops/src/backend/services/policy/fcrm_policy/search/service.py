"""S3's ``/search`` orchestration (design A §6.2): fuse H4's semantic
ranking over ``policy_clauses`` with Mongo (or, in the no-live-Mongo unit
tier, the in-memory stand-in) keyword ranking through the real
``reciprocal_rank_fusion`` -- every index's fused ranking goes through
that one call, whether it has one ranked source or two, so there is
exactly one place rank fusion happens, never a hand-rolled equivalent.

Only ``policy_clauses`` has a vector leg: H4 does not yet embed a second,
distinct index for ``precedent_cases`` or ``controls`` (tracked upstream,
see ``fcrm_context_worker/UPSTREAM.md``'s "three named indexes... not yet
done" and ``fcrm_policy.search.vector``'s module docstring). Those two
indexes are Mongo-keyword-only here, which ``reciprocal_rank_fusion``
still runs over (a single input ranking fuses to ``score = 1/(k+rank)``,
monotonic with the original order) -- so the fusion step, and the
``matches_filters``-equivalent metadata shape, stay identical across all
three indexes rather than branching into a one-off code path.

Score semantics: ``SearchHit.score`` is the fused RRF score divided by its
ceiling (rank 1 in every source, ``n_sources / (k + 1)``), so it lies in
(0, 1] and preserves RRF's order -- a *rank-agreement* measure, not a
similarity. The per-source numbers a caller wants as similarity travel in
``metadata`` (design A §6.2's ``vector_score`` / ``text_score``):
``vector_score`` is H4's cosine for ``policy_clauses``; ``text_score`` is the
keyword source's own score (a TF cosine in [0, 1] from the in-memory
stand-in, Mongo's unbounded ``textScore`` in production). The seed's
``precedent.similarity = 0.81`` is an authored figure that no source here
reproduces; see ``test_policy_routes.py``'s golden test.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, Optional

from fcrm_common.errors import ValidationError
from fcrm_contracts.api.policy import SearchHit, SearchRequest, SearchResponse

from fcrm_policy.search.filters import validate_metadata
from fcrm_policy.search.rrf import reciprocal_rank_fusion
from fcrm_policy.search.vector import VectorClauseSource
from fcrm_policy.store import (
    KNOWN_INDEXES,
    POLICY_CLAUSES,
    KeywordCollection,
    metadata_for,
    text_for,
)

logger = logging.getLogger(__name__)

#: ``SearchFilters`` fields H4's vector metadata (Index Contract, S3's own
#: copy at ``fcrm_policy.search.filters.ALLOWED_METADATA_KEYS``) actually
#: understands; ``closed_date_before`` has no vector-side equivalent.
_VECTOR_FILTER_KEYS = ("corpus_version", "trigger_type", "risk_area", "jurisdiction")


def _vector_filters(filters: Mapping[str, Any]) -> Optional[dict[str, Optional[str]]]:
    """``SearchFilters`` -> the string-valued filter map H4's vector
    metadata actually stores (``fcrm_context_worker.seedload``).

    ``corpus_version`` needs one extra bridge: the contract types it
    ``Optional[int]`` (``14``, matching S3's own Mongo documents -- see
    ``fcrm_policy.seedmodel.clause_documents``), but H4 embeds the seed's
    own label verbatim (``corpus_version="v14"``, never stripped of its
    ``"v"``). Every other key is already a plain string on both sides.
    """
    active: dict[str, Optional[str]] = {}
    for key in _VECTOR_FILTER_KEYS:
        value = filters.get(key)
        if value is None:
            continue
        active[key] = f"v{value}" if key == "corpus_version" else str(value)
    unknown = validate_metadata(dict(active))
    if unknown:
        # _VECTOR_FILTER_KEYS drifted from the Index Contract allow-list.
        raise ValidationError(f"filter key(s) not in the Index Contract: {unknown}")
    return active or None


class SearchService:
    """Builds a :class:`~fcrm_contracts.api.policy.SearchResponse` for one
    of the three indexes from whatever keyword collections and vector
    source were wired in at startup (Mongo-backed in production, in-memory
    for unit tests -- see ``fcrm_policy.store`` and ``fcrm_policy.lifespan``).
    """

    def __init__(
        self,
        *,
        collections: Mapping[str, KeywordCollection],
        vector: VectorClauseSource,
        rrf_k: int = 60,
        candidate_multiplier: int = 3,
        top_k_max: int = 30,
    ) -> None:
        missing = KNOWN_INDEXES - collections.keys()
        if missing:
            raise ValueError(f"missing keyword collections for {sorted(missing)}")
        self._collections = dict(collections)
        self._vector = vector
        self._rrf_k = rrf_k
        self._candidate_multiplier = candidate_multiplier
        self._top_k_max = top_k_max

    async def search(self, request: SearchRequest) -> SearchResponse:
        """Run ``request`` against its index and return the fused hits."""
        if request.index not in KNOWN_INDEXES:
            raise ValidationError(
                f"unknown search index {request.index!r}; "
                f"expected one of {sorted(KNOWN_INDEXES)}"
            )
        if not 1 <= request.top_k <= self._top_k_max:
            raise ValidationError(
                f"top_k must be 1..{self._top_k_max}, got {request.top_k}"
            )
        collection = self._collections[request.index]
        filters = request.filters.model_dump(exclude_none=True)
        candidates = max(request.top_k * self._candidate_multiplier, request.top_k)

        keyword_hits = await collection.text_search(
            request.query, filters=filters, limit=candidates
        )
        rankings = [[doc_id for doc_id, _ in keyword_hits]]
        text_scores = dict(keyword_hits)
        vector_scores: dict[str, float] = {}

        if request.index == POLICY_CLAUSES:
            vector_hits = await self._vector.rank(
                request.query, filters=_vector_filters(filters), top_k=candidates
            )
            rankings.insert(0, [hit.clause_id for hit in vector_hits])
            vector_scores = {hit.clause_id: hit.score for hit in vector_hits}

        fused = reciprocal_rank_fusion(rankings, k=self._rrf_k)[: request.top_k]
        docs = await collection.get_by_ids([doc_id for doc_id, _ in fused])
        # The best a document can score is rank 1 in every source:
        # len(rankings) / (k + 1). Dividing by it maps the fused score
        # onto (0, 1] without changing the order RRF produced.
        ceiling = len(rankings) / (self._rrf_k + 1)

        hits = [
            SearchHit(
                id=doc_id,
                score=round(score / ceiling, 4),
                text=text_for(request.index, doc),
                metadata={
                    **metadata_for(request.index, doc),
                    "text_score": text_scores.get(doc_id),
                    "vector_score": vector_scores.get(doc_id),
                },
            )
            for doc_id, score in fused
            if (doc := docs.get(doc_id)) is not None
        ]
        if len(hits) != len(fused):
            logger.warning(
                "fused id(s) missing from the collection",
                extra={"index": request.index, "fused": len(fused), "hits": len(hits)},
            )
        return SearchResponse(hits=hits)


__all__ = ["SearchService"]
