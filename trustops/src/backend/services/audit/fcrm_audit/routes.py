"""S4's HTTP surface: ``POST /records`` and ``POST /proposals`` (both
idempotent), ``GET /records`` (the web's row shape), ``GET /records/{case_id}``,
``GET /proposals``, ``GET /head/{case_id}`` and ``GET /verify/{case_id}``.

No route here ever accepts a caller-supplied ``seq``, ``prev_hash`` or
``hash`` -- ``RecordAppend`` (what ``POST /records`` takes) has no fields
for them, so the store computes every one of them from the case's current
chain head, same rule-1-at-the-transport-boundary shape as S2's
``routes.py`` for scores.

Every route needs a verified principal and an OPA allow (``authorize``, a
router-level dependency so a route added later cannot be left open), then
its own ``require_roles`` list -- the matrix ``ops/opa/data/
permissions.json`` encodes for ``service: audit``. Only the service account
appends (every state change is recorded by the service that made it, never
by a person directly), and the PO reads nothing here: record summaries
carry score text ("Composite 9.8 · Band 2"), and S4 holds no ownership
record it could scope a PO's read to.

Idempotency: both ``POST`` routes honour an ``Idempotency-Key`` header. A
first use answers 201; a repeat of the same request answers 200 with the
stored result and writes nothing; the same key with a different body is a
409 problem (see :mod:`fcrm_audit.store`).
"""

from __future__ import annotations

import re
from typing import Annotated, Final, Optional

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fcrm_common.auth import require_roles
from fcrm_common.authz import opa_dependency
from fcrm_common.errors import ValidationError as FcrmValidationError
from fcrm_contracts.api.audit import (
    AuditRecord,
    AuditRecordRow,
    ChainHead,
    Proposal,
    ProposalAppend,
    RecordAppend,
    VerifyResult,
)
from fcrm_contracts.common import Page, Principal
from fcrm_contracts.enums import Role

from fcrm_audit.store import DEFAULT_PAGE_LIMIT, AuditStore

#: Authenticate, then ask OPA for ``service: audit`` on the matched route
#: template (401 / 403 / 503 fail-closed, see ``fcrm_common.authz``).
authorize = opa_dependency("audit")

router = APIRouter(tags=["audit"], dependencies=[Depends(authorize)])

MAX_PAGE_LIMIT = 200
MAX_IDEMPOTENCY_KEY_LENGTH: Final = 255
#: Printable ASCII with no whitespace: what ``deterministic_id`` and a UUID give.
_IDEMPOTENCY_KEY: Final = re.compile(r"^[\x21-\x7e]+$")

#: Who may append: the service account only (S1/H1/S5 write on behalf of
#: the actor named inside the record).
_APPEND_ROLES = (Role.SERVICE,)
#: Who may read a chain: analyst, committee and the service account. Not the
#: PO; not ``fcrm_observer`` either (ADR-19 scopes it to the harness
#: monitor, and ``ops/opa/tests`` pins that).
_READ_ROLES = (Role.ANALYST, Role.COMMITTEE, Role.SERVICE)

IdempotencyKeyHeader = Annotated[Optional[str], Header(alias="Idempotency-Key")]


def get_store(request: Request) -> AuditStore:
    """The audit store the app wired into ``app.state`` at startup
    (``lifespan.py``) -- a FastAPI dependency so routes stay testable
    with ``app.state.store`` swapped for :class:`fcrm_audit.store.
    InMemoryAuditStore`.
    """
    store: AuditStore = request.app.state.store
    return store


def checked_idempotency_key(raw: Optional[str]) -> Optional[str]:
    """``raw`` when it is a usable ``Idempotency-Key``, ``None`` when absent.

    Raises:
        ValidationError: The header is empty, longer than
            :data:`MAX_IDEMPOTENCY_KEY_LENGTH`, or holds whitespace or
            non-ASCII characters (a 422, not a stored key nobody can type).
    """
    if raw is None:
        return None
    if (
        not raw
        or len(raw) > MAX_IDEMPOTENCY_KEY_LENGTH
        or _IDEMPOTENCY_KEY.match(raw) is None
    ):
        raise FcrmValidationError(
            "Idempotency-Key must be 1 to 255 printable ASCII characters "
            "with no spaces.",
            errors=[{"loc": "header.Idempotency-Key", "msg": "malformed key"}],
        )
    return raw


def to_row(record: AuditRecord) -> AuditRecordRow:
    """``record`` in the web's row shape (``by`` = actor, ``what`` = summary,
    ``at`` = append time).
    """
    return AuditRecordRow(
        seq=record.seq,
        type=record.type,
        by=record.actor,
        what=record.summary,
        at=record.created_at,
        hash=record.hash,
        prev_hash=record.prev_hash,
    )


@router.post("/records", response_model=AuditRecord, status_code=201)
async def append_record(
    body: RecordAppend,
    response: Response,
    _principal: Annotated[Principal, Depends(require_roles(*_APPEND_ROLES))],
    idempotency_key: IdempotencyKeyHeader = None,
    store: AuditStore = Depends(get_store),
) -> AuditRecord:
    """Append one record to ``body.case_id``'s chain. The only chain write
    route S4 exposes (design A §2.4; ``docs/governance/audit-chain.md``).
    A repeated ``Idempotency-Key`` returns the record it already wrote with
    200 and appends nothing.
    """
    stored = await store.append(
        body, idempotency_key=checked_idempotency_key(idempotency_key)
    )
    if stored.replayed:
        response.status_code = 200
    return stored.value


@router.post("/proposals", response_model=Proposal, status_code=201)
async def append_proposal(
    body: ProposalAppend,
    response: Response,
    _principal: Annotated[Principal, Depends(require_roles(*_APPEND_ROLES))],
    idempotency_key: IdempotencyKeyHeader = None,
    store: AuditStore = Depends(get_store),
) -> Proposal:
    """Append one validated agent output to the case's evidence ledger. The
    ledger is append-only: a later version of a proposal is a new entry whose
    ``supersedes`` names the one it replaces. A repeated ``Idempotency-Key``
    returns the proposal it already stored with 200.
    """
    stored = await store.append_proposal(
        body, idempotency_key=checked_idempotency_key(idempotency_key)
    )
    if stored.replayed:
        response.status_code = 200
    return stored.value


@router.get("/proposals", response_model=Page[Proposal])
async def list_proposals(
    _principal: Annotated[Principal, Depends(require_roles(*_READ_ROLES))],
    case_id: Annotated[str, Query(min_length=1)],
    run_id: Optional[str] = None,
    agent: Optional[str] = None,
    kind: Optional[str] = None,
    cursor: Optional[str] = None,
    limit: int = Query(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    store: AuditStore = Depends(get_store),
) -> Page[Proposal]:
    """One page of ``case_id``'s proposals in ``seq`` order, optionally for
    one run, one agent and one kind.
    """
    return await store.proposals(
        case_id, run_id=run_id, agent=agent, kind=kind, cursor=cursor, limit=limit
    )


@router.get("/head/{case_id}", response_model=ChainHead)
async def chain_head(
    case_id: str,
    _principal: Annotated[Principal, Depends(require_roles(*_READ_ROLES))],
    store: AuditStore = Depends(get_store),
) -> ChainHead:
    """The last ``seq`` and hash of ``case_id``'s chain (``seq=0`` and the
    genesis hash for a case with no records): what a pack records so it can
    later show which records it was built from.
    """
    return await store.head(case_id)


@router.get("/verify/{case_id}", response_model=VerifyResult)
async def verify_case(
    case_id: str,
    _principal: Annotated[Principal, Depends(require_roles(*_READ_ROLES))],
    store: AuditStore = Depends(get_store),
) -> VerifyResult:
    """Recompute ``case_id``'s whole chain and report whether it is
    intact, and the first broken ``seq`` if not (``fcrm_audit.chain.
    verify_chain``, the only place a chain is ever verified). A case with
    no records yet verifies trivially (``ok=True``, ``record_count=0``).
    """
    return await store.verify(case_id)


@router.get("/records", response_model=Page[AuditRecordRow])
async def list_record_rows(
    _principal: Annotated[Principal, Depends(require_roles(*_READ_ROLES))],
    case_id: Annotated[str, Query(min_length=1)],
    cursor: Optional[str] = None,
    limit: int = Query(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    store: AuditStore = Depends(get_store),
) -> Page[AuditRecordRow]:
    """One page of ``case_id``'s chain in the web's row shape (web path
    ``audit/records?case_id=``), in ``seq`` order, oldest first.
    """
    page = await store.records_for_case(case_id, cursor=cursor, limit=limit)
    return Page(
        items=[to_row(r) for r in page.items],
        next_cursor=page.next_cursor,
        total=page.total,
    )


@router.get("/records/{case_id}", response_model=Page[AuditRecord])
async def list_records(
    case_id: str,
    _principal: Annotated[Principal, Depends(require_roles(*_READ_ROLES))],
    cursor: Optional[str] = None,
    limit: int = Query(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    store: AuditStore = Depends(get_store),
) -> Page[AuditRecord]:
    """One page of ``case_id``'s chain, in ``seq`` order, oldest first."""
    return await store.records_for_case(case_id, cursor=cursor, limit=limit)
