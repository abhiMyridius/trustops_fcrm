"""S1's internal routes: what H1 (and H2's tools through it) call over a
service-account bearer token, never the web client.

``/internal/*`` is service-only at three layers: this router's
``require_roles(Role.SERVICE)`` (fail closed), OPA's ``case-any-internal-all-
service`` rule, and the gateway, which answers 404 to any ``/internal/`` path
from outside (``ops/nginx/default.conf``). The router is left out of the public
OpenAPI document for the same reason.

The request and response models are
:mod:`fcrm_contracts.api.case_internal`. Every write here is **idempotent**: a
harness step may run again after a failure (ADR-28), so the same body twice
leaves the same state (and adds no activity row the second time).

A write that changes what a person sees appends an activity row and publishes it
to the case stream *after* the transaction commits
(:class:`fcrm_case.sse.ActivityWriter`); S1 never decides a transition (H1 does).
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Annotated, Optional

from fastapi import APIRouter, Depends
from fcrm_common.auth import require_roles
from fcrm_common.errors import NotFoundError, RuleViolationError, ValidationError
from fcrm_contracts.api.case_internal import (
    AnalystLoadResponse,
    AssignAnalystRequest,
    AssignResult,
    CaseRecordInternal,
    DocumentInternal,
    DocumentPatch,
    IntakePush,
    IntakePushResult,
    ItemsUpsert,
    ItemsUpsertResult,
    ReviewItemInternal,
    StatePatch,
    StateResult,
    UiActor,
    UiEventIn,
    UiEventResult,
)
from fcrm_contracts.common import Principal
from fcrm_contracts.enums import CaseState, ItemKind, Role

from fcrm_case.deps import (
    get_directory,
    get_loads,
    get_store,
    get_views,
    get_writer,
)
from fcrm_case.directory import AnalystLoadService, IdentityDirectory
from fcrm_case.domain.activity import po_may_receive
from fcrm_case.domain.record import CaseRecord
from fcrm_case.errors import ensure_open
from fcrm_case.shaping import (
    to_case_record_internal,
    to_document_internal,
    to_review_item_internal,
)
from fcrm_case.sse import ActivityWriter
from fcrm_case.store import CaseStore
from fcrm_case.views_store import ViewStore

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/internal",
    tags=["internal"],
    include_in_schema=False,
    dependencies=[Depends(require_roles(Role.SERVICE))],
)

ServiceCaller = Annotated[Principal, Depends(require_roles(Role.SERVICE))]

#: ``case.stage_changed``, ``intake.proposals_ready``: dotted lower-case names.
_EVENT_TYPE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")
_MAX_EVENT_TYPE = 64


def _actor(principal: Principal) -> UiActor:
    """The service account behind a write, as an activity actor (``svc:caseflow``
    becomes component ``caseflow``).
    """
    return UiActor(
        id=principal.sub,
        role=Role.SERVICE.value,
        component=principal.sub.removeprefix("svc:"),
    )


async def _require_case(store: CaseStore, case_id: str) -> None:
    if not await store.exists(case_id):
        raise NotFoundError(f"no such case {case_id}")


async def _get_or_404(store: CaseStore, case_id: str) -> CaseRecord:
    record = await store.get(case_id, with_versions=False)
    if record is None:
        raise NotFoundError(f"no such case {case_id}")
    return record


async def _require_open_case(store: CaseStore, case_id: str) -> CaseRecord:
    """The case, for a write: 404 when missing, 409 ``case_closed`` once closed
    (rule 5: the one guard, :func:`fcrm_case.errors.ensure_open`).
    """
    record = await _get_or_404(store, case_id)
    ensure_open(record)
    return record


@router.get("/cases/{case_id}/record")
async def get_case_record(
    case_id: str,
    store: Annotated[CaseStore, Depends(get_store)],
    views: Annotated[ViewStore, Depends(get_views)],
) -> CaseRecordInternal:
    """The case as H1's steps and H2's ``case_record`` tool read it: identity,
    pins, every field with its origin (open ones included) and the documents.
    One case read, then the field values and documents in parallel.
    """
    record = await _get_or_404(store, case_id)
    rows, documents = await asyncio.gather(
        views.list_field_values(case_id), views.list_documents(case_id)
    )
    return to_case_record_internal(record, rows, documents)


@router.get("/cases/{case_id}/items")
async def list_items(
    case_id: str,
    store: Annotated[CaseStore, Depends(get_store)],
    views: Annotated[ViewStore, Depends(get_views)],
    kind: Optional[ItemKind] = None,
) -> list[ReviewItemInternal]:
    """The case's review items, optionally of one ``kind``."""
    await _require_case(store, case_id)
    return [to_review_item_internal(r) for r in await views.list_items(case_id, kind)]


@router.put("/cases/{case_id}/items")
async def upsert_items(
    case_id: str,
    body: ItemsUpsert,
    store: Annotated[CaseStore, Depends(get_store)],
    views: Annotated[ViewStore, Depends(get_views)],
) -> ItemsUpsertResult:
    """Insert or update review items by ``item_id`` in one round trip. Content
    is rewritten; ``status`` and any decision are kept, so a retried push never
    undoes a human decision.
    """
    await _require_open_case(store, case_id)
    ids = await views.upsert_items(case_id, body.run_id, body.items)
    logger.info(
        "review items upserted",
        extra={"case_id": case_id, "run_id": body.run_id, "items": len(ids)},
    )
    return ItemsUpsertResult(upserted=len(ids), item_ids=ids)


@router.put("/cases/{case_id}/intake")
async def put_intake(
    case_id: str,
    body: IntakePush,
    store: Annotated[CaseStore, Depends(get_store)],
    views: Annotated[ViewStore, Depends(get_views)],
) -> IntakePushResult:
    """Materialise what the intake agents proposed: extracted values (never
    over a value a person already set), gated category and gap items, the
    precedent and the fact flags. The same body twice leaves the same state.
    """
    await _require_open_case(store, case_id)
    applied = await views.apply_intake_push(case_id, body)
    logger.info(
        "intake pushed",
        extra={
            "case_id": case_id,
            "run_id": body.run_id,
            "values_written": applied.values_written,
            "values_kept": applied.values_kept,
        },
    )
    return IntakePushResult(
        case_id=case_id,
        run_id=body.run_id,
        values_written=applied.values_written,
        values_kept=applied.values_kept,
        categories=applied.categories,
        gaps=applied.gaps,
    )


@router.patch("/cases/{case_id}/state")
async def patch_state(
    case_id: str,
    body: StatePatch,
    principal: ServiceCaller,
    store: Annotated[CaseStore, Depends(get_store)],
    writer: Annotated[ActivityWriter, Depends(get_writer)],
) -> StateResult:
    """H1 moves the case. Only the fields the patch sets change. A change of
    stage writes ``case.stage_changed`` (visibility ``all``: the stage names, no
    run id) and publishes it; a replay of the same patch changes nothing. A
    closed case never leaves ``closed``: a patch to any other stage is a 409.
    """
    if body.stage is not CaseState.CLOSED:
        await _require_open_case(store, case_id)
    write = await store.apply_state(case_id, body, _actor(principal))
    if write is None:
        raise NotFoundError(f"no such case {case_id}")
    writer.publish(write.entry)
    return StateResult(
        case_id=case_id,
        stage=write.record.state,
        run_id=write.record.run_id,
        stage_changed=write.entry is not None,
    )


@router.post("/cases/{case_id}/assign")
async def assign_analyst(
    case_id: str,
    body: AssignAnalystRequest,
    principal: ServiceCaller,
    store: Annotated[CaseStore, Depends(get_store)],
    directory: Annotated[IdentityDirectory, Depends(get_directory)],
    loads: Annotated[AnalystLoadService, Depends(get_loads)],
    writer: Annotated[ActivityWriter, Depends(get_writer)],
) -> AssignResult:
    """R5's outcome: set the case's analyst. The name comes from X1's roster, so
    only a real, enabled analyst can be assigned. The ``case.assigned`` row (PO
    visible) carries the analyst's name and nothing else; the load cache is
    dropped so the next triage sees the new count.
    """
    await _require_open_case(store, case_id)
    name = await directory.analyst_name(body.analyst_id)
    if name is None:
        raise ValidationError(f"{body.analyst_id} is not an analyst")
    write = await store.assign_analyst(
        case_id, body.analyst_id, name, _actor(principal)
    )
    if write is None:
        raise NotFoundError(f"no such case {case_id}")
    loads.invalidate()
    writer.publish(write.entry)
    logger.info(
        "analyst assigned",
        extra={
            "case_id": case_id,
            "analyst_id": body.analyst_id,
            "reason": body.reason,
            "changed": write.entry is not None,
        },
    )
    return AssignResult(
        case_id=case_id,
        analyst_id=body.analyst_id,
        analyst_name=name,
        changed=write.entry is not None,
    )


@router.post("/cases/{case_id}/events", status_code=201)
async def post_event(
    case_id: str,
    body: UiEventIn,
    writer: Annotated[ActivityWriter, Depends(get_writer)],
) -> UiEventResult:
    """H1 asks for something to show on the case stream and in the activity
    feed. ``visibility`` decides who sees it. An event that could not safely
    reach the PO (a ``score.*``/``harness.*`` type, a restricted key, text that
    names a score or band) is refused if it claims ``po`` or ``all``: the
    writer's bug is reported, not hidden.
    """
    if not _EVENT_TYPE.match(body.type) or len(body.type) > _MAX_EVENT_TYPE:
        raise ValidationError(
            "event type must be dotted lower-case, e.g. case.assigned"
        )
    if body.visibility != "analyst" and not po_may_receive(
        body.type, body.visibility, body.detail, body.summary
    ):
        raise RuleViolationError(
            "this event type or payload may not be shown to the PO; "
            "write it with visibility 'analyst'",
            rule_id="R3",
        )
    entry = await writer.append(case_id, body)
    if entry is None:
        raise NotFoundError(f"no such case {case_id}")
    return UiEventResult(case_id=case_id, seq=entry.seq)


@router.get("/documents/{doc_id}")
async def get_document(
    doc_id: str, views: Annotated[ViewStore, Depends(get_views)]
) -> DocumentInternal:
    """One uploaded document (status, object keys, page count, Guard 1 result)."""
    row = await views.get_document(doc_id)
    if row is None:
        raise NotFoundError(f"no such document {doc_id}")
    return to_document_internal(row)


@router.patch("/documents/{doc_id}")
async def patch_document(
    doc_id: str,
    body: DocumentPatch,
    store: Annotated[CaseStore, Depends(get_store)],
    views: Annotated[ViewStore, Depends(get_views)],
) -> DocumentInternal:
    """H1 records what H3 produced (``extracted``/``quarantined``/``failed``,
    the canonical and anchored object keys, pages, the page map, Guard 1). A
    document of a closed case is not changed.
    """
    existing = await views.get_document(doc_id)
    if existing is None:
        raise NotFoundError(f"no such document {doc_id}")
    await _require_open_case(store, existing.case_id)
    row = await views.patch_document(doc_id, body)
    if row is None:
        raise NotFoundError(f"no such document {doc_id}")
    return to_document_internal(row)


@router.get("/analysts/load")
async def analyst_load(
    loads: Annotated[AnalystLoadService, Depends(get_loads)],
) -> AnalystLoadResponse:
    """Open cases per analyst, fewest first, for R5 auto-triage: X1's roster plus
    one ``$group`` aggregation, cached for 10 s and dropped on every assignment.
    """
    return await loads.loads()
