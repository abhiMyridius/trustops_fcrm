"""What S5's route modules share: authorization, the store, the directory, the
role sets and the visibility rule.

Every route needs a verified principal and an OPA allow (``authorize``, a
router-level dependency in :mod:`fcrm_decision.routes`, so a route added later
cannot be left open), then its own ``require_roles`` list: the matrix
``ops/opa/data/permissions.json`` encodes for ``service: decision``. Identity
comes from the token, never the body (D-23): a vote's member, a question's
asker and an answer's author are the caller.
"""

from __future__ import annotations

from typing import Annotated, Final

from fastapi import Depends, Request
from fcrm_common.authz import opa_dependency
from fcrm_common.errors import NotFoundError
from fcrm_contracts.common import Principal
from fcrm_contracts.enums import Role

from fcrm_decision.directory import CommitteeDirectory
from fcrm_decision.rows import PackRow
from fcrm_decision.store import DecisionStore

#: Authenticate, then ask OPA for ``service: decision`` on the matched route
#: template (401 / 403 / 503 fail-closed, see ``fcrm_common.authz``).
authorize = opa_dependency("decision")

#: Who may read a pack, its votes, its threads and a case's outcome.
READ_ROLES: Final = (Role.ANALYST, Role.COMMITTEE, Role.SERVICE)
#: Only a committee member votes or asks a question (Guard 7 denies the
#: service account in OPA as well).
COMMITTEE_ROLES: Final = (Role.COMMITTEE,)
#: Only the analyst releases a pack and answers a thread.
ANALYST_ROLES: Final = (Role.ANALYST,)
#: H1 builds, supersedes, confirms re-votes and records outcomes; the analyst
#: may also ask for a case's latest pack through ``POST /packs``.
SERVICE_ROLES: Final = (Role.SERVICE,)
BUILD_ROLES: Final = (Role.SERVICE, Role.ANALYST)

DEFAULT_LIMIT: Final = 50
MAX_LIMIT: Final = 200


def get_store(request: Request) -> DecisionStore:
    """The store wired onto ``app.state`` at startup (``lifespan.py``): a FastAPI
    dependency so routes stay testable with ``app.state.store`` swapped for
    :class:`fcrm_decision.store_memory.InMemoryDecisionStore`.
    """
    store: DecisionStore = request.app.state.store
    return store


def get_directory(request: Request) -> CommitteeDirectory:
    """The committee directory wired onto ``app.state`` at startup."""
    directory: CommitteeDirectory = request.app.state.directory
    return directory


StoreDep = Annotated[DecisionStore, Depends(get_store)]
DirectoryDep = Annotated[CommitteeDirectory, Depends(get_directory)]


def committee_view(principal: Principal) -> bool:
    """Whether the caller sees S5 as a committee member does: released packs
    only. The analyst and the service account see a pack from the moment it is
    built.
    """
    roles = set(principal.roles)
    return Role.COMMITTEE in roles and not roles & {Role.ANALYST, Role.SERVICE}


def viewer_id(principal: Principal) -> str | None:
    """The caller's user id when they hold a committee seat, else ``None`` (the
    analyst and the service account own no seat on the votes screen).
    """
    return principal.sub if committee_view(principal) else None


async def visible_pack(
    store: DecisionStore, principal: Principal, pack_id: str
) -> PackRow:
    """The pack, as far as this caller may know of it.

    Raises:
        NotFoundError: There is no such pack, or it was never released and the
            caller is a committee member (an unreleased pack does not exist for
            them).
    """
    pack = await store.get_pack(pack_id)
    if pack is None or (committee_view(principal) and pack.released_at is None):
        raise NotFoundError(f"no pack {pack_id}")
    return pack
