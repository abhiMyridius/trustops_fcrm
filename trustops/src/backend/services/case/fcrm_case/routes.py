"""S1's HTTP surface: case CRUD, field values, submission, submission-version
history/diff and a live-update stream.

Role-shaping (rule 3, "the PO never sees a score, band, rating or harness
field"): every route that returns case detail resolves the caller's shape
from their verified :class:`~fcrm_contracts.common.Principal` (never a
query parameter or header the caller could set) and calls the matching
``fcrm_case.shaping`` function. ``CaseDetailPO`` structurally cannot carry
a score (see ``shaping.py``'s module docstring and
``tests/unit/services/case/test_role_shaping.py``), so a bug here could at
worst return the wrong *shape* to the wrong caller -- never leak a
restricted field through the PO shape itself.

``GET /cases/{case_id}/stream`` is the real event stream (``fcrm_case.sse``):
persisted, replayable activity pushed to an in-process hub, filtered by role.
"""

from __future__ import annotations

from typing import Annotated, Optional, Union

from fastapi import APIRouter, Depends, Header, Query, Request
from fcrm_common.auth import require_roles
from fcrm_common.errors import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)
from fcrm_common.events import build_envelope
from fcrm_common.time import utcnow
from fcrm_contracts.api.case_analyst import (
    CaseDetailAnalyst,
    CaseListItemAnalyst,
    SubmissionVersion,
)
from fcrm_contracts.api.case_internal import CaseDetailInternal
from fcrm_contracts.api.case_po import (
    CaseDetailPO,
    CaseListItemPO,
    CreateCaseRequest,
    CreateDraftCaseResponse,
    SubmitCaseRequest,
    SubmitCaseResponse,
    UpdateCaseTitleRequest,
    WriteFieldValueRequest,
    WriteFieldValueResponse,
)
from fcrm_contracts.common import Page, Principal
from fcrm_contracts.enums import CaseState, Role
from fcrm_contracts.events.payloads import CaseCreated, CaseCreatedData, CaseEvent
from fcrm_contracts.tools.submission_diff import SubmissionDiffOutput
from starlette.responses import StreamingResponse

from fcrm_case.deps import get_hub, get_settings, get_store
from fcrm_case.domain.activity import audience_of
from fcrm_case.domain.completion import compute_completion
from fcrm_case.domain.diffing import (
    SubmissionVersionNotFoundError,
    diff_submission_versions,
)
from fcrm_case.domain.fieldset import resolve_required_fields
from fcrm_case.domain.record import (
    CaseRecord,
    FieldValueRecord,
    SubmissionVersionRecord,
)
from fcrm_case.domain.submit_checks import evaluate_submit_checks
from fcrm_case.errors import ensure_open
from fcrm_case.fieldset_provider import active_field_set, active_pinned_versions
from fcrm_case.settings import CaseSettings
from fcrm_case.shaping import (
    completion_state,
    to_case_detail_analyst,
    to_case_detail_internal,
    to_case_detail_po,
    to_case_list_item_analyst,
    to_case_list_item_po,
)
from fcrm_case.sse import EventHub, StreamParams, case_event_stream
from fcrm_case.store import CaseStore

router = APIRouter(tags=["case"])

#: ``GET /cases?limit=`` bounds, the same as S4's ``GET /records``: an
#: unbounded or non-positive limit is a 422 here, never a store error.
DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 200

CaseDetail = Union[CaseDetailPO, CaseDetailAnalyst, CaseDetailInternal]

_CASE_READ_ROLES = (Role.PO, Role.ANALYST, Role.COMMITTEE, Role.SERVICE)
_CASE_REVIEW_ROLES = (Role.ANALYST, Role.COMMITTEE, Role.SERVICE)
#: Who may open a case's event stream: the PO (own cases) and the staff roles.
_STREAM_ROLES = (Role.PO, Role.ANALYST, Role.COMMITTEE, Role.OBSERVER, Role.SERVICE)


def _shape_for(principal: Principal) -> str:
    """Which of po/analyst/internal a caller's verified roles entitle them
    to. Checked from the token, never from anything the caller can set.
    """
    if Role.SERVICE in principal.roles:
        return "internal"
    if Role.ANALYST in principal.roles or Role.COMMITTEE in principal.roles:
        return "analyst"
    if Role.PO in principal.roles:
        return "po"
    raise ForbiddenError("caller's role is not entitled to read a case")


def _detail_for(shape: str, record: CaseRecord) -> CaseDetail:
    if shape == "po":
        return to_case_detail_po(record)
    if shape == "internal":
        return to_case_detail_internal(record)
    return to_case_detail_analyst(record)


def _require_po_owner(principal: Principal, record: CaseRecord) -> None:
    """A PO may only act on a case they themselves own; a mismatch reads as
    404, not 403, so a PO cannot even confirm another case id exists.
    """
    if record.owner != principal.sub:
        raise NotFoundError(f"no such case {record.case_id}")


async def _get_or_404(store: CaseStore, case_id: str) -> CaseRecord:
    record = await store.get(case_id)
    if record is None:
        raise NotFoundError(f"no such case {case_id}")
    return record


@router.post("/cases", status_code=201)
async def create_case(
    body: CreateCaseRequest,
    principal: Annotated[Principal, Depends(require_roles(Role.PO))],
    store: Annotated[CaseStore, Depends(get_store)],
) -> CreateDraftCaseResponse:
    """Create a draft case and resolve its required fields from the active
    field set, pinned to this case for its whole lifetime (CLAUDE.md:
    "each case pins the versions of its scoring model, corpus, controls,
    field set and prompts").
    """
    if body.owner != principal.sub:
        raise ForbiddenError("a PO may only create a case they themselves own")
    field_set = active_field_set()
    required_fields = resolve_required_fields(
        [change_type.value for change_type in body.change_types], field_set
    )
    now = utcnow()
    case_id = await store.next_case_id(now.year)
    record = CaseRecord(
        case_id=case_id,
        title=body.title,
        unit=body.unit,
        owner=body.owner,
        created_by=principal.sub,
        owner_name=principal.name,
        change_types=body.change_types,
        fieldset_v=field_set.version,
        completion_threshold=field_set.completion_threshold,
        pinned_versions=active_pinned_versions(),
        required_fields=required_fields,
        created_at=now,
    )
    event = build_envelope(
        "case.created",
        CaseCreated(
            case_id=case_id,
            actor=principal.sub,
            data=CaseCreatedData(
                title=body.title,
                unit=body.unit,
                change_types=body.change_types,
                pins=record.pinned_versions,
            ),
        ).model_dump(mode="json"),
        case_id=case_id,
    )
    created = await store.create(record, event)
    return CreateDraftCaseResponse(case_id=created.case_id)


@router.get("/cases")
async def list_cases(
    principal: Annotated[Principal, Depends(require_roles(*_CASE_READ_ROLES))],
    store: Annotated[CaseStore, Depends(get_store)],
    state: Optional[CaseState] = None,
    owner: Optional[str] = None,
    analyst_id: Optional[str] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_LIMIT)] = DEFAULT_PAGE_LIMIT,
    cursor: Optional[str] = None,
) -> Union[Page[CaseListItemPO], Page[CaseListItemAnalyst]]:
    """The PO's case list or the analyst/committee queue, role-shaped.

    A PO is always scoped to their own cases (``owner`` is forced to their
    ``sub``, overriding anything passed); an analyst or committee member
    may browse freely (fine-grained per-analyst queue scoping through OPA
    is deferred -- see the task report). A service caller's filters pass
    through unchanged.
    """
    shape = _shape_for(principal)
    if shape == "po":
        page = await store.list(
            owner=principal.sub, state=state, limit=limit, cursor=cursor
        )
        return Page[CaseListItemPO](
            items=[to_case_list_item_po(r) for r in page.items],
            next_cursor=page.next_cursor,
            total=page.total,
        )
    page = await store.list(
        owner=owner,
        analyst_id=analyst_id,
        state=state,
        limit=limit,
        cursor=cursor,
    )
    # Parametrised, not bare ``Page(...)``: FastAPI validates the returned
    # object against the declared Union, and only an exact-class instance
    # is matched to its own member. A bare ``Page`` fell through to the
    # first member (the PO row) and silently dropped ``analyst_id``.
    return Page[CaseListItemAnalyst](
        items=[to_case_list_item_analyst(r) for r in page.items],
        next_cursor=page.next_cursor,
        total=page.total,
    )


@router.get("/cases/{case_id}")
async def get_case(
    case_id: str,
    principal: Annotated[Principal, Depends(require_roles(*_CASE_READ_ROLES))],
    store: Annotated[CaseStore, Depends(get_store)],
) -> CaseDetail:
    """The case, shaped for the caller's role (module docstring)."""
    record = await _get_or_404(store, case_id)
    shape = _shape_for(principal)
    if shape == "po":
        _require_po_owner(principal, record)
    return _detail_for(shape, record)


@router.put("/cases/{case_id}/title")
async def update_title(
    case_id: str,
    body: UpdateCaseTitleRequest,
    principal: Annotated[Principal, Depends(require_roles(Role.PO))],
    store: Annotated[CaseStore, Depends(get_store)],
) -> CaseDetailPO:
    """Rename a case the caller owns."""
    record = await _get_or_404(store, case_id)
    _require_po_owner(principal, record)
    ensure_open(record)
    updated = await store.update_title(case_id, body.title)
    if updated is None:
        raise ConflictError(f"case {case_id} is closed for further writes")
    return to_case_detail_po(updated)


@router.put("/cases/{case_id}/fields/{field_id}")
async def write_field_value(
    case_id: str,
    field_id: str,
    body: WriteFieldValueRequest,
    principal: Annotated[Principal, Depends(require_roles(Role.PO))],
    store: Annotated[CaseStore, Depends(get_store)],
) -> WriteFieldValueResponse:
    """Write one field's value and return the recomputed completion state
    (``fcrm_case.domain.completion.compute_completion``, never
    reimplemented here).

    PO only, and only on a case they own: rule 4 ("the analyst never edits
    the submission", design A §2.1.2 "``field_values`` writes accept only
    ``fcrm_po``") refuses every other role, the service role included --
    agents propose values, the PO confirms or corrects them.
    """
    record = await _get_or_404(store, case_id)
    _require_po_owner(principal, record)
    ensure_open(record)
    required = {f.field_id: f.label for f in record.required_fields}
    if field_id not in required:
        raise ValidationError(f"{field_id} is not a required field for {case_id}")
    field = FieldValueRecord(
        field_id=field_id,
        label=required[field_id],
        value=body.value,
        source=body.source,
        how=body.how,
        filled_at=utcnow(),
    )
    updated = await store.write_field(case_id, field)
    if updated is None:
        raise ConflictError(f"case {case_id} is closed for further writes")
    return WriteFieldValueResponse(
        case_id=case_id, field_id=field_id, completion=completion_state(updated)
    )


@router.post("/cases/{case_id}/submit")
async def submit_case(
    case_id: str,
    body: SubmitCaseRequest,
    principal: Annotated[Principal, Depends(require_roles(Role.PO))],
    store: Annotated[CaseStore, Depends(get_store)],
) -> SubmitCaseResponse:
    """Run the four submit checks; a case that passes gets a new,
    immutable submission-version snapshot and ``submitted_at`` is stamped
    (one-shot: a second submit attempt gets 409, same as any other write
    to an already-submitted case).
    """
    record = await _get_or_404(store, case_id)
    _require_po_owner(principal, record)
    ensure_open(record)
    if not record.open_for_writes:
        raise ConflictError(f"case {case_id} is already submitted or closed")
    completion = compute_completion(record.required_fields, record.filled_field_ids)
    checks = evaluate_submit_checks(
        completion,
        record.completion_threshold,
        # No clarification-thread endpoints exist in this pass, so every
        # case's thread list is empty -- there is never an open gap to
        # report. Once clarifications land, this reads the case's own
        # threads instead of a constant.
        no_open_blocking_gaps=True,
        owner_reviewed_corrections=body.owner_reviewed_corrections,
    )
    next_version = len(record.submission_versions) + 1
    if not checks.all_passed:
        return SubmitCaseResponse(
            case_id=case_id, checks=checks, submission_version=next_version - 1
        )
    now = utcnow()
    snapshot: dict[str, Optional[str]] = {
        f.field_id: (
            record.fields[f.field_id].value if f.field_id in record.fields else None
        )
        for f in record.required_fields
    }
    version = SubmissionVersionRecord(
        version=next_version,
        created_at=now,
        created_by=principal.sub,
        field_values=snapshot,
    )
    event = build_envelope(
        "case.submitted",
        CaseEvent(
            case_id=case_id,
            actor=principal.sub,
            data={"submission_version": next_version},
        ).model_dump(mode="json"),
        case_id=case_id,
    )
    updated = await store.submit(case_id, version, event)
    if updated is None:
        raise ConflictError(f"case {case_id} is already submitted or closed")
    return SubmitCaseResponse(
        case_id=case_id, checks=checks, submission_version=next_version
    )


@router.get("/cases/{case_id}/versions")
async def list_submission_versions(
    case_id: str,
    principal: Annotated[Principal, Depends(require_roles(*_CASE_REVIEW_ROLES))],
    store: Annotated[CaseStore, Depends(get_store)],
) -> list[SubmissionVersion]:
    """Every submission version recorded so far, each diffed against the
    one before it (an arbitrary pair is ``GET .../versions/diff``).
    """
    record = await _get_or_404(store, case_id)
    return to_case_detail_analyst(record).submission_versions


@router.get("/cases/{case_id}/versions/diff")
async def diff_submission_version_pair(
    case_id: str,
    from_version: int,
    to_version: int,
    principal: Annotated[Principal, Depends(require_roles(*_CASE_REVIEW_ROLES))],
    store: Annotated[CaseStore, Depends(get_store)],
) -> SubmissionDiffOutput:
    """What changed between any two of a case's submission versions
    (``fcrm_contracts.tools.submission_diff``, backed by S1).
    """
    record = await _get_or_404(store, case_id)
    try:
        changed = diff_submission_versions(
            record.submission_versions, from_version, to_version
        )
    except SubmissionVersionNotFoundError as exc:
        raise NotFoundError(str(exc)) from exc
    return SubmissionDiffOutput(changed_fields=changed)


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


@router.get("/cases/{case_id}/stream")
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
