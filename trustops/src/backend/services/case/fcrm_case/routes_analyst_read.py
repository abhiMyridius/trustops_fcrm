"""S1's analyst READ routes, in the shapes the web's analyst screens read
(:mod:`fcrm_contracts.api.case_analyst_read`; ADR-30).

Every route here authenticates, asks OPA whether the caller's role may call it
(fail closed) and requires the analyst role: the PO, the committee, the observer
and the service account are refused. They read stored rows only: a score box is
what S2 answered and H1 or the seed stored, withheld until the analyst has opened
the risk view; no route here calls a model, computes a score or writes a state.

Two paths are shared with the PO and are in their own router
(:data:`shared_router`), which carries the PO response guard: the guard lets a
staff caller through and scans what a PO is sent, so the PO shape of
``GET /cases/{id}/activity`` stays fenced while the analyst's is richer.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Final, Literal, Optional

from fastapi import APIRouter, Depends, Query, Request
from fcrm_common.auth import require_roles
from fcrm_common.errors import UpstreamUnavailableError
from fcrm_common.time import utcnow
from fcrm_contracts.api.case_analyst_read import (
    AssessmentSummary,
    DashboardAnalyst,
    HandoffView,
    PolicyView,
    QueueItem,
    RiskView,
    SubmissionDiff,
    SubmissionVersion,
    WorkbenchHeader,
)
from fcrm_contracts.common import Page, Principal
from fcrm_contracts.enums import Role

from fcrm_case.analyst_read_service import AnalystReadService
from fcrm_case.analyst_read_store import AnalystReadStore
from fcrm_case.assessment_store import AssessmentStore
from fcrm_case.deps import authorize_case, get_outcomes, get_store, get_views
from fcrm_case.outcome_store import OutcomeStore
from fcrm_case.routes_internal_assessment import get_assessment
from fcrm_case.store import CaseStore
from fcrm_case.views_store import ViewStore

logger = logging.getLogger(__name__)

#: Queue page bounds (the same shape as every list).
DEFAULT_PAGE_LIMIT: Final = 50
MAX_PAGE_LIMIT: Final = 200

QueueStage = Literal[
    "new",
    "assessing",
    "review",
    "awaiting_business",
    "recommend",
    "committee",
    "closed",
    "fast_lane",
]

router = APIRouter(tags=["analyst"], dependencies=[Depends(authorize_case)])

Analyst = Annotated[Principal, Depends(require_roles(Role.ANALYST))]
PageLimit = Annotated[int, Query(ge=1, le=MAX_PAGE_LIMIT)]


def get_analyst_reads(request: Request) -> AnalystReadStore:
    """The analyst read store ``lifespan.register`` (or a test) put on
    ``app.state``.

    Raises:
        fcrm_common.errors.UpstreamUnavailableError: nothing wired one (a
            deployment fault, answered 503: fail closed).
    """
    store: Optional[AnalystReadStore] = getattr(
        request.app.state, "analyst_reads", None
    )
    if store is None:
        logger.error(
            "case service dependency not wired", extra={"dependency": "analyst_reads"}
        )
        raise UpstreamUnavailableError(
            "The case service is not fully configured (analyst_reads).",
            dependency="analyst_reads",
        )
    return store


def get_clock() -> Callable[[], datetime]:
    """The clock the read views use (``now``); a test overrides this."""
    return utcnow


def get_read_service(
    cases: Annotated[CaseStore, Depends(get_store)],
    reads: Annotated[AnalystReadStore, Depends(get_analyst_reads)],
    assessment: Annotated[AssessmentStore, Depends(get_assessment)],
    views: Annotated[ViewStore, Depends(get_views)],
    outcomes: Annotated[OutcomeStore, Depends(get_outcomes)],
    clock: Annotated[Callable[[], datetime], Depends(get_clock)],
) -> AnalystReadService:
    """One read service per request over the stores the lifespan wired."""
    return AnalystReadService(cases, reads, assessment, views, outcomes, clock)


Service = Annotated[AnalystReadService, Depends(get_read_service)]


@router.get("/queue")
async def queue(
    principal: Analyst,
    service: Service,
    scope: Literal["mine", "all", "unassigned"] = "mine",
    stage: Optional[QueueStage] = None,
    band: Optional[Literal["1", "2", "3", "4"]] = None,
    limit: PageLimit = DEFAULT_PAGE_LIMIT,
    cursor: Optional[str] = None,
) -> Page[QueueItem]:
    """The analyst's queue, the cases that need them first. ``scope`` is the
    analyst's own, everyone's or the unassigned; ``stage`` and ``band`` filter
    (the band of the latest score; a closed case is listed only for
    ``stage=closed``).
    """
    return await service.queue(
        principal,
        scope=scope,
        stage=stage,
        band=int(band) if band is not None else None,
        limit=limit,
        cursor=cursor,
    )


@router.get("/dashboard/analyst")
async def analyst_dashboard(principal: Analyst, service: Service) -> DashboardAnalyst:
    """The analyst dashboard: counts and averages computed from the stored cases
    and scores, the viewer's own open cases and the seed's reference tables.
    """
    return await service.dashboard(principal)


@router.get("/cases/{case_id}/workbench")
async def workbench(
    case_id: str, principal: Analyst, service: Service
) -> WorkbenchHeader:
    """The case summary header: stage, strip, clocks, pinned versions and what
    is owed before the case can be released.
    """
    return await service.workbench(case_id)


@router.get("/cases/{case_id}/assessment")
async def assessment(
    case_id: str, principal: Analyst, service: Service
) -> AssessmentSummary:
    """The Summary tab: what the submission holds, what to notice, the critic's
    findings, the completeness report and the score box. The score is withheld
    until the analyst has opened the risk view.
    """
    return await service.assessment(case_id)


@router.get("/cases/{case_id}/risk")
async def risk(case_id: str, principal: Analyst, service: Service) -> RiskView:
    """The Risk & Controls tab: the score of record by domain with the controls
    behind it, the planned controls (never scored) and the critic's banner.
    Opening it is what starts review: the case's own analyst's first read stores
    the time, once, and the score is shown from then on.
    """
    return await service.risk(principal, case_id)


@router.get("/cases/{case_id}/policy")
async def policy(case_id: str, principal: Analyst, service: Service) -> PolicyView:
    """The Policy tab: the clauses research proposed, each with its stored
    verbatim quote and source, and its decision; the gap research declared.
    """
    return await service.policy(case_id)


@router.get("/cases/{case_id}/handoff")
async def handoff(case_id: str, principal: Analyst, service: Service) -> HandoffView:
    """The Handoff tab: the pack Caseflow built (id, version, hash, status), its
    four release checks, the route and band, and who the committee is.
    """
    return await service.handoff(case_id)


@router.get("/cases/{case_id}/submissions")
async def submissions(
    case_id: str, principal: Analyst, service: Service
) -> Page[SubmissionVersion]:
    """The case's frozen submissions, oldest first: every field with its value,
    origin and source, and what a corrected value replaced.
    """
    return await service.submissions(case_id)


@router.get("/cases/{case_id}/submissions/diff")
async def submission_diff(
    case_id: str,
    principal: Analyst,
    service: Service,
    from_version: Annotated[int, Query(alias="from", ge=1)],
    to_version: Annotated[int, Query(alias="to", ge=1)],
) -> SubmissionDiff:
    """What changed between two submissions: the fields (value and origin before
    and after) and the documents added. 404 when either version does not exist.
    """
    return await service.submission_diff(case_id, from_version, to_version)
