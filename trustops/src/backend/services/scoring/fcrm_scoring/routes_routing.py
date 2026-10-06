"""S2's routing-rules read (the web's ``GET scoring/routing-rules/{id}``) and
the two service-only routes S3's configuration screens publish through.

The read is for the analyst, the committee (who see the rules a pack was routed
by) and the service account. The two ``POST /internal/*`` routes are the service
account's alone: ``require_roles(Role.SERVICE)`` refuses a person's token even
where OPA's analyst wildcard would let it through (and OPA denies the analyst
these paths by name). The router carries no authorization of its own because
:mod:`fcrm_scoring.routes` includes it under the router-wide OPA check.
"""

from __future__ import annotations

import logging
from typing import Annotated, Final

from fastapi import APIRouter, Depends, Request
from fcrm_common.auth import require_roles
from fcrm_common.errors import NotFoundError, UpstreamUnavailableError
from fcrm_contracts.api.scoring_routing import (
    PublishedVersionOut,
    RoutingRulesOut,
    RoutingRulesPublishIn,
    ScoringModelPublishIn,
)
from fcrm_contracts.common import Principal
from fcrm_contracts.enums import Role

from fcrm_scoring.config_publish import PublishStore
from fcrm_scoring.routing_rules import RoutingStore, parse_version, routing_out

logger = logging.getLogger(__name__)

#: Who may read the routing rules a case is pinned to.
READ_ROLES: Final = (Role.SERVICE, Role.ANALYST, Role.COMMITTEE)
#: Who may put a published version into force: S3's service account.
PUBLISH_ROLES: Final = (Role.SERVICE,)

router = APIRouter(tags=["scoring-routing"])


def get_routing_store(request: Request) -> RoutingStore:
    """The routing store the app wired into ``app.state`` at startup; unwired
    is a 503 (fail closed).
    """
    store: RoutingStore | None = getattr(request.app.state, "routing", None)
    if store is None:
        raise UpstreamUnavailableError(
            "Routing rules are not wired.", dependency="mongo"
        )
    return store


def get_publish_store(request: Request) -> PublishStore:
    """The publish store the app wired into ``app.state`` at startup; unwired
    is a 503 (fail closed).
    """
    store: PublishStore | None = getattr(request.app.state, "publisher", None)
    if store is None:
        raise UpstreamUnavailableError("Publishing is not wired.", dependency="mongo")
    return store


@router.get("/routing-rules/{version}", response_model=RoutingRulesOut)
async def routing_rules_by_version(
    version: str,
    _principal: Annotated[Principal, Depends(require_roles(*READ_ROLES))],
    store: Annotated[RoutingStore, Depends(get_routing_store)],
) -> RoutingRulesOut:
    """One routing rules version (``v4`` or ``4``; ``active`` for the one in
    force): R1 to R6 as ``{id, rule}`` rows, the parameter table and the
    committee quorum.

    Raises:
        NotFoundError: There is no such version.
    """
    found = await store.routing_rules(parse_version(version))
    if found is None:
        raise NotFoundError(f"No routing rules version {version!r}.")
    return routing_out(found)


@router.post("/internal/models", response_model=PublishedVersionOut)
async def publish_model(
    body: ScoringModelPublishIn,
    principal: Annotated[Principal, Depends(require_roles(*PUBLISH_ROLES))],
    store: Annotated[PublishStore, Depends(get_publish_store)],
) -> PublishedVersionOut:
    """Make ``body.model`` the active scoring model, if ``body.base_version`` is
    the one active now (409 otherwise) and the model is valid (422). A repeat of
    a publish that already happened answers the same version with
    ``created: false``.
    """
    version, created = await store.publish_model(body.base_version, body.model)
    logger.info(
        "scoring model published",
        extra={
            "config_version": version,
            "newly_written": created,
            "published_by": body.published_by,
            "caller": principal.sub,
        },
    )
    return PublishedVersionOut(version=version, created=created)


@router.post("/internal/routing-rules", response_model=PublishedVersionOut)
async def publish_routing(
    body: RoutingRulesPublishIn,
    principal: Annotated[Principal, Depends(require_roles(*PUBLISH_ROLES))],
    store: Annotated[PublishStore, Depends(get_publish_store)],
) -> PublishedVersionOut:
    """Make ``body.routing`` the active routing rules, under the same
    ``base_version`` and repeat rules as :func:`publish_model`.
    """
    version, created = await store.publish_routing(body.base_version, body.routing)
    logger.info(
        "routing rules published",
        extra={
            "config_version": version,
            "newly_written": created,
            "published_by": body.published_by,
            "caller": principal.sub,
        },
    )
    return PublishedVersionOut(version=version, created=created)
