"""The stored shapes behind a case's outcome and the pure steps around them: no
I/O, so the Mongo store and its in-memory twin build the same rows, events and
fingerprints.

``outcomes`` (``_id = case_id``) holds one row per case with two lifecycles in
one document: a delegated close that was *requested* (the analyst's reason and
summary, waiting for H1 and S5) and an outcome that was *set* (what S5
recorded, which closes the case). A request is replaced by the outcome that
answers it.

Nothing here carries a score, a band or a vote.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Final, Literal, Optional

from fcrm_common.events import build_envelope
from fcrm_contracts.api.case_internal import StatePatch, UiActor, UiEventIn
from fcrm_contracts.api.case_internal_outcome import (
    ActivatedCondition,
    OutcomePut,
    outcome_label,
)
from fcrm_contracts.enums import CaseState, Outcome
from fcrm_contracts.events.envelope import Envelope
from fcrm_contracts.events.payloads import CaseCloseRequested, CaseEvent
from pydantic import BaseModel, ConfigDict

from fcrm_case.domain.record import CaseRecord
from fcrm_case.domain.stage import stage_changed_event
from fcrm_case.domain.views import ConditionRow

#: A condition row's ``source.kind`` when an outcome activated it (seeded rows
#: carry the seed's own kinds).
PACK_CONDITION_SOURCE: Final = "pack"


class CloseRequestRow(BaseModel):
    """An analyst's delegated close, waiting for H1 (R1)."""

    model_config = ConfigDict(extra="forbid")

    outcome: Outcome
    reason: str
    plain_summary: str
    requested_by: str
    requested_at: datetime
    band: int


class OutcomeRow(BaseModel):
    """``outcomes``: a case's close request and/or its outcome."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    status: Literal["requested", "set"]
    request: Optional[CloseRequestRow] = None
    outcome: Optional[Outcome] = None
    mode: Optional[Literal["committee", "delegated"]] = None
    decided_by: Optional[str] = None
    decided_at: Optional[datetime] = None
    pack_id: Optional[str] = None
    reason: Optional[str] = None
    plain_summary: Optional[str] = None
    condition_ids: list[str] = []
    fingerprint: Optional[str] = None


def canonical_json(value: Any) -> str:
    """``value`` as JSON with sorted keys and no spaces: the same content gives
    the same text on every run.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def body_fingerprint(kind: str, case_id: str, body: BaseModel) -> str:
    """sha256 of the request's identity: its kind, the case it targets and its
    canonical body. Two requests with the same fingerprint say the same thing.
    """
    payload = {"kind": kind, "case_id": case_id, "body": body.model_dump(mode="json")}
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def condition_row(
    case_id: str, body: OutcomePut, condition: ActivatedCondition, now: datetime
) -> ConditionRow:
    """The ``conditions`` row an activated condition becomes."""
    return ConditionRow(
        condition_id=condition.condition_id,
        case_id=case_id,
        text=condition.text,
        owner_id=condition.owner_id,
        owner_label=condition.owner_label,
        due_label=condition.due_label,
        due_date=condition.due_date.isoformat() if condition.due_date else None,
        status=condition.status,
        source={"kind": PACK_CONDITION_SOURCE, "label": condition.origin},
        ref=condition.ref,
        pack_id=body.pack_id,
        activated_at=now,
    )


def outcome_row(
    case_id: str, body: OutcomePut, fingerprint: str, now: datetime
) -> OutcomeRow:
    """The row that records ``body`` as the case's outcome."""
    return OutcomeRow(
        case_id=case_id,
        status="set",
        outcome=body.outcome,
        mode=body.mode,
        decided_by=body.decided_by,
        decided_at=now,
        pack_id=body.pack_id,
        reason=body.reason,
        plain_summary=body.plain_summary,
        condition_ids=[c.condition_id for c in body.conditions],
        fingerprint=fingerprint,
    )


def closing_changes(case_id: str, body: OutcomePut, now: datetime) -> dict[str, Any]:
    """The ``$set`` that closes the case: the state, when it closed and was
    decided, the plain summary the PO reads and ``outcome_ref``, the
    ``outcomes`` row's id (which is the case id). The recommendation stays
    locked: closing does not unlock anything.
    """
    changes: dict[str, Any] = {
        "state": CaseState.CLOSED,
        "closed_at": now,
        "decided_at": now,
        "outcome_ref": case_id,
    }
    if body.plain_summary is not None:
        changes["outcome_summary"] = body.plain_summary
    return changes


def stage_closed_event(before: CaseState, actor: UiActor) -> Optional[UiEventIn]:
    """The ``case.stage_changed`` entry for the move to ``closed`` (visible to
    the PO: the two stage names only), or ``None`` when the case was already
    closed.
    """
    if before is CaseState.CLOSED:
        return None
    return stage_changed_event(before, StatePatch(stage=CaseState.CLOSED), actor)


def case_closed_envelope(record: CaseRecord, body: OutcomePut) -> Envelope:
    """``case.closed``: the case is closed under ``body``'s outcome (for the
    oversight side; H1 records its own ``case.closed`` audit row).
    """
    return build_envelope(
        "case.closed",
        CaseEvent(
            case_id=record.case_id,
            actor=body.decided_by,
            data={
                "outcome": body.outcome.value,
                "mode": body.mode,
                "label": outcome_label(body.outcome),
                "conditions": len(body.conditions),
            },
        ).model_dump(mode="json"),
        case_id=record.case_id,
        run_id=record.run_id,
    )


def condition_activated_envelope(
    record: CaseRecord, condition: ConditionRow
) -> Envelope:
    """``condition.activated``: a condition went live (oversight's reminder
    clock starts from it).
    """
    return build_envelope(
        "condition.activated",
        CaseEvent(
            case_id=record.case_id,
            actor="system",
            data={
                "condition_id": condition.condition_id,
                "ref": condition.ref,
                "due_date": condition.due_date,
                "status": condition.status,
            },
        ).model_dump(mode="json"),
        case_id=record.case_id,
        run_id=record.run_id,
    )


def close_requested_envelope(record: CaseRecord, request: CloseRequestRow) -> Envelope:
    """``case.close_requested``: H1 checks Guard 5 and asks S5 to record the
    outcome.
    """
    return build_envelope(
        "case.close_requested",
        CaseCloseRequested(
            case_id=record.case_id,
            actor=request.requested_by,
            outcome=request.outcome,
            reason=request.reason,
            plain_summary=request.plain_summary,
            band=request.band,
        ).model_dump(mode="json"),
        case_id=record.case_id,
        run_id=record.run_id,
    )
