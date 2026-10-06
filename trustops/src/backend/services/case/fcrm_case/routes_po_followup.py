"""The product owner's follow-up routes after submission (scenes 11 and 19), in the
shapes the web's PO screens read (ADR-30).

Like :mod:`fcrm_case.routes_po`, this module is deliberately thin and blind: it
imports only the PO contract, the platform's common pieces and the follow-up
service, and ``test_po_followup_router_imports`` fails if it ever imports anything
that holds analyst, score or harness data. Every route authenticates, asks OPA,
requires the PO role (the service requires the caller to own the case), and
answers through :class:`fcrm_case.po_guard.PoGuardedRoute`, which re-scans the JSON
for restricted keys and terms and replaces it with 500 ``rule_violation:R3`` when
it finds one.

``GET /cases/{id}/conditions`` is not here: it is one route with an analyst's and an
owner's shape (:mod:`fcrm_case.routes_analyst_close`), whose owner branch is
:meth:`fcrm_case.po_followup_service.PoFollowupService.conditions_page`.
``GET /cases/{id}/outcome`` is not here either, for the same reason: the owner's plain
outcome and the analyst's and committee's full one are one route
(:mod:`fcrm_case.routes_analyst_read`, ``shared_router``), whose owner branch is
:meth:`fcrm_case.po_followup_service.PoFollowupService.outcome`.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from fcrm_common.auth import require_roles
from fcrm_contracts.api.case_po import ConditionPO, InboxPO
from fcrm_contracts.api.case_po_followup import (
    ConditionEvidenceBody,
    ThreadAnswerBody,
)
from fcrm_contracts.common import Principal
from fcrm_contracts.enums import Role

from fcrm_case.deps import authorize_case, get_po_followup_service
from fcrm_case.po_followup_service import PoFollowupService
from fcrm_case.po_guard import PoGuardedRoute

router = APIRouter(
    tags=["po"], route_class=PoGuardedRoute, dependencies=[Depends(authorize_case)]
)

Owner = Annotated[Principal, Depends(require_roles(Role.PO))]
Service = Annotated[PoFollowupService, Depends(get_po_followup_service)]


@router.get("/cases/{case_id}/inbox")
async def get_inbox(case_id: str, principal: Owner, service: Service) -> InboxPO:
    """The questions FCRM asked, by round, each "Re: <field>", with the owner's
    answers. Nothing the analyst keeps to themselves.
    """
    return await service.inbox(principal, case_id)


@router.post("/cases/{case_id}/clarifications/{thread_id}/answer")
async def answer_question(
    case_id: str,
    thread_id: str,
    body: ThreadAnswerBody,
    principal: Owner,
    service: Service,
) -> InboxPO:
    """Answer one question (text, optionally values and documents). The answer
    that closes the round freezes submission v2 and tells Caseflow which fields
    changed and which documents were added.
    """
    return await service.answer(principal, case_id, thread_id, body)


@router.post("/cases/{case_id}/conditions/{condition_id}/evidence")
async def evidence_condition(
    case_id: str,
    condition_id: str,
    body: ConditionEvidenceBody,
    principal: Owner,
    service: Service,
) -> ConditionPO:
    """Attach evidence to one of the owner's own conditions (allowed after the
    case is closed): its status becomes ``evidence_submitted`` and FCRM reviews it.
    """
    return await service.evidence(principal, case_id, condition_id, body)
