"""S3's HTTP surface: ``POST /search`` (design A §2.3, §6.2).

Every hit is the fused output of the real ``reciprocal_rank_fusion`` over
whatever keyword and (for ``policy_clauses``) vector sources were wired
into the ``SearchService`` at startup -- the route itself never ranks,
scores or filters anything (``fcrm_policy.search.service`` does).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fcrm_contracts.api.policy import SearchRequest, SearchResponse

from fcrm_policy.search.service import SearchService

router = APIRouter(tags=["policy"])


def get_search_service(request: Request) -> SearchService:
    """The ``SearchService`` the app wired into ``app.state`` at startup
    (``lifespan.py``) -- a FastAPI dependency so routes stay testable with
    ``app.state.search`` swapped for an in-memory-backed instance.
    """
    service: SearchService = request.app.state.search
    return service


@router.post("/search", response_model=SearchResponse)
async def search(
    body: SearchRequest, service: SearchService = Depends(get_search_service)
) -> SearchResponse:
    """Hybrid search over one of the three indexes (``policy_clauses``,
    ``precedent_cases``, ``controls``): rank fusion of a keyword ranking
    (Mongo ``$text``) and, for ``policy_clauses``, H4's semantic ranking,
    with the Index Contract's metadata filters pushed down to both.
    """
    return await service.search(body)
