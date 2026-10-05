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
from dataclasses import dataclass
from typing import Any, Optional

from fcrm_common.errors import ValidationError
from fcrm_contracts.api.policy import (
    PolicyClauseHit,
    PrecedentSearchRequest,
    PrecedentSearchResponse,
    SearchHit,
    SearchRequest,
    SearchResponse,
)

from fcrm_policy.search.filters import validate_metadata
from fcrm_policy.search.rrf import reciprocal_rank_fusion
from fcrm_policy.search.shaping import policy_hit, precedent_averages, precedent_hit
from fcrm_policy.search.vector import VectorClauseSource
from fcrm_policy.store import (
    KNOWN_INDEXES,
    POLICY_CLAUSES,
    PRECEDENT_CASES,
    Document,
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


@dataclass(frozen=True)
class RankedDoc:
    """One fused result: the stored document plus the numbers that ranked it
    (``score`` is the normalised fused score in (0, 1]; the two ranker scores
    are ``None`` for a ranker that did not return the document).
    """

    doc_id: str
    score: float
    doc: Document
    text_score: Optional[float]
    vector_score: Optional[float]


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

    def _check_top_k(self, top_k: int) -> None:
        """Refuse a ``top_k`` outside ``1..top_k_max`` (a 422, not a clamp)."""
        if not 1 <= top_k <= self._top_k_max:
            raise ValidationError(f"top_k must be 1..{self._top_k_max}, got {top_k}")

    async def _rank(
        self,
        index: str,
        query: str,
        filters: Mapping[str, Any],
        top_k: int,
        vector_filters: Optional[Mapping[str, Optional[str]]] = None,
    ) -> list[RankedDoc]:
        """The fused ranking of ``index`` for ``query`` under ``filters``:
        the keyword ranking and, for ``policy_clauses``, the semantic one,
        fused by the one ``reciprocal_rank_fusion`` call, then the stored
        documents for the winners. Never ranks anything but those two sources.
        """
        collection = self._collections[index]
        candidates = max(top_k * self._candidate_multiplier, top_k)
        keyword_hits = await collection.text_search(
            query, filters=filters, limit=candidates
        )
        rankings = [[doc_id for doc_id, _ in keyword_hits]]
        text_scores = dict(keyword_hits)
        vector_scores: dict[str, float] = {}

        if index == POLICY_CLAUSES:
            vector_hits = await self._vector.rank(
                query, filters=vector_filters, top_k=candidates
            )
            rankings.insert(0, [hit.clause_id for hit in vector_hits])
            vector_scores = {hit.clause_id: hit.score for hit in vector_hits}

        fused = reciprocal_rank_fusion(rankings, k=self._rrf_k)[:top_k]
        docs = await collection.get_by_ids([doc_id for doc_id, _ in fused])
        # The best a document can score is rank 1 in every source:
        # len(rankings) / (k + 1). Dividing by it maps the fused score
        # onto (0, 1] without changing the order RRF produced.
        ceiling = len(rankings) / (self._rrf_k + 1)
        ranked = [
            RankedDoc(
                doc_id=doc_id,
                score=round(score / ceiling, 4),
                doc=doc,
                text_score=text_scores.get(doc_id),
                vector_score=vector_scores.get(doc_id),
            )
            for doc_id, score in fused
            if (doc := docs.get(doc_id)) is not None
        ]
        if len(ranked) != len(fused):
            logger.warning(
                "fused id(s) missing from the collection",
                extra={"index": index, "fused": len(fused), "hits": len(ranked)},
            )
        return ranked

    async def search(self, request: SearchRequest) -> SearchResponse:
        """Run ``request`` against its index and return the fused hits."""
        if request.index not in KNOWN_INDEXES:
            raise ValidationError(
                f"unknown search index {request.index!r}; "
                f"expected one of {sorted(KNOWN_INDEXES)}"
            )
        self._check_top_k(request.top_k)
        filters = request.filters.model_dump(exclude_none=True)
        ranked = await self._rank(
            request.index,
            request.query,
            filters,
            request.top_k,
            _vector_filters(filters) if request.index == POLICY_CLAUSES else None,
        )
        return SearchResponse(
            hits=[
                SearchHit(
                    id=item.doc_id,
                    score=item.score,
                    text=text_for(request.index, item.doc),
                    metadata={
                        **metadata_for(request.index, item.doc),
                        "text_score": item.text_score,
                        "vector_score": item.vector_score,
                    },
                )
                for item in ranked
            ]
        )

    async def search_policy(self, query: str, top_k: int) -> list[PolicyClauseHit]:
        """The clauses most relevant to ``query``, each with its verbatim text
        and citations (the web's policy search).
        """
        self._check_top_k(top_k)
        ranked = await self._rank(POLICY_CLAUSES, query, {}, top_k)
        return [
            policy_hit(
                item.doc,
                similarity=item.score,
                vector_score=item.vector_score,
                text_score=item.text_score,
            )
            for item in ranked
        ]

    async def search_precedents(
        self, request: PrecedentSearchRequest
    ) -> PrecedentSearchResponse:
        """The closed cases most like ``request.query`` under its filters, and
        the mean control rating over them.
        """
        self._check_top_k(request.top_k)
        flt = request.filters
        filters: dict[str, Any] = {
            "trigger_types": flt.trigger_types or None,
            "risk_areas": flt.risk_areas or None,
            "closed_date_after": flt.closed_after,
            "closed_date_before": flt.closed_before,
            "outcome": flt.outcome,
        }
        ranked = await self._rank(
            PRECEDENT_CASES, request.query, filters, request.top_k
        )
        hits = [precedent_hit(item.doc, similarity=item.score) for item in ranked]
        return PrecedentSearchResponse(
            results=hits, precedent_avg=precedent_averages(hits)
        )


__all__ = ["RankedDoc", "SearchService"]
