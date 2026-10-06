"""S1's service-only read routes the agent tools call (``fcrm_tools``): the
``submission_diff`` tool's comparison of two submissions and the ``case_search``
tool's search of past cases.

Service-only at the same layers as :mod:`fcrm_case.routes_internal`: this router
requires the service role (fail closed), asks OPA (``/internal/*`` is the service
account's) and sits behind a gateway that answers 404 to any ``/internal/`` path
from outside. Left out of the public OpenAPI document for the same reason. Both
read stored rows only; neither calls a model.
"""

from __future__ import annotations

import logging
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Query
from fcrm_common.auth import require_roles
from fcrm_common.errors import ValidationError
from fcrm_contracts.enums import ChangeType, Role
from fcrm_contracts.tools.case_search import CaseSearchOutput
from fcrm_contracts.tools.submission_diff import SubmissionDiffOutput

from fcrm_case.analyst_read_service import AnalystReadService
from fcrm_case.deps import authorize_case
from fcrm_case.routes_analyst_read import get_read_service

logger = logging.getLogger(__name__)

#: ``top_k`` bound of a search (the tool's own bound).
MAX_TOP_K = 25

router = APIRouter(
    prefix="/internal",
    tags=["internal-read"],
    include_in_schema=False,
    dependencies=[Depends(authorize_case), Depends(require_roles(Role.SERVICE))],
)

Service = Annotated[AnalystReadService, Depends(get_read_service)]


def _change_types(raw: Optional[str]) -> list[str]:
    """The comma-separated change types of a search, each checked.

    Raises:
        fcrm_common.errors.ValidationError: a name is not a change type.
    """
    if not raw:
        return []
    names = [part.strip() for part in raw.split(",") if part.strip()]
    known = {t.value for t in ChangeType}
    unknown = [n for n in names if n not in known]
    if unknown:
        raise ValidationError(
            "Unknown change type.",
            errors=[{"loc": "query.change_types", "msg": f"unknown: {unknown}"}],
        )
    return names


@router.get("/cases/search")
async def search_cases(
    service: Service,
    q: Annotated[str, Query(min_length=1, max_length=1000)],
    change_types: Optional[str] = None,
    top_k: Annotated[int, Query(ge=1, le=MAX_TOP_K)] = 5,
    closed_only: bool = True,
    geography: Annotated[Optional[str], Query(max_length=100)] = None,
) -> CaseSearchOutput:
    """Past cases like ``q`` (the ``case_search`` tool): ranked by word overlap
    with the title and overlap of ``change_types`` (comma separated), closed
    cases only unless ``closed_only`` is false.
    """
    return await service.search(
        query=q,
        change_types=_change_types(change_types),
        top_k=top_k,
        closed_only=closed_only,
        geography=geography,
    )


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
