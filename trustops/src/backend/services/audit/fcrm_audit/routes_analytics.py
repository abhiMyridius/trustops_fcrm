"""S4's assurance routes: ``GET /overrides``, ``GET /versions/{case_id}``,
``GET /agent-quality`` and ``GET /agent-quality/dashboard``.

Who reads what, and why:

* **Overrides and versions** carry case ids, reasons and the versions of a case:
  analysts only (the web's matrix).
* **Agent quality and its dashboard** are about the agents, never about a case, so
  an analyst, a committee member and the observer (ADR-19: the harness's reader)
  may read them. The PO reads none of it: OPA and :func:`require_roles` agree,
  and no route here carries a score, a band or a model id.

The dashboard is the published self-contained HTML, served as ``text/html`` under
a CSP that lets it load and run nothing.
"""

from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from fcrm_common.auth import require_roles
from fcrm_common.errors import NotFoundError, UpstreamUnavailableError
from fcrm_contracts.api.audit_oversight import (
    AgentQualityOut,
    OverridesPage,
    VersionsRowOut,
)
from fcrm_contracts.common import Page, Principal
from fcrm_contracts.enums import Role

from fcrm_audit.analytics import (
    DEFAULT_PAGE_LIMIT,
    MAX_PAGE_LIMIT,
    AnalyticsStore,
    version_rows,
)
from fcrm_audit.quality import DASHBOARD_CSP, EvalsReader, agent_quality
from fcrm_audit.routes import authorize

router = APIRouter(tags=["assurance"], dependencies=[Depends(authorize)])

_ANALYST_ONLY = (Role.ANALYST,)
#: The agents' quality is not case data.
_QUALITY_ROLES = (Role.ANALYST, Role.COMMITTEE, Role.OBSERVER)

#: An ISO week as the eval publisher writes it (``2026-W41``).
WEEK_PATTERN = r"^\d{4}-W(0[1-9]|[1-4]\d|5[0-3])$"


def get_analytics(request: Request) -> AnalyticsStore:
    """The analytics store ``lifespan.py`` (or a test) put on ``app.state``.

    Raises:
        UpstreamUnavailableError: Nothing wired it (a deployment fault, 503).
    """
    store: AnalyticsStore | None = getattr(request.app.state, "analytics", None)
    if store is None:
        raise UpstreamUnavailableError(
            "The audit analytics are not configured.", dependency="analytics"
        )
    return store


def get_evals(request: Request) -> EvalsReader:
    """The eval reader, wired only when S4 has its read-only credential.

    Raises:
        UpstreamUnavailableError: S4 was started without
            ``MONGODB_URI_EVALS_READER`` (a deployment fault, 503).
    """
    reader: EvalsReader | None = getattr(request.app.state, "evals", None)
    if reader is None:
        raise UpstreamUnavailableError(
            "The agent quality is not available: the audit service has no "
            "read-only credential for the eval results.",
            dependency="fcrm_evals",
        )
    return reader


@router.get("/overrides")
async def list_overrides(
    _principal: Annotated[Principal, Depends(require_roles(*_ANALYST_ONLY))],
    store: Annotated[AnalyticsStore, Depends(get_analytics)],
    agent: Annotated[Optional[str], Query(max_length=40)] = None,
    cursor: Optional[str] = None,
    limit: int = Query(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
) -> OverridesPage:
    """The analyst overrides, newest first, with how often each agent's
    proposals were replaced (``overridden`` of ``proposals``). ``agent`` narrows
    both.
    """
    return await store.overrides(agent=agent, cursor=cursor, limit=limit)


@router.get("/versions/{case_id}")
async def case_versions(
    case_id: str,
    _principal: Annotated[Principal, Depends(require_roles(*_ANALYST_ONLY))],
    store: Annotated[AnalyticsStore, Depends(get_analytics)],
) -> Page[VersionsRowOut]:
    """The versions ``case_id`` was pinned to when its run opened, beside what a
    case opened now would pin. A case with no pins in its chain has no rows.
    """
    pinned = await store.pinned(case_id)
    rows = version_rows(pinned, await store.current()) if pinned else []
    return Page[VersionsRowOut](items=rows, next_cursor=None, total=len(rows))


@router.get(
    "/agent-quality",
    responses={404: {"description": "No eval run was published for the week."}},
)
async def get_agent_quality(
    _principal: Annotated[Principal, Depends(require_roles(*_QUALITY_ROLES))],
    reader: Annotated[EvalsReader, Depends(get_evals)],
    week: Annotated[Optional[str], Query(pattern=WEEK_PATTERN)] = None,
) -> AgentQualityOut:
    """Each agent's primary eval metric in the week's latest run (the newest
    week when none is asked for). 404 when nothing was published: the web's
    empty state.

    Raises:
        NotFoundError: No run for the week.
    """
    quality = await agent_quality(reader, week or None)
    if quality is None:
        raise NotFoundError("No agent evaluation has been published for that week.")
    return quality


@router.get(
    "/agent-quality/dashboard",
    response_class=HTMLResponse,
    responses={404: {"description": "No dashboard was published yet."}},
)
async def agent_quality_dashboard(
    _principal: Annotated[Principal, Depends(require_roles(*_QUALITY_ROLES))],
    reader: Annotated[EvalsReader, Depends(get_evals)],
) -> HTMLResponse:
    """The latest published dashboard, self-contained, as ``text/html`` under a
    strict CSP (no network, no script, no frame, no form).

    Raises:
        NotFoundError: Nothing was published.
    """
    html = await reader.dashboard()
    if html is None:
        raise NotFoundError("No agent dashboard has been published yet.")
    return HTMLResponse(
        html,
        headers={
            "Content-Security-Policy": DASHBOARD_CSP,
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Cache-Control": "no-store",
        },
    )
