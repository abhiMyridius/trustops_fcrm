"""S3's HTTP surface: ``POST /search`` (design A §2.3, §6.2).

Every hit is the fused output of the real ``reciprocal_rank_fusion`` over
whatever keyword and (for ``policy_clauses``) vector sources were wired
into the ``SearchService`` at startup -- the route itself never ranks,
scores or filters anything (``fcrm_policy.search.service`` does).

Every route needs a verified principal and an OPA allow (``authorize``, a
router-level dependency so a route added later cannot be left open), then
its own ``require_roles`` list -- the matrix ``ops/opa/data/
permissions.json`` encodes for ``service: policy``. The PO is in none of
them: precedent hits carry a case's band and control ratings.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fcrm_common.auth import require_roles
from fcrm_common.authz import opa_dependency
from fcrm_contracts.api.policy import SearchRequest, SearchResponse
from fcrm_contracts.common import Principal
from fcrm_contracts.enums import Role

from fcrm_policy.search.service import SearchService

#: Authenticate, then ask OPA for ``service: policy`` on the matched route
#: template (401 / 403 / 503 fail-closed, see ``fcrm_common.authz``).
authorize = opa_dependency("policy")

router = APIRouter(tags=["policy"], dependencies=[Depends(authorize)])

#: Who may search: the analyst, committee members reading a pack's cited
#: clauses, and the service account (the research/policy_gap agents' tools
#: run through H2). Never the PO.
_SEARCH_ROLES = (Role.ANALYST, Role.COMMITTEE, Role.SERVICE)


def get_search_service(request: Request) -> SearchService:
    """The ``SearchService`` the app wired into ``app.state`` at startup
    (``lifespan.py``) -- a FastAPI dependency so routes stay testable with
    ``app.state.search`` swapped for an in-memory-backed instance.
    """
    service: SearchService = request.app.state.search
    return service


@router.post("/search", response_model=SearchResponse)
async def search(
    body: SearchRequest,
    _principal: Annotated[Principal, Depends(require_roles(*_SEARCH_ROLES))],
    service: SearchService = Depends(get_search_service),
) -> SearchResponse:
    """Hybrid search over one of the three indexes (``policy_clauses``,
    ``precedent_cases``, ``controls``): rank fusion of a keyword ranking
    (Mongo ``$text``) and, for ``policy_clauses``, H4's semantic ranking,
    with the Index Contract's metadata filters pushed down to both.
    """
    return await service.search(body)
