"""S3's configuration routes: ``policy/config/{section}`` and its draft,
preview and publish (the web's contract, ADR-30).

The analyst owns configuration (D-03), so every route here is the analyst's:
the committee is shown no configuration page, the PO has none, and the service
account may read nothing and write nothing (it inherits the analyst's grants
for reads elsewhere, so ``ops/opa/data/permissions.json`` denies it these five
routes by name, and ``require_roles`` refuses it a second time). The router
carries no authorization of its own because :mod:`fcrm_policy.routes` includes
it under the router-wide OPA check.

Concurrency is the web's ``If-Match``: the draft's ETag names the published
version it started from and its revision, so an edit made from a stale page is
a 409. ``POST .../draft/publish`` also honours ``Idempotency-Key``: a retry of
the same publish replays instead of publishing twice.
"""

from __future__ import annotations

import logging
from typing import Annotated, Final, Optional

from fastapi import APIRouter, Depends, Header, Request
from fcrm_common.auth import require_roles
from fcrm_contracts.api.policy_config import (
    ConfigDraftIn,
    ConfigPublishIn,
    ConfigSection,
    ImpactPreview,
)
from fcrm_contracts.common import Principal
from fcrm_contracts.enums import Role

from fcrm_policy.config_service import ConfigService
from fcrm_policy.idempotency import IdempotencyKeyHeader, checked_key

logger = logging.getLogger(__name__)

#: Who may use the configuration routes: the analyst, nobody else.
CONFIG_ROLES: Final = (Role.ANALYST,)

router = APIRouter(tags=["policy-config"])

IfMatch = Annotated[Optional[str], Header(alias="If-Match")]


def get_config_service(request: Request) -> ConfigService:
    """The configuration service the app wired into ``app.state`` at startup."""
    service: ConfigService = request.app.state.config
    return service


Analyst = Annotated[Principal, Depends(require_roles(*CONFIG_ROLES))]
Service = Annotated[ConfigService, Depends(get_config_service)]


def _actor(principal: Principal) -> str:
    """Who is named in the history: the display name, else the subject."""
    return principal.name or principal.sub


@router.get("/config/{section}", response_model=ConfigSection)
async def get_config(
    section: str, _principal: Analyst, service: Service
) -> ConfigSection:
    """One section: the data in force, its append-only version history, and
    the server-side draft with its ETag.
    """
    return await service.read(section)


@router.put("/config/{section}/draft", response_model=ConfigSection)
async def put_draft(
    section: str,
    body: ConfigDraftIn,
    principal: Analyst,
    service: Service,
    if_match: IfMatch = None,
) -> ConfigSection:
    """Add one change to the section's working set (a change back to the
    published value removes it). 409 when ``If-Match`` is stale or the
    published version moved under the draft; 422 naming the field when the path
    or the value does not suit the section.
    """
    return await service.save_draft(
        section,
        path=body.path,
        after=body.after,
        if_match=if_match,
        actor=_actor(principal),
    )


@router.delete("/config/{section}/draft", response_model=ConfigSection)
async def delete_draft(
    section: str,
    principal: Analyst,
    service: Service,
    if_match: IfMatch = None,
) -> ConfigSection:
    """Discard the section's working set."""
    return await service.discard_draft(
        section, if_match=if_match, actor=_actor(principal)
    )


@router.post("/config/{section}/draft/preview", response_model=ImpactPreview)
async def preview_draft(
    section: str, _principal: Analyst, service: Service
) -> ImpactPreview:
    """What publishing would change, replayed from stored data: nothing is
    written, and anything the data cannot answer is said to be not computable.
    """
    return await service.preview(section)


@router.post("/config/{section}/draft/publish", response_model=ConfigSection)
async def publish_draft(
    section: str,
    body: ConfigPublishIn,
    principal: Analyst,
    service: Service,
    if_match: IfMatch = None,
    idempotency_key: IdempotencyKeyHeader = None,
) -> ConfigSection:
    """Append the draft as the section's next immutable snapshot and put it
    into force; the version note must be at least 20 characters. The jurisdiction
    list publishes by activating the stored list, with its
    ``jurisdiction.list_published`` event, in the same transaction.
    """
    return await service.publish(
        section,
        note=body.note,
        if_match=if_match,
        idempotency_key=checked_key(idempotency_key),
        actor=_actor(principal),
    )
