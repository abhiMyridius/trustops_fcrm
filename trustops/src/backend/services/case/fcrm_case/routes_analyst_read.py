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
    ActivityAnalyst,
    AssessmentSummary,
    ClarificationsView,
    DashboardAnalyst,
    HandoffView,
    HarnessSnapshot,
    OutcomeAnalyst,
    PolicyView,
    QueueItem,
    RiskView,
    SubmissionDiff,
    SubmissionVersion,
    WorkbenchHeader,
)
from fcrm_contracts.api.case_po import ActivityEventPO, OutcomePO
from fcrm_contracts.common import Page, Principal
from fcrm_contracts.enums import Role

from fcrm_case.analyst_read_service import AnalystReadService
from fcrm_case.analyst_read_store import AnalystReadStore
from fcrm_case.analyst_write_store import AnalystWriteStore
from fcrm_case.deps import (
    authorize_case,
    get_outcomes,
    get_po_followup_service,
    get_po_service,
    get_store,
    get_views,
)
from fcrm_case.domain.activity import audience_of
from fcrm_case.po_followup_service import PoFollowupService
from fcrm_case.po_guard import PoGuardedRoute
from fcrm_case.po_service import PoService
from fcrm_case.routes_internal_assessment import get_assessment

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
    "handoff",
    "committee",
    "closed",
    "fast_lane",
]

router = APIRouter(tags=["analyst"], dependencies=[Depends(authorize_case)])
#: The two paths the web reads as an owner and as staff. One handler each, under
#: the PO response guard (it lets staff through and scans what an owner is sent).
shared_router = APIRouter(
    tags=["analyst"],
    route_class=PoGuardedRoute,
    dependencies=[Depends(authorize_case)],
)

Analyst = Annotated[Principal, Depends(require_roles(Role.ANALYST))]
Reader = Annotated[Principal, Depends(require_roles(Role.PO, Role.ANALYST))]
#: The committee reads a case's submissions too (the Submission tab of its pack).
SubmissionReader = Annotated[
    Principal, Depends(require_roles(Role.ANALYST, Role.COMMITTEE))
]
#: Who may see the harness monitor (ADR-19): never the owner.
MonitorReader = Annotated[
    Principal,
    Depends(require_roles(Role.ANALYST, Role.COMMITTEE, Role.OBSERVER)),
]
OutcomeReader = Annotated[
    Principal, Depends(require_roles(Role.PO, Role.ANALYST, Role.COMMITTEE))
]
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


def get_analyst_store(request: Request) -> AnalystWriteStore:
    """The analyst's ledger store ``lifespan.register`` (or a test) put on
    ``app.state`` (read here for the gates decided, the planned controls and the
    gaps recorded).

    Raises:
        fcrm_common.errors.UpstreamUnavailableError: nothing wired one (a
            deployment fault, answered 503: fail closed).
    """
    store: Optional[AnalystWriteStore] = getattr(
        request.app.state, "analyst_store", None
    )
    if store is None:
        logger.error(
            "case service dependency not wired", extra={"dependency": "analyst_store"}
        )
        raise UpstreamUnavailableError(
            "The case service is not fully configured (analyst_store).",
            dependency="analyst_store",
        )
    return store


def read_service(
    request: Request, clock: Callable[[], datetime] = utcnow
) -> AnalystReadService:
    """One read service over the stores the lifespan wired onto ``request``'s app.

    Raises:
        fcrm_common.errors.UpstreamUnavailableError: a store is not wired (a
            deployment fault, answered 503).
    """
    return AnalystReadService(
        get_store(request),
        get_analyst_reads(request),
        get_assessment(request),
        get_views(request),
        get_outcomes(request),
        get_analyst_store(request),
        clock,
        # Optional on purpose: only the Summary of a case in handoff reads it, so a
        # deployment that has not wired it still answers every other read.
        handoffs=getattr(request.app.state, "handoffs", None),
    )


def get_read_service(
    request: Request,
    clock: Annotated[Callable[[], datetime], Depends(get_clock)],
) -> AnalystReadService:
    """The dependency form of :func:`read_service`."""
    return read_service(request, clock)


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


@router.get("/cases/{case_id}/clarifications")
async def clarifications(
    case_id: str, principal: Analyst, service: Service
) -> ClarificationsView:
    """The Inbox tab: the questions drafted for the owner, the round sent, each
    question with the owner's answer, and whether the analyst's clock is paused on
    the owner.
    """
    return await service.clarifications(case_id)


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
    case_id: str, principal: SubmissionReader, service: Service
) -> Page[SubmissionVersion]:
    """The case's frozen submissions, oldest first: every field with its value,
    origin and source, and what a corrected value replaced.
    """
    return await service.submissions(case_id)


@router.get("/cases/{case_id}/submissions/diff")
async def submission_diff(
    case_id: str,
    principal: SubmissionReader,
    service: Service,
    from_version: Annotated[int, Query(alias="from", ge=1)],
    to_version: Annotated[int, Query(alias="to", ge=1)],
) -> SubmissionDiff:
    """What changed between two submissions: the fields (value and origin before
    and after) and the documents added. 404 when either version does not exist.
    """
    return await service.submission_diff(case_id, from_version, to_version)


@shared_router.get("/cases/{case_id}/activity")
async def activity(
    case_id: str,
    request: Request,
    principal: Reader,
    clock: Annotated[Callable[[], datetime], Depends(get_clock)],
    limit: PageLimit = DEFAULT_PAGE_LIMIT,
    cursor: Optional[str] = None,
) -> Page[ActivityEventPO] | Page[ActivityAnalyst]:
    """The case's activity, newest first. An owner reads their own case's feed in
    plain words (``PoService.activity``: only what the owner may see, no actor,
    no audit record); an analyst reads every row with who did it, the audit
    record it cites and its gate. Each caller's service is built only on its own
    branch, so an owner's read never needs the analyst's stores.
    """
    if audience_of(principal) == "po":
        owner_service: PoService = get_po_service(request)
        return await owner_service.activity(
            principal, case_id, limit=limit, cursor=cursor
        )
    return await read_service(request, clock).activity(
        case_id, limit=limit, cursor=cursor
    )


@shared_router.get("/cases/{case_id}/outcome")
async def outcome(
    case_id: str,
    request: Request,
    principal: OutcomeReader,
    clock: Annotated[Callable[[], datetime], Depends(get_clock)],
) -> OutcomePO | OutcomeAnalyst:
    """The recorded outcome. An owner reads their own case's plain one
    (``PoFollowupService.outcome``: the label, the summary and the conditions in
    words, no score, band, vote or rationale); the analyst and the committee read
    how it was decided (committee vote or delegated close), by whom, why, and the
    conditions it activated. Each caller's service is built only on its own branch.
    """
    if audience_of(principal) == "po":
        owner_service: PoFollowupService = get_po_followup_service(request)
        return await owner_service.outcome(principal, case_id)
    return await read_service(request, clock).outcome(case_id)


@router.get("/cases/{case_id}/harness")
async def harness(
    case_id: str, principal: MonitorReader, service: Service
) -> HarnessSnapshot:
    """The harness monitor: the case's run as Caseflow last pushed it (the agents
    run against the 120k budget, the guards, the log, the chain check). For the
    analyst, the committee and the observer; the owner never receives it (ADR-19).
    404 until a run has pushed a snapshot.
    """
    return await service.harness(case_id)
