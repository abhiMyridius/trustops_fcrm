"""S1's analyst routes for closing a case and tracking its conditions, in the
web's shapes (:mod:`fcrm_contracts.api.case_analyst_close`).

* ``POST /cases/{case_id}/close``: R1's delegated close. S1 only *requests* it:
  it records the request and publishes ``case.close_requested``; H1 checks
  Guard 5 and S5 records the outcome, and the case is closed when S1 receives
  the outcome back (``PUT /internal/cases/{id}/outcome``).
* ``GET /cases/{case_id}/conditions``: the conditions an approval activated, as
  the analyst reads them. (The same path serves the PO a plainer shape in a
  later lane; here the PO is refused.)
* ``PATCH /conditions/{condition_id}``: the analyst moves a condition's status
  (with a reason of at least 20 characters) or notes the evidence they reviewed.

Every write needs the case's own analyst, and a close needs the case open. A
condition can still be updated after the case closes: conditions outlive the
decision (rule 5 allows ``condition_update`` on a closed case).
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from fcrm_common.auth import require_roles
from fcrm_common.errors import (
    ForbiddenError,
    GateRequiredError,
    NotFoundError,
    RuleViolationError,
    ValidationError,
)
from fcrm_common.time import utcnow
from fcrm_contracts.api.case_analyst_close import (
    CloseCaseRequest,
    CloseRequested,
    ConditionAnalyst,
    ConditionPatch,
    ConditionStatus,
)
from fcrm_contracts.api.case_internal import UiActor
from fcrm_contracts.common import Page, Principal
from fcrm_contracts.enums import Role

from fcrm_case.deps import (
    authorize_case,
    get_outcomes,
    get_po_followup_service,
    get_store,
    get_writer,
)
from fcrm_case.domain.activity import has_restricted_text
from fcrm_case.domain.outcome import CloseRequestRow
from fcrm_case.domain.record import CaseRecord
from fcrm_case.domain.views import ConditionRow
from fcrm_case.errors import ensure_open
from fcrm_case.outcome_store import OutcomeStore
from fcrm_case.po_guard import guard_response
from fcrm_case.sse import ActivityWriter
from fcrm_case.store import CaseStore

logger = logging.getLogger(__name__)

router = APIRouter(tags=["case"], dependencies=[Depends(authorize_case)])

Analyst = Annotated[Principal, Depends(require_roles(Role.ANALYST))]
#: ``GET conditions`` is one route with two shapes: the analyst's tracker and the
#: owner's plain list.
AnalystOrOwner = Annotated[Principal, Depends(require_roles(Role.ANALYST, Role.PO))]

#: A delegated close is for Band 1-2 (R1); Band 3-4 goes to the committee (R2).
DELEGATED_BANDS = (1, 2)

#: The web's four condition statuses; anything else a row holds reads as open.
_STATUS: dict[str, ConditionStatus] = {
    "open": "open",
    "scheduled": "scheduled",
    "evidence_submitted": "evidence_submitted",
    "closed": "closed",
}


def _actor(principal: Principal) -> UiActor:
    return UiActor(id=principal.sub, role=Role.ANALYST.value, component="case")


async def _case_or_404(store: CaseStore, case_id: str) -> CaseRecord:
    record = await store.get(case_id, with_versions=False)
    if record is None:
        raise NotFoundError(f"no such case {case_id}")
    return record


def _require_own_case(principal: Principal, record: CaseRecord) -> None:
    """An analyst writes only to the case they hold (design A §2.1)."""
    if record.analyst_id != principal.sub:
        raise ForbiddenError("only the analyst who holds this case may change it")


def to_condition_analyst(row: ConditionRow) -> ConditionAnalyst:
    """``row`` as the web's ``ConditionAnalyst``."""
    kind = str(row.source.get("kind", ""))
    origin = row.source.get("label") or kind.replace("_", " ")
    return ConditionAnalyst.model_validate(
        {
            "id": row.condition_id,
            "text": row.text,
            "owner": row.owner_label,
            "due": row.due_label,
            "status": _STATUS.get(row.status, "open"),
            "from": str(origin),
            "due_date": row.due_date,
            "note": row.note,
        }
    )


@router.post("/cases/{case_id}/close", status_code=202)
async def close_case(
    case_id: str,
    body: CloseCaseRequest,
    principal: Analyst,
    store: Annotated[CaseStore, Depends(get_store)],
    outcomes: Annotated[OutcomeStore, Depends(get_outcomes)],
    writer: Annotated[ActivityWriter, Depends(get_writer)],
) -> CloseRequested:
    """Ask for a delegated close (R1, Band 1-2).

    Refused unless the case is open, the analyst holds it, its latest score
    routes to the analyst (Band 1-2) and its recommendation is locked; a summary
    that names a score, band or rating is refused too, because the owner reads
    it. Asking twice with the same content is a no-op; a different second
    request is a 409.

    Raises:
        NotFoundError: No such case.
        ForbiddenError: The caller is not the case's analyst.
        fcrm_case.errors.CaseClosedError: The case is closed.
        RuleViolationError: R1: no score yet, or the band routes to committee.
        GateRequiredError: The recommendation is not locked.
        ValidationError: The plain summary names a restricted term.
    """
    record = await _case_or_404(store, case_id)
    _require_own_case(principal, record)
    ensure_open(record)
    band = await outcomes.latest_band(case_id)
    if band is None or band not in DELEGATED_BANDS:
        raise RuleViolationError(
            "a delegated close is only for a case whose latest score the analyst "
            "may close; this one goes to the committee",
            rule_id="R1",
            case_id=case_id,
        )
    if not record.stage_flags.locked:
        raise GateRequiredError(
            "lock the recommendation before closing the case", gate_type="lock"
        )
    if has_restricted_text(body.plain_summary):
        raise ValidationError(
            "the plain summary is read by the owner and may not name a score, "
            "band, rating or control",
            errors=[{"loc": "body.plain_summary", "msg": "restricted term"}],
        )
    request = CloseRequestRow(
        outcome=body.outcome,
        reason=body.reason,
        plain_summary=body.plain_summary,
        requested_by=principal.sub,
        requested_at=utcnow(),
        band=band,
    )
    write = await outcomes.request_close(case_id, request, actor=_actor(principal))
    if write is None:
        raise NotFoundError(f"no such case {case_id}")
    for entry in write.entries:
        writer.publish(entry)
    logger.info(
        "delegated close requested",
        extra={
            "case_id": case_id,
            "outcome": body.outcome.value,
            "replayed": write.replayed,
        },
    )
    return CloseRequested(
        case_id=case_id, outcome=body.outcome, replayed=write.replayed
    )


@router.get("/cases/{case_id}/conditions", response_model=Page[ConditionAnalyst])
async def list_conditions(
    case_id: str,
    request: Request,
    principal: AnalystOrOwner,
    store: Annotated[CaseStore, Depends(get_store)],
    outcomes: Annotated[OutcomeStore, Depends(get_outcomes)],
) -> Any:
    """The case's conditions, soonest due first (one query on ``{case_id}``).

    Role-shaped (ADR-30, one route, two shapes): an analyst reads
    ``Page[ConditionAnalyst]`` (above); the owner of the case reads the plain
    ``Page[ConditionPO]`` of :mod:`fcrm_contracts.api.case_po` (text, owner, due,
    status, ``mine``; nothing about where a condition came from), through the
    PO response guard. Another owner's case is a 404.
    """
    if Role.ANALYST not in principal.roles:
        page = await get_po_followup_service(request).conditions_page(
            principal, case_id
        )
        return guard_response(request, JSONResponse(page.model_dump(mode="json")))
    await _case_or_404(store, case_id)
    rows = await outcomes.list_conditions(case_id)
    items = [to_condition_analyst(row) for row in rows]
    return Page[ConditionAnalyst](items=items, next_cursor=None, total=len(items))


@router.patch("/conditions/{condition_id}")
async def patch_condition(
    condition_id: str,
    body: ConditionPatch,
    principal: Analyst,
    store: Annotated[CaseStore, Depends(get_store)],
    outcomes: Annotated[OutcomeStore, Depends(get_outcomes)],
    writer: Annotated[ActivityWriter, Depends(get_writer)],
) -> ConditionAnalyst:
    """Move a condition's status (reason of at least 20 characters) and/or record
    a note on the evidence the analyst reviewed.

    Raises:
        NotFoundError: No such condition (or its case).
        ForbiddenError: The caller is not the case's analyst.
        fcrm_case.errors.ConditionClosedError: Closing is terminal.
        fcrm_case.errors.ConditionMoveError: Not a move an analyst may make.
    """
    row = await outcomes.get_condition(condition_id)
    if row is None:
        raise NotFoundError(f"no such condition {condition_id}")
    record = await _case_or_404(store, row.case_id)
    _require_own_case(principal, record)
    write = await outcomes.update_condition(
        condition_id, body, by=principal.sub, actor=_actor(principal)
    )
    if write is None:
        raise NotFoundError(f"no such condition {condition_id}")
    for entry in write.entries:
        writer.publish(entry)
    logger.info(
        "condition updated",
        extra={
            "case_id": row.case_id,
            "condition_id": condition_id,
            "status": write.condition.status,
        },
    )
    return to_condition_analyst(write.condition)
