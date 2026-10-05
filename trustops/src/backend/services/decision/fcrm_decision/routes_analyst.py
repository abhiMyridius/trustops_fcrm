"""The analyst's surface: ask for a case's pack (and, for H1, build it), release
it to the committee, and answer a committee thread.

``POST /packs`` serves two callers with one route, because the web has one
(``decision/packs``): H1's service account **builds** a pack from a snapshot
(idempotent by content hash and by ``Idempotency-Key``; building never
releases), and an analyst, who sends only the case id, is handed the latest
pack. Releasing is a human gate (Guard 7: OPA denies the service account),
answered 409 unless all four checks passed.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from fcrm_common.auth import require_roles
from fcrm_common.errors import ValidationError as FcrmValidationError
from fcrm_contracts.api.decision import (
    CommitteeThread,
    PackBuild,
    PackView,
    ThreadAnswerIn,
)
from fcrm_contracts.common import Principal
from fcrm_contracts.enums import Role

from fcrm_decision.commands_pack import build_pack, release_pack
from fcrm_decision.commands_thread import answer_thread
from fcrm_decision.deps import (
    ANALYST_ROLES,
    BUILD_ROLES,
    DirectoryDep,
    StoreDep,
)
from fcrm_decision.errors import PackNotBuiltError
from fcrm_decision.idempotency import IdempotencyKeyHeader, checked_key, fingerprint
from fcrm_decision.views import pack_view, thread_view

router = APIRouter()

Analyst = Annotated[Principal, Depends(require_roles(*ANALYST_ROLES))]
Builder = Annotated[Principal, Depends(require_roles(*BUILD_ROLES))]


@router.post("/packs", response_model=PackView)
async def build_or_latest_pack(
    body: PackBuild,
    response: Response,
    principal: Builder,
    store: StoreDep,
    idempotency_key: IdempotencyKeyHeader = None,
) -> PackView:
    """H1 (service): freeze ``snapshot`` as the case's next pack, 201, or hand
    back the live pack when the content is the same, 200. Building never
    releases. An analyst: the case's latest pack, 200, or 409 when none has
    been built (it is built when the recommendation is locked).
    """
    if Role.SERVICE in principal.roles:
        if body.snapshot is None:
            raise FcrmValidationError(
                "A pack build needs the snapshot.",
                errors=[{"loc": "body.snapshot", "msg": "required"}],
            )
        row, created = await build_pack(
            store,
            case_id=body.case_id,
            snapshot=body.snapshot,
            key=checked_key(idempotency_key),
            print_=fingerprint("pack.build", body.case_id, body),
        )
        response.status_code = 201 if created else 200
    else:
        if body.snapshot is not None:
            raise FcrmValidationError(
                "An analyst never supplies a pack snapshot.",
                errors=[{"loc": "body.snapshot", "msg": "not allowed"}],
            )
        latest = await store.latest_pack(body.case_id)
        if latest is None:
            raise PackNotBuiltError(
                "No pack has been built for this case; it is built when the "
                "recommendation is locked.",
                case_id=body.case_id,
            )
        row = latest
    return pack_view(row, await store.addenda(row.pack_id))


@router.post("/packs/{pack_id}/release", response_model=PackView)
async def release(
    pack_id: str, _principal: Analyst, store: StoreDep, directory: DirectoryDep
) -> PackView:
    """Release the pack to the committee (the ``pack_release`` gate).

    409 unless all four release checks passed, naming the ones that did not;
    the committee roster comes from X1. Releasing an already released pack is
    a no-op that announces nothing a second time.
    """
    pack, _ = await release_pack(store, directory, pack_id=pack_id)
    return pack_view(pack, await store.addenda(pack_id))


@router.post("/threads/{thread_id}/answer", response_model=CommitteeThread)
async def answer(
    thread_id: str, body: ThreadAnswerIn, principal: Analyst, store: StoreDep
) -> CommitteeThread:
    """Answer a committee question, in the caller's own name (R6).

    Unchanged numbers: the answer is attached as a pack addendum and the pack's
    ``content_hash`` stays exactly as it was. Changed numbers: the contract
    already required an ``unlock_reason`` of at least 20 characters; the pack
    stops taking votes at once and ``committee.answered`` tells H1, which
    drives the supersede, the corrected pack and the re-vote.
    """
    thread = await answer_thread(
        store,
        thread_id=thread_id,
        answerer_id=principal.sub,
        answerer_name=principal.name,
        text=body.text,
        numbers_changed=body.numbers_changed,
        unlock_reason=body.unlock_reason,
    )
    return thread_view(thread)
