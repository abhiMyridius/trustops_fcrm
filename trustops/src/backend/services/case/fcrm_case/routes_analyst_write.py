"""The analyst's write routes (scenes 8-16), in the shapes the web posts (ADR-30).

Every route admits the analyst role only (OPA also denies the service account:
Guard 7, a human decides) and the service requires the case to be the caller's
own and open. A route that decides a gate stores the decision first and only then
announces it (:mod:`fcrm_case.analyst_write_store`). There is no route that takes
more than one item: a JSON array body is refused by the model (422, rule 2).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from fcrm_common.auth import require_roles
from fcrm_contracts.api.case_analyst_write import (
    AcknowledgeBody,
    AcknowledgeResult,
    FastLaneBody,
    GateWriteResult,
    ItemDecisionBody,
    ItemDecisionResult,
)
from fcrm_contracts.common import Principal
from fcrm_contracts.enums import Role

from fcrm_case.analyst_write_service import AnalystWriteService
from fcrm_case.deps import authorize_case, get_analyst_write_service

router = APIRouter(tags=["analyst"], dependencies=[Depends(authorize_case)])

Analyst = Annotated[Principal, Depends(require_roles(Role.ANALYST))]
Service = Annotated[AnalystWriteService, Depends(get_analyst_write_service)]


@router.post("/cases/{case_id}/items/{item_id}")
async def decide_item(
    case_id: str,
    item_id: str,
    body: ItemDecisionBody,
    principal: Analyst,
    service: Service,
) -> ItemDecisionResult:
    """Decide ONE review item (Gate A, rule 2): accept or remove a clause, confirm,
    override or remove a control, confirm or override a rating, explain a critic
    finding. An override, a removal and an explanation need a reason of at least
    20 characters. The decision is stored, then ``gate.decided review_item`` is
    emitted; Caseflow re-scores from the stored value when it changed an input.
    """
    return await service.decide_item(principal, case_id, item_id, body)


@router.post("/cases/{case_id}/acknowledge", status_code=202)
async def acknowledge(
    case_id: str, body: AcknowledgeBody, principal: Analyst, service: Service
) -> AcknowledgeResult:
    """Acknowledge a boundary flag or a band change (R3): ``gate.decided ack`` for
    every open gate of that kind, read from the score that opened it.
    """
    return await service.acknowledge(principal, case_id, body)


@router.post("/cases/{case_id}/screening", status_code=202)
async def decide_screening(
    case_id: str, body: FastLaneBody, principal: Analyst, service: Service
) -> GateWriteResult:
    """Confirm or decline the fast lane the screening agent proposed (R4):
    ``gate.decided fast_lane_confirm``. Declining needs a reason.
    """
    return await service.decide_fast_lane(principal, case_id, body)
