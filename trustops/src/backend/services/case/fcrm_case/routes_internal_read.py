"""S1's service-only routes for the diff tool and the harness monitor.

The ``submission_diff`` tool's comparison of two submissions, and the snapshot of
its run that Caseflow pushes for the harness monitor. (``GET /internal/cases/search``,
the ``case_search`` tool's route, is :mod:`fcrm_case.routes_internal_reflags`'s.)

Service-only at the same layers as :mod:`fcrm_case.routes_internal`: this router
requires the service role (fail closed), asks OPA (``/internal/*`` is the service
account's) and sits behind a gateway that answers 404 to any ``/internal/`` path
from outside. Left out of the public OpenAPI document for the same reason. They
read and store rows only; none calls a model.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fcrm_common.auth import require_roles
from fcrm_contracts.api.case_analyst_read import (
    HarnessPush,
    HarnessPushResult,
    HarnessSnapshot,
)
from fcrm_contracts.enums import Role
from fcrm_contracts.tools.submission_diff import SubmissionDiffOutput

from fcrm_case.analyst_read_service import AnalystReadService
from fcrm_case.deps import authorize_case
from fcrm_case.routes_analyst_read import get_read_service

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/internal",
    tags=["internal-read"],
    include_in_schema=False,
    dependencies=[Depends(authorize_case), Depends(require_roles(Role.SERVICE))],
)

Service = Annotated[AnalystReadService, Depends(get_read_service)]


@router.get("/cases/{case_id}/submissions/diff")
async def submission_diff(
    case_id: str,
    service: Service,
    from_version: Annotated[int, Query(ge=1)],
    to_version: Annotated[int, Query(ge=1)],
) -> SubmissionDiffOutput:
    """The fields whose value differs between two submissions (the
    ``submission_diff`` tool): what a changed-fields re-run re-proposes.
    """
    return await service.tool_diff(case_id, from_version, to_version)


@router.put("/cases/{case_id}/harness")
async def put_harness(
    case_id: str, body: HarnessPush, service: Service
) -> HarnessPushResult:
    """Caseflow's snapshot of its run after a step: stored as the case's latest
    (a replace, so a repeat changes nothing). Analyst, committee and observer read
    it through ``GET /cases/{id}/harness``; the owner never does.
    """
    return await service.put_harness(case_id, body)


@router.get("/cases/{case_id}/harness")
async def get_harness(case_id: str, service: Service) -> HarnessSnapshot:
    """The snapshot as stored (404 before the first push)."""
    return await service.harness(case_id)
