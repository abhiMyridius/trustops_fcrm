"""The service account's surface: H1's steps after an unlock, a split or a
decision. Every ``POST`` here honours ``Idempotency-Key`` (ADR-28: a step may
run twice), and every one is a record of something people decided; S5 makes no
decision of its own.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from fcrm_common.auth import require_roles
from fcrm_contracts.api.decision import (
    CaseOutcome,
    OutcomeRecord,
    RevoteRequest,
    RevoteResult,
    SupersedeRequest,
    SupersedeResult,
)
from fcrm_contracts.common import Principal

from fcrm_decision.commands_outcome import KIND_OUTCOME, record_outcome
from fcrm_decision.commands_pack import (
    KIND_REVOTE,
    KIND_SUPERSEDE,
    confirm_revote,
    supersede_pack,
)
from fcrm_decision.deps import SERVICE_ROLES, StoreDep
from fcrm_decision.idempotency import IdempotencyKeyHeader, checked_key, fingerprint
from fcrm_decision.views import case_outcome_view

router = APIRouter()

Service = Annotated[Principal, Depends(require_roles(*SERVICE_ROLES))]


@router.post("/packs/{pack_id}/supersede", response_model=SupersedeResult)
async def supersede(
    pack_id: str,
    body: SupersedeRequest,
    _principal: Service,
    store: StoreDep,
    idempotency_key: IdempotencyKeyHeader = None,
) -> SupersedeResult:
    """Supersede the pack after the analyst unlocked it (R6): it takes no more
    votes, its cast votes are voided (kept, never deleted) and re-requested for
    the next version, and ``pack.superseded`` (plus ``vote.rerequested``) goes
    out. 409 once the pack has an outcome.
    """
    return await supersede_pack(
        store,
        pack_id=pack_id,
        reason=body.reason,
        key=checked_key(idempotency_key),
        print_=fingerprint(KIND_SUPERSEDE, pack_id, body),
    )


@router.post("/packs/{pack_id}/revote", response_model=RevoteResult)
async def revote(
    pack_id: str,
    body: RevoteRequest,
    _principal: Service,
    store: StoreDep,
    idempotency_key: IdempotencyKeyHeader = None,
) -> RevoteResult:
    """Confirm the re-vote round after a 1-1-1 split (ADR-08, ADR-31). S5 opened
    the round when it tallied the split; this reports which round it is and who
    is asked to vote again, and writes nothing. 409 unless the previous round
    ended with no majority.
    """
    return await confirm_revote(
        store,
        pack_id=pack_id,
        key=checked_key(idempotency_key),
        print_=fingerprint(KIND_REVOTE, pack_id, body),
    )


@router.post("/outcomes", response_model=CaseOutcome)
async def outcomes(
    body: OutcomeRecord,
    _principal: Service,
    store: StoreDep,
    idempotency_key: IdempotencyKeyHeader = None,
) -> CaseOutcome:
    """Record an outcome people decided and announce it (``outcome.set``).

    ``committee``: the pack's decisive tally, exactly. ``delegated``: an
    analyst's Band 1-2 close with a reason (R1); never over an open committee
    pack, never over an existing outcome.
    """
    row = await record_outcome(
        store,
        body,
        key=checked_key(idempotency_key),
        print_=fingerprint(KIND_OUTCOME, body.case_id, body),
    )
    return case_outcome_view(row)
