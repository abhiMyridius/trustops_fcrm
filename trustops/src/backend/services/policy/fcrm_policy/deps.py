"""What S3's routes read from ``app.state`` and who may call them.

Each getter is a FastAPI dependency so a route stays testable with its
``app.state`` entry swapped for an in-memory twin. The role tuples are the
route-level half of authorization (``fcrm_common.auth.require_roles``); OPA's
route rules (``ops/opa/data/permissions.json``) are the other half and decide
first, router-wide.
"""

from __future__ import annotations

from typing import Final

from fastapi import Request
from fcrm_contracts.enums import Role

from fcrm_policy.corpus import ClauseStore
from fcrm_policy.precedents import PrecedentStore
from fcrm_policy.reference import ReferenceStore
from fcrm_policy.search.service import SearchService

#: Who may search: the analyst, committee members reading a pack's cited
#: clauses, and the service account (the research/policy_gap agents' tools run
#: through H2). Never the PO.
SEARCH_ROLES: Final = (Role.ANALYST, Role.COMMITTEE, Role.SERVICE)
#: Who may read the reference data: the same three. The PO's own field-set
#: view is served by S1, which resolves the field set itself.
REFERENCE_ROLES: Final = SEARCH_ROLES
#: Who may run the web's policy search and the precedent search: the analyst
#: and the service account (design A §2.3); committee members read clauses by
#: id, they do not search.
ANALYST_SEARCH_ROLES: Final = (Role.ANALYST, Role.SERVICE)
#: The H1/H4 internal routes: the service account only.
INTERNAL_ROLES: Final = (Role.SERVICE,)


def get_search_service(request: Request) -> SearchService:
    """The ``SearchService`` the app wired into ``app.state`` at startup
    (``lifespan.py``).
    """
    service: SearchService = request.app.state.search
    return service


def get_reference_store(request: Request) -> ReferenceStore:
    """The reference-data store the app wired into ``app.state`` at startup."""
    store: ReferenceStore = request.app.state.reference
    return store


def get_clause_store(request: Request) -> ClauseStore:
    """The clause store the app wired into ``app.state`` at startup."""
    store: ClauseStore = request.app.state.clauses
    return store


def get_precedent_store(request: Request) -> PrecedentStore:
    """The precedent store the app wired into ``app.state`` at startup."""
    store: PrecedentStore = request.app.state.precedents
    return store
