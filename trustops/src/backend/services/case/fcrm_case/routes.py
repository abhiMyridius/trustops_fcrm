"""S1's live-update stream: ``GET /cases/{case_id}/stream``.

The product owner's routes are :mod:`fcrm_case.routes_po` (in the shapes the web
reads), H1's are :mod:`fcrm_case.routes_internal`; what is left here is the one
route every role shares. The stream is the real event stream
(:mod:`fcrm_case.sse`): persisted, replayable activity pushed to an in-process
hub, filtered by role. The gateway turns ``?access_token=`` into the bearer
header (``ops/nginx/default.conf``), so the route authenticates like any other.

A PO must own the case and sees ``po``/``all`` rows only (``fcrm_case.domain.
activity``: no score, harness or audit event, and no payload with a restricted
key, even when a row was written with the wrong visibility).
"""

from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Header, Query, Request
from fcrm_common.auth import require_roles
from fcrm_common.errors import NotFoundError, ValidationError
from fcrm_contracts.common import Principal
from fcrm_contracts.enums import Role
from starlette.responses import StreamingResponse

from fcrm_case.deps import authorize_case, get_hub, get_settings, get_store
from fcrm_case.domain.activity import audience_of
from fcrm_case.domain.record import CaseRecord
from fcrm_case.settings import CaseSettings
from fcrm_case.sse import EventHub, StreamParams, case_event_stream
from fcrm_case.store import CaseStore

router = APIRouter(tags=["case"])

#: Who may open a case's event stream: the PO (own cases) and the staff roles.
_STREAM_ROLES = (Role.PO, Role.ANALYST, Role.COMMITTEE, Role.OBSERVER, Role.SERVICE)


def _require_po_owner(principal: Principal, record: CaseRecord) -> None:
    """A PO may only follow a case they themselves own; a mismatch (or a
    discarded draft) reads as 404, so a PO cannot even confirm another case id
    exists.
    """
    if record.owner != principal.sub or record.discarded_at is not None:
        raise NotFoundError(f"no such case {record.case_id}")


def _resume_point(header: Optional[str], query: Optional[str]) -> Optional[int]:
    """The ``seq`` the client last saw: the ``Last-Event-ID`` header (what a
    browser sends when it reconnects on its own) or ``?last_event_id=`` (what
    the web client sets when it reopens). The header wins.

    Raises:
        ValidationError: The value is not a non-negative integer.
    """
    raw = header if header else query
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValidationError("Last-Event-ID must be a stream sequence number") from exc
    if value < 0:
        raise ValidationError("Last-Event-ID must not be negative")
    return value


@router.get("/cases/{case_id}/stream", dependencies=[Depends(authorize_case)])
async def stream_case(
    case_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(require_roles(*_STREAM_ROLES))],
    store: Annotated[CaseStore, Depends(get_store)],
    hub: Annotated[EventHub, Depends(get_hub)],
    settings: Annotated[CaseSettings, Depends(get_settings)],
    last_event_id: Annotated[Optional[str], Header()] = None,
    last_event_id_query: Annotated[Optional[str], Query(alias="last_event_id")] = None,
) -> StreamingResponse:
    """The case's live event stream (``fcrm_case.sse``): unnamed frames
    ``id: <seq>`` / ``data: {type, caseId, seq, at, payload}``.

    ``Last-Event-ID`` (or ``?last_event_id=``) replays everything after that
    ``seq`` from the activity log; without one the stream starts at the case's
    current head. A PO must own the case (another PO's case is a 404) and is
    shown ``po``/``all`` entries only, never score, harness or audit events;
    analysts, committee members, observers and the service account are shown
    the analyst feed too.
    """
    record = await store.get(case_id, with_versions=False)
    if record is None:
        raise NotFoundError(f"no such case {case_id}")
    audience = audience_of(principal)
    if audience == "po":
        _require_po_owner(principal, record)
    resume = _resume_point(last_event_id, last_event_id_query)
    head = record.activity_seq
    params = StreamParams(
        case_id=case_id,
        audience=audience,
        # A client cannot be ahead of the case (a re-seeded database): clamp.
        start_seq=head if resume is None else min(resume, head),
        heartbeat_s=settings.sse_heartbeat_s,
        max_lifetime_s=settings.sse_max_lifetime_s,
    )
    return StreamingResponse(
        case_event_stream(request, hub, store.activity, params),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
