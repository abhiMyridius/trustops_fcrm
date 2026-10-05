"""Recording an outcome: what the committee decided, or an analyst's close.

``POST /outcomes`` is H1's formal record of a decision people made; S5 never
makes one. In ``committee`` mode the pack must have a decisive tally and the
outcome offered must be that tally's (a committee outcome is only ever what the
committee voted). In ``delegated`` mode (Band 1-2, R1; Guard 5 is H1's) there
is no pack and the analyst's reason is required; it cannot pre-empt a released
committee pack, nor overwrite an outcome the case already has.

Either way ``outcome.set`` is enqueued in the same transaction as the record.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fcrm_common.errors import NotFoundError
from fcrm_common.time import utcnow
from fcrm_contracts.api.decision import OutcomeRecord, OutcomeSet

from fcrm_decision import events
from fcrm_decision.errors import (
    CaseDecidedError,
    CommitteePendingError,
    NotTalliedError,
    OutcomeAlreadyRecordedError,
    OutcomeMismatchError,
)
from fcrm_decision.idempotency import once
from fcrm_decision.rows import (
    DELEGATED_PACK_PREFIX,
    OutcomeRow,
    RecordedOutcome,
)
from fcrm_decision.store import DecisionStore, DecisionTx

logger = logging.getLogger(__name__)

KIND_OUTCOME = "outcome.record"


def _recorded(record: OutcomeRecord) -> RecordedOutcome:
    return RecordedOutcome(
        decided_by=record.decided_by,
        reason=record.reason,
        plain_summary=record.plain_summary,
        conditions=record.conditions,
        recorded_at=utcnow(),
    )


def _same_record(existing: RecordedOutcome, record: OutcomeRecord) -> bool:
    """Whether ``existing`` already says what ``record`` says."""
    return (
        existing.decided_by == record.decided_by
        and existing.reason == record.reason
        and existing.plain_summary == record.plain_summary
        and existing.conditions == record.conditions
    )


async def _record_committee(tx: DecisionTx, record: OutcomeRecord) -> OutcomeRow:
    pack_id = str(record.pack_id)
    pack = await tx.lock_pack(pack_id)
    if pack is None:
        raise NotFoundError(f"no pack {pack_id}")
    if pack.case_id != record.case_id:
        raise OutcomeMismatchError(
            "That pack belongs to another case.",
            case_id=record.case_id,
            pack_id=pack_id,
        )
    row = await tx.get_outcome(pack_id)
    if row is None or row.outcome.no_majority or row.outcome.outcome is None:
        raise NotTalliedError(
            "This pack has no decisive tally to record.",
            case_id=record.case_id,
            pack_id=pack_id,
        )
    if row.outcome.outcome != record.outcome:
        raise OutcomeMismatchError(
            "A committee outcome is only ever what the committee voted for.",
            case_id=record.case_id,
            pack_id=pack_id,
        )
    if row.recorded is not None:
        if _same_record(row.recorded, record):
            return row
        raise OutcomeAlreadyRecordedError(
            "This outcome was already recorded differently.",
            case_id=record.case_id,
            pack_id=pack_id,
        )
    recorded = _recorded(record)
    await tx.update_outcome(row.row_id, {"recorded": recorded})
    await tx.enqueue(events.outcome_set(record, pack_id=pack_id))
    return row.model_copy(update={"recorded": recorded})


async def _record_delegated(tx: DecisionTx, record: OutcomeRecord) -> OutcomeRow:
    existing = await tx.case_outcome(record.case_id)
    if existing is not None:
        same = (
            existing.mode == "delegated"
            and existing.recorded is not None
            and existing.outcome.outcome == record.outcome
            and _same_record(existing.recorded, record)
        )
        if same:
            return existing
        raise CaseDecidedError(
            "This case already has an outcome.", case_id=record.case_id
        )
    if await tx.open_committee_pack(record.case_id) is not None:
        raise CommitteePendingError(
            "A committee pack is open for this case; a delegated close cannot "
            "pre-empt it.",
            case_id=record.case_id,
        )
    now = utcnow()
    row = OutcomeRow(
        outcome=OutcomeSet(
            case_id=record.case_id,
            pack_id=f"{DELEGATED_PACK_PREFIX}{record.case_id}",
            round=1,
            outcome=record.outcome,
        ),
        mode="delegated",
        decided_at=now,
        recorded=_recorded(record),
    )
    await tx.put_outcome(row)
    await tx.enqueue(events.outcome_set(record, pack_id=None))
    return row


async def record_outcome(
    store: DecisionStore,
    record: OutcomeRecord,
    *,
    key: Optional[str],
    print_: str,
) -> OutcomeRow:
    """Record ``record`` and announce it with ``outcome.set``.

    Recording the same outcome again (same key, or the same content) returns
    the stored row and announces nothing a second time.

    Raises:
        NotFoundError: ``committee`` mode: no such pack.
        NotTalliedError: ``committee`` mode: the pack has no decisive tally.
        OutcomeMismatchError: ``committee`` mode: the outcome is not the tallied
            one, or the pack belongs to another case.
        OutcomeAlreadyRecordedError: ``committee`` mode: recorded differently.
        CaseDecidedError: ``delegated`` mode: the case already has an outcome.
        CommitteePendingError: ``delegated`` mode: a committee pack is open.
    """

    async def work(tx: DecisionTx) -> OutcomeRow:
        async def produce() -> dict[str, Any]:
            row = (
                await _record_committee(tx, record)
                if record.mode == "committee"
                else await _record_delegated(tx, record)
            )
            return {"pack_id": row.outcome.pack_id, "round": row.outcome.round}

        result, _ = await once(
            tx,
            key=key,
            kind=KIND_OUTCOME,
            print_=print_,
            produce=produce,
            case_id=record.case_id,
        )
        row = await tx.get_outcome(str(result["pack_id"]), int(result["round"]))
        if row is None:
            raise NotFoundError(f"no outcome for {result['pack_id']}")
        return row

    row = await store.run(work)
    logger.info(
        "outcome recorded",
        extra={
            "case_id": record.case_id,
            "pack_id": row.outcome.pack_id,
            "mode": record.mode,
        },
    )
    return row
