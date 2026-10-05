"""S3's HTTP surface: ``POST /search`` (design A §2.3, §6.2) and the read-only
reference data the agent tools need: jurisdiction lists, field sets and
control inventories.

Every hit is the fused output of the real ``reciprocal_rank_fusion`` over
whatever keyword and (for ``policy_clauses``) vector sources were wired
into the ``SearchService`` at startup -- the route itself never ranks,
scores or filters anything (``fcrm_policy.search.service`` does). The
reference-data routes only read stored versions
(:mod:`fcrm_policy.reference`); a version that does not exist is a 404.

Every route needs a verified principal and an OPA allow (``authorize``, a
router-level dependency so a route added later cannot be left open), then
its own ``require_roles`` list -- the matrix ``ops/opa/data/
permissions.json`` encodes for ``service: policy``. The PO is in none of
them: precedent hits carry a case's band and control ratings.
"""

from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Query, Request
from fcrm_common.auth import require_roles
from fcrm_common.authz import opa_dependency
from fcrm_common.errors import NotFoundError
from fcrm_contracts.api.policy import (
    ControlInventoryOut,
    ControlVersionOut,
    FieldSetOut,
    JurisdictionListOut,
    JurisdictionVersionOut,
    ResolvedFieldSetOut,
    SearchRequest,
    SearchResponse,
)
from fcrm_contracts.common import Page, Principal
from fcrm_contracts.enums import Domain, Role

from fcrm_policy.reference import ReferenceStore, parse_change_types, resolve_fields
from fcrm_policy.search.service import SearchService

#: Authenticate, then ask OPA for ``service: policy`` on the matched route
#: template (401 / 403 / 503 fail-closed, see ``fcrm_common.authz``).
authorize = opa_dependency("policy")

router = APIRouter(tags=["policy"], dependencies=[Depends(authorize)])

#: Who may search: the analyst, committee members reading a pack's cited
#: clauses, and the service account (the research/policy_gap agents' tools
#: run through H2). Never the PO.
_SEARCH_ROLES = (Role.ANALYST, Role.COMMITTEE, Role.SERVICE)
#: Who may read the reference data: the same three. The PO's own field-set
#: view is served by S1, which resolves the field set itself.
_REFERENCE_ROLES = _SEARCH_ROLES


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


def get_reference_store(request: Request) -> ReferenceStore:
    """The reference-data store the app wired into ``app.state`` at startup
    (``lifespan.py``); swapped for an in-memory one in unit tests.
    """
    store: ReferenceStore = request.app.state.reference
    return store


@router.get("/jurisdictions", response_model=JurisdictionListOut)
async def get_jurisdictions(
    _principal: Annotated[Principal, Depends(require_roles(*_REFERENCE_ROLES))],
    version: Optional[str] = None,
    store: ReferenceStore = Depends(get_reference_store),
) -> JurisdictionListOut:
    """One jurisdiction list: ``version`` is a version id (``v2026-06``),
    ``latest``/``current`` or none for the active list, or ``fixture`` for the
    synthetic re-flag list.
    """
    listing = await store.jurisdiction_list(version)
    if listing is None:
        raise NotFoundError(f"No jurisdiction list {version!r}.")
    return listing


@router.get("/jurisdictions/versions", response_model=Page[JurisdictionVersionOut])
async def list_jurisdiction_versions(
    _principal: Annotated[Principal, Depends(require_roles(*_REFERENCE_ROLES))],
    store: ReferenceStore = Depends(get_reference_store),
) -> Page[JurisdictionVersionOut]:
    """Every stored jurisdiction list version, the active one flagged."""
    items = await store.jurisdiction_versions()
    return Page(items=items, next_cursor=None, total=len(items))


@router.get("/field-sets/{version}", response_model=FieldSetOut)
async def get_field_set(
    version: str,
    _principal: Annotated[Principal, Depends(require_roles(*_REFERENCE_ROLES))],
    store: ReferenceStore = Depends(get_reference_store),
) -> FieldSetOut:
    """One field-set version (``v6`` or ``6``; ``latest`` is the active one)."""
    field_set = await store.field_set(version)
    if field_set is None:
        raise NotFoundError(f"No field set {version!r}.")
    return field_set


@router.get("/field-sets/{version}/resolve", response_model=ResolvedFieldSetOut)
async def resolve_field_set(
    version: str,
    _principal: Annotated[Principal, Depends(require_roles(*_REFERENCE_ROLES))],
    change_types: Annotated[
        Optional[str], Query(description="Comma-separated change types.")
    ] = None,
    store: ReferenceStore = Depends(get_reference_store),
) -> ResolvedFieldSetOut:
    """The fields a case with ``change_types`` must fill at this field-set
    version: always-required first, then each type's own fields in the order
    given (the same arithmetic as S1's ``resolve_required_fields``).
    """
    field_set = await store.field_set(version)
    if field_set is None:
        raise NotFoundError(f"No field set {version!r}.")
    return resolve_fields(field_set, parse_change_types(change_types))


@router.get("/controls", response_model=ControlInventoryOut)
async def get_controls(
    _principal: Annotated[Principal, Depends(require_roles(*_REFERENCE_ROLES))],
    version: Optional[str] = None,
    domain: Optional[Domain] = None,
    store: ReferenceStore = Depends(get_reference_store),
) -> ControlInventoryOut:
    """One control-inventory version (``v9`` or ``9``; the active one when
    omitted), optionally only the controls of one risk ``domain``.
    """
    inventory = await store.control_inventory(
        version, domain.value if domain is not None else None
    )
    if inventory is None:
        raise NotFoundError(f"No control inventory {version!r}.")
    return inventory


@router.get("/controls/versions", response_model=Page[ControlVersionOut])
async def list_control_versions(
    _principal: Annotated[Principal, Depends(require_roles(*_REFERENCE_ROLES))],
    store: ReferenceStore = Depends(get_reference_store),
) -> Page[ControlVersionOut]:
    """Every stored control-inventory version, the active one flagged."""
    items = await store.control_versions()
    return Page(items=items, next_cursor=None, total=len(items))
