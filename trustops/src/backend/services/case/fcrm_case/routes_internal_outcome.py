"""S1's internal outcome route, called by H1 once S5 has recorded a decision:
``PUT /internal/cases/{case_id}/outcome``.

It records the outcome, activates its conditions and closes the case in one
transaction (:mod:`fcrm_case.outcome_store`), and is **idempotent** two ways: the
body is the whole intended state (the same body again changes nothing), and an
``Idempotency-Key`` binds a key to one body (a different body under the same key
is a 409). Like every ``/internal`` route it is service-only at three layers
(this router, OPA's ``case-any-internal-all-service`` rule and the gateway) and
left out of the public OpenAPI document.

The stream entries the write staged are published *after* the commit, so a
watcher never sees a close that was rolled back.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from fcrm_common.auth import require_roles
from fcrm_common.errors import NotFoundError
from fcrm_contracts.api.case_internal import UiActor
from fcrm_contracts.api.case_internal_outcome import OutcomePut, OutcomePutResult
from fcrm_contracts.common import Principal
from fcrm_contracts.enums import Role

from fcrm_case.deps import get_outcomes, get_writer
from fcrm_case.idempotency import IdempotencyKeyHeader, checked_key
from fcrm_case.outcome_store import OutcomeStore
from fcrm_case.sse import ActivityWriter

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/internal",
    tags=["internal"],
    include_in_schema=False,
    dependencies=[Depends(require_roles(Role.SERVICE))],
)

ServiceCaller = Annotated[Principal, Depends(require_roles(Role.SERVICE))]


def _actor(principal: Principal) -> UiActor:
    """The service account behind a write, as an activity actor."""
    return UiActor(
        id=principal.sub,
        role=Role.SERVICE.value,
        component=principal.sub.removeprefix("svc:"),
    )


@router.put("/cases/{case_id}/outcome")
async def put_outcome(
    case_id: str,
    body: OutcomePut,
    principal: ServiceCaller,
    outcomes: Annotated[OutcomeStore, Depends(get_outcomes)],
    writer: Annotated[ActivityWriter, Depends(get_writer)],
    idempotency_key: IdempotencyKeyHeader = None,
) -> OutcomePutResult:
    """Record the outcome S5 decided, activate its conditions and close the case.

    Raises:
        NotFoundError: No such case.
        fcrm_case.errors.OutcomeConflictError: The case already has a different
            outcome (409 ``outcome_already_set``).
        fcrm_case.errors.IdempotencyKeyReusedError: The key belongs to another
            request (409 ``idempotency_key_reused``).
    """
    write = await outcomes.put_outcome(
        case_id,
        body,
        key=checked_key(idempotency_key),
        actor=_actor(principal),
    )
    if write is None:
        raise NotFoundError(f"no such case {case_id}")
    for entry in write.entries:
        writer.publish(entry)
    logger.info(
        "outcome recorded",
        extra={
            "case_id": case_id,
            "mode": body.mode,
            "outcome": body.outcome.value,
            "conditions": write.result.conditions_activated,
            "replayed": write.result.replayed,
        },
    )
    return write.result
