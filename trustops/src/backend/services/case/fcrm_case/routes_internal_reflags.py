"""S1's internal re-flag routes, called by H1's watchlist step:
``GET /internal/reflags/candidates``, ``POST /internal/reflags`` and
``GET /internal/cases/search`` (the agents' ``case_search`` tool, which the
watchlist agent reads the open cases of a country through).

Service-only at three layers (this router, OPA's ``case-any-internal-all-service``
rule and the gateway) and left out of the public OpenAPI document. The
candidate match is code (:func:`fcrm_case.domain.reflags.countries_matched`):
H1 refuses an agent's proposal for a case that is not a candidate. The post is
idempotent by the re-flag ids H1 derives, so a step that runs twice creates each
row once.
"""

from __future__ import annotations

import logging
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Query
from fcrm_common.auth import require_roles
from fcrm_common.errors import ValidationError as FcrmValidationError
from fcrm_contracts.api.case_internal_reflags import (
    ReflagCandidates,
    ReflagsPost,
    ReflagsPosted,
)
from fcrm_contracts.common import Principal
from fcrm_contracts.enums import Role
from fcrm_contracts.tools.case_search import CaseSearchOutput

from fcrm_case.deps import get_reflags
from fcrm_case.idempotency import IdempotencyKeyHeader, checked_key
from fcrm_case.reflag_store import ReflagStore

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/internal",
    tags=["internal"],
    include_in_schema=False,
    dependencies=[Depends(require_roles(Role.SERVICE))],
)

ServiceCaller = Annotated[Principal, Depends(require_roles(Role.SERVICE))]

#: Countries one candidate query may name (a list version adds a handful).
MAX_COUNTRIES = 50
#: The agents' ``case_search`` bounds (the tool refuses more before it asks).
MAX_QUERY_CHARS = 200
MAX_TOP_K = 20


@router.get("/reflags/candidates")
async def reflag_candidates(
    _principal: ServiceCaller,
    reflags: Annotated[ReflagStore, Depends(get_reflags)],
    countries: Annotated[list[str], Query(min_length=1, max_length=MAX_COUNTRIES)],
) -> ReflagCandidates:
    """The open cases whose geography or corridor names any of ``countries``,
    one entry per case and country, ordered by case id.
    """
    return ReflagCandidates(items=await reflags.candidates(countries))


@router.get("/cases/search")
async def search_cases(
    _principal: ServiceCaller,
    reflags: Annotated[ReflagStore, Depends(get_reflags)],
    q: Annotated[str, Query(min_length=1, max_length=MAX_QUERY_CHARS)],
    top_k: Annotated[int, Query(ge=1, le=MAX_TOP_K)] = 5,
    closed_only: bool = True,
    change_types: Annotated[Optional[str], Query(max_length=200)] = None,
    geography: Annotated[Optional[str], Query(max_length=120)] = None,
) -> CaseSearchOutput:
    """The agents' ``case_search`` tool: the cases exposed to ``geography`` (the
    same code match as the re-flag candidates), else those whose title holds
    ``q``; best match first. ``outcome`` is ``None`` for an open case, which is
    how the watchlist agent tells the open cases from precedents.
    """
    wanted = [t.strip() for t in (change_types or "").split(",") if t.strip()]
    found = await reflags.search_cases(
        q,
        geography=geography,
        change_types=wanted,
        closed_only=closed_only,
        top_k=top_k,
    )
    return CaseSearchOutput(hits=found)


@router.post("/reflags")
async def post_reflags(
    body: ReflagsPost,
    principal: ServiceCaller,
    reflags: Annotated[ReflagStore, Depends(get_reflags)],
    idempotency_key: IdempotencyKeyHeader = None,
) -> ReflagsPosted:
    """Hold the re-flags H1 proposes for one list version, once each.

    Raises:
        fcrm_common.errors.ValidationError: A re-flag names a case S1 does not
            hold, or does not belong to ``list_version``.
    """
    checked_key(idempotency_key)
    stray = [r.reflag_id for r in body.reflags if r.list_version != body.list_version]
    if stray:
        raise FcrmValidationError(
            "Every re-flag in one post belongs to the post's list version.",
            errors=[
                {"loc": "body.reflags", "msg": f"{rid}: other version"} for rid in stray
            ],
        )
    posted = await reflags.create_many(body.reflags)
    logger.info(
        "re-flags posted",
        extra={
            "list_version": body.list_version,
            "reflags_created": posted.created,
            "replayed": posted.replayed,
            "caller": principal.sub,
        },
    )
    return posted
