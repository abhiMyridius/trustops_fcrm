"""The corpus-facing routes of S3: a clause by id, the policy search the web
calls, and the precedent search.

Every text a response carries comes from the stored clause (the system of
record) exactly as it is held: policy is quoted verbatim with a source
reference and never generated (rule 8). These routes only read; they sit in a
router without its own authorization because :mod:`fcrm_policy.routes` includes
it under the router-wide OPA check.
"""

from __future__ import annotations

import logging
from typing import Annotated, Optional

from fastapi import APIRouter, Depends
from fcrm_common.auth import require_roles
from fcrm_common.errors import NotFoundError
from fcrm_contracts.api.policy import (
    ClauseDetailOut,
    PolicySearchResultOut,
    PolicySearchWebRequest,
    PrecedentSearchRequest,
    PrecedentSearchResponse,
)
from fcrm_contracts.common import Principal

from fcrm_policy.corpus import ClauseStore, corpus_label
from fcrm_policy.deps import (
    ANALYST_SEARCH_ROLES,
    REFERENCE_ROLES,
    get_clause_store,
    get_search_service,
)
from fcrm_policy.search.service import SearchService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["policy"])


async def run_policy_search(
    body: PolicySearchWebRequest,
    principal: Principal,
    service: SearchService,
    clauses: ClauseStore,
) -> PolicySearchResultOut:
    """The web's policy search: the fused clause ranking, each hit carrying
    its verbatim text and both citations. ``body.case_id`` is logged and
    nothing more; it never narrows or re-ranks the search.
    """
    hits = await service.search_policy(body.q, body.top_k)
    version = await clauses.latest_version()
    logger.info(
        "policy search",
        extra={
            "case_id": body.case_id,
            "actor": principal.sub,
            "query_chars": len(body.q),
            "hits": len(hits),
        },
    )
    return PolicySearchResultOut(
        query=body.q,
        results=hits,
        corpus_version=corpus_label(version) if version is not None else "",
    )


@router.post("/policy/search", response_model=PolicySearchResultOut)
async def policy_search(
    body: PolicySearchWebRequest,
    principal: Annotated[Principal, Depends(require_roles(*ANALYST_SEARCH_ROLES))],
    service: Annotated[SearchService, Depends(get_search_service)],
    clauses: Annotated[ClauseStore, Depends(get_clause_store)],
) -> PolicySearchResultOut:
    """Search the policy corpus for ``body.q`` (design A §6.2). The web reaches
    the same search at ``POST /search`` (its ``policy.search`` path on this
    service's prefix), which tells the two request shapes apart.
    """
    return await run_policy_search(body, principal, service, clauses)


@router.get("/clauses/{clause_id}", response_model=ClauseDetailOut)
async def get_clause(
    clause_id: str,
    _principal: Annotated[Principal, Depends(require_roles(*REFERENCE_ROLES))],
    clauses: Annotated[ClauseStore, Depends(get_clause_store)],
    corpus_version: Optional[int] = None,
) -> ClauseDetailOut:
    """One clause: verbatim text, ``ref`` and structured ``source_ref``, page
    and lines, provenance. ``corpus_version`` pins a version (the latest when
    omitted).
    """
    clause = await clauses.get_clause(clause_id, corpus_version)
    if clause is None:
        raise NotFoundError(f"No clause {clause_id!r}.")
    return clause


@router.post("/precedents/search", response_model=PrecedentSearchResponse)
async def search_precedents(
    body: PrecedentSearchRequest,
    _principal: Annotated[Principal, Depends(require_roles(*ANALYST_SEARCH_ROLES))],
    service: Annotated[SearchService, Depends(get_search_service)],
) -> PrecedentSearchResponse:
    """Closed cases similar to ``body.query`` under its filters, with each
    one's control ratings and the mean rating per control (``precedent_avg``).
    """
    return await service.search_precedents(body)
