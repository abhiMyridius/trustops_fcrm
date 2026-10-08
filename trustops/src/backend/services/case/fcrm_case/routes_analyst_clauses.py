"""The analyst adds a clause from a policy-corpus search to the case (ADR-74).

The analyst role only (OPA also denies the service account: Guard 7, a person
decides), on the caller's own open case. The clause is stored accepted, with the
text S3 holds at the case's pinned corpus version, then ``case.clause_added`` is
announced; Caseflow writes the ``clause.added`` audit record. Removing a clause
the analyst added goes through ``POST /cases/{id}/items/{item_id}`` like any
clause.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from fcrm_common.auth import require_roles
from fcrm_contracts.api.case_analyst_clause import ClauseAddBody, ClauseAddResult
from fcrm_contracts.common import Principal
from fcrm_contracts.enums import Role

from fcrm_case.analyst_write_service import AnalystWriteService
from fcrm_case.deps import authorize_case, get_analyst_write_service

router = APIRouter(tags=["analyst"], dependencies=[Depends(authorize_case)])

Analyst = Annotated[Principal, Depends(require_roles(Role.ANALYST))]
Service = Annotated[AnalystWriteService, Depends(get_analyst_write_service)]


@router.post("/cases/{case_id}/clauses", status_code=201)
async def add_clause(
    case_id: str, body: ClauseAddBody, principal: Analyst, service: Service
) -> ClauseAddResult:
    """Add a clause found by searching the corpus, with a reason of at least 20
    characters. It is accepted when added (no gate) and never scored. 409
    ``wrong_stage`` outside review or an unlocked recommendation, 409
    ``clause_on_case`` when it is already on the case and not removed, 422 when the
    pinned corpus has no such quotable clause, 503 when S3 cannot be read (nothing
    is stored). The same addition again replays.
    """
    return await service.add_clause(principal, case_id, body)
