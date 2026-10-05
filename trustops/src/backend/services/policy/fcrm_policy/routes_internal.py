"""S3's internal routes, for the service account only: what H4 reads to build
the vector indexes and what H1 posts when a case closes.

``/internal/*`` is never public (the gateway answers 404 for it) and every
route here also requires the ``fcrm_service`` role, so a person's token is
refused even if OPA's analyst wildcard would let it through.
"""

from __future__ import annotations

import logging
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Query, Response
from fcrm_common.auth import require_roles
from fcrm_common.errors import NotFoundError
from fcrm_contracts.api.policy import (
    ClauseDetailOut,
    PrecedentIndexRecord,
    PrecedentRecordIn,
    PrecedentRecordOut,
)
from fcrm_contracts.common import Page, Principal

from fcrm_policy.corpus import DEFAULT_CLAUSE_PAGE, MAX_CLAUSE_PAGE, ClauseStore
from fcrm_policy.deps import INTERNAL_ROLES, get_clause_store, get_precedent_store
from fcrm_policy.precedents import (
    DEFAULT_PRECEDENT_PAGE,
    MAX_PRECEDENT_PAGE,
    PrecedentStore,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["policy-internal"])


@router.get("/internal/clauses", response_model=Page[ClauseDetailOut])
async def internal_clauses(
    _principal: Annotated[Principal, Depends(require_roles(*INTERNAL_ROLES))],
    clauses: Annotated[ClauseStore, Depends(get_clause_store)],
    limit: Annotated[int, Query(ge=1, le=MAX_CLAUSE_PAGE)] = DEFAULT_CLAUSE_PAGE,
    corpus_version: Optional[int] = None,
    cursor: Optional[str] = None,
) -> Page[ClauseDetailOut]:
    """One page of a corpus version's clauses in id order (the latest version
    when ``corpus_version`` is omitted): the records H4 embeds into
    ``policy_clauses``. Follow ``next_cursor`` until it is ``None``.

    Raises:
        NotFoundError: The corpus is empty or holds no such version.
    """
    version = corpus_version
    if version is None:
        version = await clauses.latest_version()
    if version is None:
        raise NotFoundError("The policy corpus is empty.")
    page = await clauses.page(version, cursor=cursor, limit=limit)
    if cursor is None and not page.items:
        raise NotFoundError(f"No clauses at corpus version {version}.")
    return page


@router.get("/internal/precedents", response_model=Page[PrecedentIndexRecord])
async def internal_precedents(
    _principal: Annotated[Principal, Depends(require_roles(*INTERNAL_ROLES))],
    precedents: Annotated[PrecedentStore, Depends(get_precedent_store)],
    limit: Annotated[int, Query(ge=1, le=MAX_PRECEDENT_PAGE)] = DEFAULT_PRECEDENT_PAGE,
    cursor: Optional[str] = None,
) -> Page[PrecedentIndexRecord]:
    """One page of the stored precedents in case-id order: the records H4
    embeds into ``precedent_cases``.
    """
    return await precedents.page(cursor=cursor, limit=limit)


@router.post("/internal/precedents", response_model=PrecedentRecordOut)
async def record_precedent(
    body: PrecedentRecordIn,
    response: Response,
    principal: Annotated[Principal, Depends(require_roles(*INTERNAL_ROLES))],
    precedents: Annotated[PrecedentStore, Depends(get_precedent_store)],
) -> PrecedentRecordOut:
    """Add a closed case to the precedents (H1 posts one on ``case.closed``).

    The row and the ``index.build`` request for ``precedent_cases`` are
    written in one transaction. Posting a case again is idempotent: an
    identical repost is a 200 that changes and enqueues nothing; a new case is
    a 201.
    """
    stored = await precedents.record(body)
    response.status_code = 201 if stored.created else 200
    logger.info(
        "precedent posted",
        extra={
            "case_id": body.case_id,
            "actor": principal.sub,
            "created": stored.created,
            "index_requested": stored.index_requested,
        },
    )
    return stored
