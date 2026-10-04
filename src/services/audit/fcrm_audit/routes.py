"""S4's HTTP surface: ``POST /records``, ``GET /verify/{case_id}`` and
``GET /records/{case_id}``.

No route here ever accepts a caller-supplied ``seq``, ``prev_hash`` or
``hash`` -- ``RecordAppend`` (what ``POST /records`` takes) has no fields
for them, so the store computes every one of them from the case's current
chain head, same rule-1-at-the-transport-boundary shape as S2's
``routes.py`` for scores.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fcrm_contracts.api.audit import AuditRecord, RecordAppend, VerifyResult
from fcrm_contracts.common import Page

from fcrm_audit.store import DEFAULT_PAGE_LIMIT, AuditStore

router = APIRouter(tags=["audit"])

MAX_PAGE_LIMIT = 200


def get_store(request: Request) -> AuditStore:
    """The audit store the app wired into ``app.state`` at startup
    (``lifespan.py``) -- a FastAPI dependency so routes stay testable
    with ``app.state.store`` swapped for :class:`fcrm_audit.store.
    InMemoryAuditStore`.
    """
    store: AuditStore = request.app.state.store
    return store


@router.post("/records", response_model=AuditRecord, status_code=201)
async def append_record(
    body: RecordAppend, store: AuditStore = Depends(get_store)
) -> AuditRecord:
    """Append one record to ``body.case_id``'s chain. The only write route
    S4 exposes (design A §2.4; ``docs/governance/audit-chain.md``).
    """
    return await store.append(body)


@router.get("/verify/{case_id}", response_model=VerifyResult)
async def verify_case(
    case_id: str, store: AuditStore = Depends(get_store)
) -> VerifyResult:
    """Recompute ``case_id``'s whole chain and report whether it is
    intact, and the first broken ``seq`` if not (``fcrm_audit.chain.
    verify_chain``, the only place a chain is ever verified). A case with
    no records yet verifies trivially (``ok=True``, ``record_count=0``).
    """
    return await store.verify(case_id)


@router.get("/records/{case_id}", response_model=Page[AuditRecord])
async def list_records(
    case_id: str,
    cursor: Optional[str] = None,
    limit: int = Query(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    store: AuditStore = Depends(get_store),
) -> Page[AuditRecord]:
    """One page of ``case_id``'s chain, in ``seq`` order, oldest first."""
    return await store.records_for_case(case_id, cursor=cursor, limit=limit)
