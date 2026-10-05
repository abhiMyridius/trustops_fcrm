"""S1's outcome, close-request and condition store.

Same split as :mod:`fcrm_case.store`: a small :class:`OutcomeStore` ``Protocol``
the routes depend on, a Mongo implementation the lifespan wires and an
in-memory twin for unit tests. The collections (design A §2.1.1):

=====================  ======================================================
``outcomes``           ``_id = case_id``; a requested delegated close and/or
                       the outcome that was set (:mod:`fcrm_case.domain
                       .outcome`)
``conditions``         the conditions an approval activated; ``{case_id}`` and
                       ``{due_date, status}`` indexes
``idempotency_keys``   ``_id = Idempotency-Key``; the request's fingerprint and
                       result; TTL 24 h
=====================  ======================================================

**Atomic.** Every write is one Mongo transaction: ``PUT outcome`` commits the
outcome row, the conditions, the case's move to ``closed`` (with ``closed_at``
and ``outcome_ref``), the activity row, the outbox events and the idempotency
key together or not at all, so a reader never sees a closed case without its
conditions, and an event is never published for a write that did not happen.
Stream publication happens after the commit, by the route, from the entries a
write returns.

**Idempotent.** The body is the whole intended state. The same body again (or
the same ``Idempotency-Key`` with the same body) writes nothing and answers
``replayed``; a different body for a case that has an outcome is a 409, and the
same key with a different body is a 409.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final, Optional, Protocol

from fcrm_common.errors import ConflictError
from fcrm_common.events import build_envelope
from fcrm_common.mongo import Repository, to_bson, with_transaction
from fcrm_common.outbox import Outbox
from fcrm_common.time import utcnow
from fcrm_contracts.api.case_analyst_close import (
    ANALYST_CONDITION_MOVES,
    ConditionPatch,
)
from fcrm_contracts.api.case_internal import UiActor, UiEventIn
from fcrm_contracts.api.case_internal_outcome import (
    OutcomePut,
    OutcomePutResult,
    outcome_label,
)
from fcrm_contracts.events.envelope import Envelope
from fcrm_contracts.events.payloads import CaseEvent
from pymongo import ASCENDING, AsyncMongoClient, IndexModel
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.collection import AsyncCollection

from fcrm_case.activity_log import ActivityLog, MongoActivityLog
from fcrm_case.domain.activity import ActivityEntry
from fcrm_case.domain.outcome import (
    CloseRequestRow,
    OutcomeRow,
    body_fingerprint,
    case_closed_envelope,
    close_requested_envelope,
    closing_changes,
    condition_activated_envelope,
    condition_row,
    outcome_row,
    stage_closed_event,
)
from fcrm_case.domain.record import CaseRecord
from fcrm_case.domain.views import ConditionRow
from fcrm_case.errors import (
    CloseAlreadyRequestedError,
    ConditionClosedError,
    ConditionMoveError,
    IdempotencyKeyReusedError,
    OutcomeConflictError,
)

logger = logging.getLogger(__name__)

KIND_OUTCOME_PUT: Final = "outcome.put"
#: How long an ``Idempotency-Key`` is remembered (design A §2: 24 h).
KEY_TTL: Final = timedelta(hours=24)
#: A case has a handful of conditions; this bound only stops a bug reading more.
MAX_CONDITIONS_PER_CASE: Final = 200


@dataclass(frozen=True)
class OutcomeWrite:
    """What ``PUT outcome`` did: the result for the caller and the activity
    entries to publish to the stream once the transaction has committed.
    """

    result: OutcomePutResult
    entries: tuple[ActivityEntry, ...] = ()


@dataclass(frozen=True)
class CloseWrite:
    """What a close request did: whether it replayed an earlier identical one
    (nothing written) and the entries to publish after the commit.
    """

    replayed: bool
    entries: tuple[ActivityEntry, ...] = ()


@dataclass(frozen=True)
class ConditionWrite:
    """The condition after an analyst's update and the entries to publish."""

    condition: ConditionRow
    entries: tuple[ActivityEntry, ...] = ()


class OutcomeStore(Protocol):
    """What the outcome, close and condition routes need."""

    async def ensure_indexes(self) -> None:
        """Create every index the queries below rely on (idempotent)."""
        ...

    async def put_outcome(
        self,
        case_id: str,
        body: OutcomePut,
        *,
        key: Optional[str],
        actor: UiActor,
    ) -> Optional[OutcomeWrite]:
        """Record ``body`` as the case's outcome, activate its conditions and
        close the case, in one transaction; ``None`` when no such case.

        Raises:
            fcrm_case.errors.OutcomeConflictError: The case already has a
                different outcome.
            fcrm_case.errors.IdempotencyKeyReusedError: ``key`` was used for
                another request.
        """
        ...

    async def request_close(
        self, case_id: str, request: CloseRequestRow, *, actor: UiActor
    ) -> Optional[CloseWrite]:
        """Record a delegated close request and enqueue ``case.close_requested``
        atomically; ``None`` when no such case.

        Raises:
            fcrm_case.errors.CloseAlreadyRequestedError: A different request is
                already waiting.
            fcrm_case.errors.OutcomeConflictError: The case already has an
                outcome.
        """
        ...

    async def get_outcome(self, case_id: str) -> Optional[OutcomeRow]:
        """The case's outcome row, or ``None``."""
        ...

    async def latest_band(self, case_id: str) -> Optional[int]:
        """The band (1-4) of the case's latest score snapshot, or ``None``
        before the first score.
        """
        ...

    async def list_conditions(self, case_id: str) -> list[ConditionRow]:
        """The case's conditions, soonest due first (at most
        :data:`MAX_CONDITIONS_PER_CASE`).
        """
        ...

    async def get_condition(self, condition_id: str) -> Optional[ConditionRow]:
        """One condition, or ``None``."""
        ...

    async def update_condition(
        self, condition_id: str, patch: ConditionPatch, *, by: str, actor: UiActor
    ) -> Optional[ConditionWrite]:
        """Apply an analyst's ``patch`` and write its activity row and event in
        one transaction; ``None`` when no such condition.

        Raises:
            fcrm_case.errors.ConditionClosedError: The condition is closed.
            fcrm_case.errors.ConditionMoveError: The status change is not one an
                analyst may make from the current status.
            fcrm_common.errors.ConflictError: The condition changed meanwhile.
        """
        ...


def plan_condition_update(
    row: ConditionRow, patch: ConditionPatch, by: str, now: datetime
) -> dict[str, Any]:
    """The ``$set`` for ``patch`` on ``row``, or a refusal.

    Raises:
        ConditionClosedError: ``row`` is closed (terminal).
        ConditionMoveError: ``patch.status`` is not a move an analyst may make
            from ``row.status``.
    """
    if row.status == "closed":
        raise ConditionClosedError(
            f"condition {row.condition_id} is closed",
            case_id=row.case_id,
            condition_id=row.condition_id,
        )
    changes: dict[str, Any] = {"updated_at": now, "updated_by": by}
    if patch.note is not None:
        changes["note"] = patch.note
    if patch.status is not None and patch.status != row.status:
        if patch.status not in ANALYST_CONDITION_MOVES[row.status]:
            raise ConditionMoveError(
                f"a condition cannot move from {row.status} to {patch.status}",
                case_id=row.case_id,
                condition_id=row.condition_id,
            )
        changes["status"] = patch.status
    return changes


def condition_event(
    row: ConditionRow, new_status: str, patch: ConditionPatch, actor: UiActor
) -> UiEventIn:
    """The analyst-visible activity entry for an update (never shown to the PO)."""
    label = row.ref or row.condition_id
    if new_status != row.status:
        summary = f"Condition {label} {row.status} → {new_status}"
        if patch.reason:
            summary = f"{summary}: {patch.reason}"
    else:
        summary = f"Condition {label}: evidence note recorded"
    return UiEventIn(
        type="condition.updated",
        visibility="analyst",
        summary=summary,
        detail={"condition_id": row.condition_id, "status": new_status},
        actor=actor,
    )


def condition_update_envelope(
    row: ConditionRow, new_status: str, by: str, now: datetime
) -> Envelope:
    """``condition.updated`` (or ``condition.completed`` once closed)."""
    event_type = (
        "condition.completed" if new_status == "closed" else "condition.updated"
    )
    return build_envelope(
        event_type,
        CaseEvent(
            case_id=row.case_id,
            actor=by,
            data={
                "condition_id": row.condition_id,
                "ref": row.ref,
                "status": new_status,
                "at": now.isoformat(),
            },
        ).model_dump(mode="json"),
        case_id=row.case_id,
    )


class InMemoryOutcomeStore:
    """Used by unit tests: plain dicts and no transactions, with the case read
    and written through two callables so it needs nothing of the case store but
    the case record.

    Args:
        activity: The case store's activity log (allocates the ``seq``).
        load_case: ``case_id`` -> the stored case, or ``None``.
        save_case: Stores the changed case.
    """

    def __init__(
        self,
        activity: ActivityLog,
        load_case: Callable[[str], Optional[CaseRecord]],
        save_case: Callable[[CaseRecord], None],
    ) -> None:
        self._activity = activity
        self._load = load_case
        self._save = save_case
        self.outcomes: dict[str, OutcomeRow] = {}
        self.conditions: dict[str, ConditionRow] = {}
        self.keys: dict[str, tuple[str, str, OutcomePutResult]] = {}
        self.bands: dict[str, int] = {}
        self.events: list[Envelope] = []

    async def ensure_indexes(self) -> None:
        """Nothing to create."""

    async def _entry(self, case_id: str, event: UiEventIn) -> ActivityEntry:
        entry = await self._activity.append(case_id, event)
        if entry is None:
            raise ConflictError(f"no such case {case_id}")
        return entry

    async def put_outcome(
        self,
        case_id: str,
        body: OutcomePut,
        *,
        key: Optional[str],
        actor: UiActor,
    ) -> Optional[OutcomeWrite]:
        record = self._load(case_id)
        if record is None:
            return None
        fingerprint = body_fingerprint(KIND_OUTCOME_PUT, case_id, body)
        if key is not None and key in self.keys:
            kind, known_print, result = self.keys[key]
            if kind != KIND_OUTCOME_PUT or known_print != fingerprint:
                raise IdempotencyKeyReusedError(
                    "This Idempotency-Key was already used for a different request."
                )
            return OutcomeWrite(result.model_copy(update={"replayed": True}))
        existing = self.outcomes.get(case_id)
        if existing is not None and existing.status == "set":
            if existing.fingerprint != fingerprint:
                raise OutcomeConflictError(
                    "This case already has a different outcome.", case_id=case_id
                )
            return OutcomeWrite(_result(case_id, body, replayed=True))
        now = utcnow()
        for condition in body.conditions:
            self.conditions[condition.condition_id] = condition_row(
                case_id, body, condition, now
            )
        self.outcomes[case_id] = outcome_row(case_id, body, fingerprint, now)
        entries: list[ActivityEntry] = []
        stage_event = stage_closed_event(record.state, actor)
        self._save(record.model_copy(update=closing_changes(case_id, body, now)))
        if stage_event is not None:
            entries.append(await self._entry(case_id, stage_event))
        self.events.append(case_closed_envelope(record, body))
        for condition in body.conditions:
            self.events.append(
                condition_activated_envelope(
                    record, self.conditions[condition.condition_id]
                )
            )
        result = _result(case_id, body, replayed=False)
        if key is not None:
            self.keys[key] = (KIND_OUTCOME_PUT, fingerprint, result)
        return OutcomeWrite(result, tuple(entries))

    async def request_close(
        self, case_id: str, request: CloseRequestRow, *, actor: UiActor
    ) -> Optional[CloseWrite]:
        record = self._load(case_id)
        if record is None:
            return None
        existing = self.outcomes.get(case_id)
        if existing is not None:
            if existing.status == "set":
                raise OutcomeConflictError(
                    "This case already has an outcome.", case_id=case_id
                )
            if existing.request is not None and _same_request(
                existing.request, request
            ):
                return CloseWrite(replayed=True)
            raise CloseAlreadyRequestedError(
                "A different delegated close is already waiting for H1.",
                case_id=case_id,
            )
        self.outcomes[case_id] = OutcomeRow(
            case_id=case_id, status="requested", request=request
        )
        self.events.append(close_requested_envelope(record, request))
        entry = await self._entry(case_id, _close_requested_event(request, actor))
        return CloseWrite(replayed=False, entries=(entry,))

    async def get_outcome(self, case_id: str) -> Optional[OutcomeRow]:
        return self.outcomes.get(case_id)

    async def latest_band(self, case_id: str) -> Optional[int]:
        return self.bands.get(case_id)

    async def list_conditions(self, case_id: str) -> list[ConditionRow]:
        rows = [c for c in self.conditions.values() if c.case_id == case_id]
        rows.sort(key=lambda c: (c.due_date or "", c.condition_id))
        return rows[:MAX_CONDITIONS_PER_CASE]

    async def get_condition(self, condition_id: str) -> Optional[ConditionRow]:
        return self.conditions.get(condition_id)

    async def update_condition(
        self, condition_id: str, patch: ConditionPatch, *, by: str, actor: UiActor
    ) -> Optional[ConditionWrite]:
        row = self.conditions.get(condition_id)
        if row is None:
            return None
        now = utcnow()
        changes = plan_condition_update(row, patch, by, now)
        updated = row.model_copy(update=changes)
        self.conditions[condition_id] = updated
        entry = await self._entry(
            row.case_id, condition_event(row, updated.status, patch, actor)
        )
        self.events.append(condition_update_envelope(row, updated.status, by, now))
        return ConditionWrite(updated, (entry,))


def _same_request(existing: CloseRequestRow, request: CloseRequestRow) -> bool:
    """Whether ``existing`` already says what ``request`` says (who asked and
    when do not matter for a replay).
    """
    return (
        existing.outcome == request.outcome
        and existing.reason == request.reason
        and existing.plain_summary == request.plain_summary
    )


def _close_requested_event(request: CloseRequestRow, actor: UiActor) -> UiEventIn:
    """The analyst-visible entry for a close request (never shown to the PO)."""
    return UiEventIn(
        type="case.close_requested",
        visibility="analyst",
        summary=f"Delegated close requested: {outcome_label(request.outcome)}",
        detail={"outcome": request.outcome.value},
        actor=actor,
    )


class MongoOutcomeStore:
    """The real store over ``fcrm_case``'s ``outcomes``, ``conditions`` and
    ``idempotency_keys``, writing through the case, activity and outbox
    collections in the same transaction.

    Args:
        client: The Mongo client (transactions need the ``rs0`` replica set).
        db: The ``fcrm_case`` database.
        cases: The ``cases`` repository.
        conditions: The ``conditions`` repository.
        outbox: The case service's outbox.
        activity: The activity log (its ``stage`` inserts a row in a
            transaction).
    """

    def __init__(
        self,
        client: AsyncMongoClient[Any],
        db: Any,
        cases: Repository[CaseRecord],
        conditions: Repository[ConditionRow],
        outbox: Outbox,
        activity: MongoActivityLog,
    ) -> None:
        self._client = client
        self._cases = cases
        self._conditions = conditions
        self._outbox = outbox
        self._activity = activity
        self._outcomes: AsyncCollection[Any] = db["outcomes"]
        self._keys: AsyncCollection[Any] = db["idempotency_keys"]
        self._snapshots: AsyncCollection[Any] = db["score_snapshots"]

    async def ensure_indexes(self) -> None:
        """``conditions`` ``{case_id}`` and ``{due_date, status}`` (the tracker
        and the reminder scan), and a 24 h TTL on ``idempotency_keys.at``.
        """
        await self._conditions.ensure_indexes(
            [
                IndexModel([("case_id", ASCENDING)], name="case_idx"),
                IndexModel(
                    [("due_date", ASCENDING), ("status", ASCENDING)], name="due_status"
                ),
            ]
        )
        await self._keys.create_indexes(
            [
                IndexModel(
                    [("at", ASCENDING)],
                    expireAfterSeconds=int(KEY_TTL.total_seconds()),
                    name="at_ttl",
                )
            ]
        )

    # ── helpers inside one transaction ───────────────────────────────────

    async def _load_case(
        self, case_id: str, session: AsyncClientSession
    ) -> Optional[CaseRecord]:
        return await self._cases.find_one({"case_id": case_id}, session=session)

    async def _stage(
        self,
        case_id: str,
        events: list[UiEventIn],
        session: AsyncClientSession,
    ) -> tuple[ActivityEntry, ...]:
        """Allocate ``len(events)`` consecutive seqs on the case and insert the
        rows (the same ``$inc``-then-``stage`` the activity log does for one).
        """
        if not events:
            return ()
        bumped = await self._cases.find_one_and_update(
            {"case_id": case_id},
            {"$inc": {"activity_seq": len(events)}},
            session=session,
        )
        if bumped is None:
            raise ConflictError(f"no such case {case_id}")
        first = bumped.activity_seq - len(events) + 1
        return tuple(
            [
                await self._activity.stage(session, case_id, first + offset, event)
                for offset, event in enumerate(events)
            ]
        )

    async def _known_key(
        self,
        key: Optional[str],
        fingerprint: str,
        session: AsyncClientSession,
    ) -> Optional[OutcomePutResult]:
        if key is None:
            return None
        known = await self._keys.find_one({"_id": key}, session=session)
        if known is None:
            return None
        if known["kind"] != KIND_OUTCOME_PUT or known["fingerprint"] != fingerprint:
            raise IdempotencyKeyReusedError(
                "This Idempotency-Key was already used for a different request."
            )
        stored = OutcomePutResult.model_validate(known["result"])
        return stored.model_copy(update={"replayed": True})

    async def put_outcome(
        self,
        case_id: str,
        body: OutcomePut,
        *,
        key: Optional[str],
        actor: UiActor,
    ) -> Optional[OutcomeWrite]:
        fingerprint = body_fingerprint(KIND_OUTCOME_PUT, case_id, body)

        async def _do(session: AsyncClientSession) -> Optional[OutcomeWrite]:
            record = await self._load_case(case_id, session)
            if record is None:
                return None
            replay = await self._known_key(key, fingerprint, session)
            if replay is not None:
                return OutcomeWrite(replay)
            existing = await self._outcomes.find_one({"_id": case_id}, session=session)
            if existing is not None and existing.get("status") == "set":
                if existing.get("fingerprint") != fingerprint:
                    raise OutcomeConflictError(
                        "This case already has a different outcome.", case_id=case_id
                    )
                return OutcomeWrite(_result(case_id, body, replayed=True))
            return await self._write_outcome(
                record, body, fingerprint, key, actor, session
            )

        return await with_transaction(self._client, _do)

    async def _write_outcome(
        self,
        record: CaseRecord,
        body: OutcomePut,
        fingerprint: str,
        key: Optional[str],
        actor: UiActor,
        session: AsyncClientSession,
    ) -> OutcomeWrite:
        case_id = record.case_id
        now = utcnow()
        rows = [condition_row(case_id, body, c, now) for c in body.conditions]
        await self._conditions.insert_many(rows, session=session)
        row = outcome_row(case_id, body, fingerprint, now)
        await self._outcomes.replace_one(
            {"_id": case_id},
            {**to_bson(row.model_dump(mode="python")), "_id": case_id},
            upsert=True,
            session=session,
        )
        stage_event = stage_closed_event(record.state, actor)
        closed = await self._cases.find_one_and_update(
            {"case_id": case_id},
            {"$set": closing_changes(case_id, body, now)},
            session=session,
        )
        if closed is None:
            raise ConflictError(f"no such case {case_id}")
        entries = await self._stage(
            case_id, [stage_event] if stage_event is not None else [], session
        )
        await self._outbox.enqueue(session, case_closed_envelope(record, body))
        for condition in rows:
            await self._outbox.enqueue(
                session, condition_activated_envelope(record, condition)
            )
        result = _result(case_id, body, replayed=False)
        if key is not None:
            await self._keys.insert_one(
                {
                    "_id": key,
                    "kind": KIND_OUTCOME_PUT,
                    "case_id": case_id,
                    "fingerprint": fingerprint,
                    "result": result.model_dump(mode="json"),
                    "at": now,
                },
                session=session,
            )
        return OutcomeWrite(result, entries)

    async def request_close(
        self, case_id: str, request: CloseRequestRow, *, actor: UiActor
    ) -> Optional[CloseWrite]:
        async def _do(session: AsyncClientSession) -> Optional[CloseWrite]:
            record = await self._load_case(case_id, session)
            if record is None:
                return None
            existing = await self._outcomes.find_one({"_id": case_id}, session=session)
            if existing is not None:
                row = OutcomeRow.model_validate(
                    {k: v for k, v in existing.items() if k != "_id"}
                )
                if row.status == "set":
                    raise OutcomeConflictError(
                        "This case already has an outcome.", case_id=case_id
                    )
                if row.request is not None and _same_request(row.request, request):
                    return CloseWrite(replayed=True)
                raise CloseAlreadyRequestedError(
                    "A different delegated close is already waiting for H1.",
                    case_id=case_id,
                )
            pending = OutcomeRow(case_id=case_id, status="requested", request=request)
            await self._outcomes.insert_one(
                {**to_bson(pending.model_dump(mode="python")), "_id": case_id},
                session=session,
            )
            entries = await self._stage(
                case_id, [_close_requested_event(request, actor)], session
            )
            await self._outbox.enqueue(
                session, close_requested_envelope(record, request)
            )
            return CloseWrite(replayed=False, entries=entries)

        return await with_transaction(self._client, _do)

    async def get_outcome(self, case_id: str) -> Optional[OutcomeRow]:
        doc = await self._outcomes.find_one({"_id": case_id})
        if doc is None:
            return None
        return OutcomeRow.model_validate({k: v for k, v in doc.items() if k != "_id"})

    async def latest_band(self, case_id: str) -> Optional[int]:
        doc = await self._snapshots.find_one(
            {"case_id": case_id},
            projection={"band": 1},
            sort=[("computed_at", -1)],
        )
        return int(doc["band"]) if doc is not None else None

    async def list_conditions(self, case_id: str) -> list[ConditionRow]:
        return await self._conditions.find(
            {"case_id": case_id},
            sort=[("due_date", ASCENDING), ("_id", ASCENDING)],
            limit=MAX_CONDITIONS_PER_CASE,
        )

    async def get_condition(self, condition_id: str) -> Optional[ConditionRow]:
        return await self._conditions.find_one({"condition_id": condition_id})

    async def update_condition(
        self, condition_id: str, patch: ConditionPatch, *, by: str, actor: UiActor
    ) -> Optional[ConditionWrite]:
        async def _do(session: AsyncClientSession) -> Optional[ConditionWrite]:
            row = await self._conditions.find_one(
                {"condition_id": condition_id}, session=session
            )
            if row is None:
                return None
            now = utcnow()
            changes = plan_condition_update(row, patch, by, now)
            updated = await self._conditions.find_one_and_update(
                {"condition_id": condition_id, "status": row.status},
                changes,
                session=session,
            )
            if updated is None:
                raise ConflictError(
                    f"condition {condition_id} changed while it was being updated",
                    condition_id=condition_id,
                )
            entries = await self._stage(
                row.case_id,
                [condition_event(row, updated.status, patch, actor)],
                session,
            )
            await self._outbox.enqueue(
                session, condition_update_envelope(row, updated.status, by, now)
            )
            return ConditionWrite(updated, entries)

        return await with_transaction(self._client, _do)


def _result(case_id: str, body: OutcomePut, *, replayed: bool) -> OutcomePutResult:
    return OutcomePutResult(
        case_id=case_id,
        outcome=body.outcome,
        mode=body.mode,
        closed=True,
        conditions_activated=len(body.conditions),
        replayed=replayed,
    )
