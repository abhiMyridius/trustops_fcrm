"""S1's analyst re-flag routes (the web's ``GET /reflags`` and ``POST
/reflags/{id}/acknowledge``; shapes in :mod:`fcrm_contracts.api.case_analyst_reflags`).

Only an analyst reads or acknowledges a re-flag: the PO, the committee and the
observer never see one (OPA denies them and :func:`require_roles` agrees), and
the service account never decides it (Guard 7). An acknowledgement needs a
reason of at least 20 characters, is stored and announced as ``gate.decided`` in
one transaction, and is allowed on a closed case: the re-flag is about the list,
not about the case's stage. A re-flag changes no score and reopens nothing.
"""

from __future__ import annotations

import logging
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Query, Response
from fcrm_common.auth import require_roles
from fcrm_common.errors import NotFoundError
from fcrm_contracts.api.case_analyst_reflags import ReflagAcknowledge, ReflagOut
from fcrm_contracts.common import Page, Principal
from fcrm_contracts.enums import Role

from fcrm_case.deps import authorize_case, get_reflags
from fcrm_case.reflag_store import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT, ReflagStore

logger = logging.getLogger(__name__)

router = APIRouter(tags=["case"], dependencies=[Depends(authorize_case)])

Analyst = Annotated[Principal, Depends(require_roles(Role.ANALYST))]


@router.get("/reflags")
async def list_reflags(
    _principal: Analyst,
    reflags: Annotated[ReflagStore, Depends(get_reflags)],
    cursor: Optional[str] = None,
    limit: int = Query(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
) -> Page[ReflagOut]:
    """The watchlist re-flags, newest first, acknowledged ones included."""
    return await reflags.list_page(cursor=cursor, limit=limit)


@router.post(
    "/reflags/{reflag_id}/acknowledge",
    responses={
        200: {"description": "Acknowledged (or an identical acknowledgement again)."},
        404: {"description": "No such re-flag."},
        409: {"description": "Already acknowledged with another reason."},
    },
)
async def acknowledge_reflag(
    reflag_id: str,
    body: ReflagAcknowledge,
    response: Response,
    principal: Analyst,
    reflags: Annotated[ReflagStore, Depends(get_reflags)],
) -> ReflagOut:
    """Acknowledge one re-flag with a reason of at least 20 characters.

    The decision is stored and ``gate.decided`` (``reflag_ack``) is emitted in
    the same transaction; H1 then records ``reflag.acknowledged``. The same
    acknowledgement again changes nothing.

    Raises:
        NotFoundError: No such re-flag.
        fcrm_case.reflag_store.ReflagAlreadyAcknowledgedError: It was
            acknowledged with a different reason.
    """
    write = await reflags.acknowledge(reflag_id, body, by=principal.sub)
    if write is None:
        raise NotFoundError(f"no such re-flag {reflag_id}")
    logger.info(
        "re-flag acknowledged",
        extra={
            "case_id": write.row.case_id,
            "reflag_id": reflag_id,
            "replayed": write.replayed,
        },
    )
    return write.row.out()
