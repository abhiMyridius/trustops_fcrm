"""Mongo persistence for one case run.

``case_runs`` (snapshot) and ``run_events`` (append-only), matching design
A §3.5's schema exactly:

- ``case_runs``: ``_id = run_id``, ``case_id, state, prev_state, version
  (event count), pins{...}, flags{...}, trace_id, root_span_id, opened_at,
  updated_at, closed_at``.
- ``run_events``: ``{run_id, seq, at, kind, state_from, state_to,
  message_id, data}``; unique ``{run_id, seq}``. ``eventstore.rebuild``
  folds these back into a state and must equal the snapshot (already
  proven for direct ``(trigger, context)`` pairs by the 19 pure tests;
  :func:`CaseRunStore.load_recorded_transitions` is what lets the same
  fold run against what is actually in Mongo).

Both collections are written in the same Mongo transaction as the outbox
row the caller enqueues (:mod:`fcrm_caseflow.engine`) — this module only
holds the document shapes and the read/write primitives; it never opens a
transaction itself.

``RunSnapshot``/``RunFlags`` (:mod:`fcrm_caseflow.states`) stay the
*minimal* projection the pure eventstore needs; the extra runtime-only
fields design A §3.5 lists (``pins``, ``trace_id``, ``root_span_id``,
timestamps, ``prev_state``) live only in the Mongo document this module
builds, not in those pure models — keeping the pure package's replay
contract exactly as small as the existing 19 tests already prove it to be.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Optional

from fcrm_common.mongo import Document
from fcrm_common.time import utcnow
from fcrm_contracts.enums import CaseState
from fcrm_contracts.versions import PinnedVersions
from pymongo import ASCENDING, IndexModel, ReturnDocument
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.collection import AsyncCollection

from fcrm_caseflow.eventstore import RecordedTransition
from fcrm_caseflow.states import RunFlags, RunSnapshot
from fcrm_caseflow.transitions import TransitionContext
from fcrm_caseflow.triggers import Trigger

CASE_RUNS_COLLECTION: Final = "case_runs"
RUN_EVENTS_COLLECTION: Final = "run_events"

#: Placeholder pins until a real version-registry lane exists to resolve
#: "active versions" at ``case.created`` time (design A §3.1's "open run
#: ... pins from active versions"). Values match CLAUDE.md's seed package
#: P7 (controls v9, corpus v14, field set v6, routing v4); ``scoring_v``
#: and ``prompts_v`` are not pinned down anywhere this lane could read
#: from, so they are conservative placeholders, clearly named as such —
#: a real version-registry integration should replace this constant
#: wholesale, not patch around it.
DEFAULT_PINS: Final = PinnedVersions(
    scoring_v=3,
    corpus_v=14,
    controls_v=9,
    fieldset_v=6,
    jurisdictions_v="2026-01",
    routing_v=4,
    prompts_v="v1",
)


def pins_from_case_event_data(data: dict[str, Any]) -> PinnedVersions:
    """``case.created``'s payload is ``fcrm_contracts.events.payloads
    .CaseEvent``, whose free-form ``data`` dict may carry a ``pins`` key
    once a real version-registry lane exists; absent that, fall back to
    :data:`DEFAULT_PINS`.
    """
    raw = data.get("pins")
    if isinstance(raw, dict):
        return PinnedVersions.model_validate(raw)
    return DEFAULT_PINS


@dataclass(frozen=True)
class CaseRun:
    """A ``case_runs`` document, typed for this package's own use.

    ``snapshot`` is exactly what :mod:`fcrm_caseflow.eventstore` and the
    pure tests already reason about; everything else is the runtime-only
    context design A §3.5 adds around it.
    """

    snapshot: RunSnapshot
    pins: PinnedVersions
    prev_state: Optional[CaseState]
    trace_id: Optional[str]
    root_span_id: Optional[str]
    opened_at: Any
    updated_at: Any
    pending_agents: tuple[str, ...] = ()
    """Agents dispatched by a two-agent step (research + assessment, run
    in parallel) that have not yet reported ``agent.done`` — tracked here,
    not in :class:`~fcrm_caseflow.states.RunFlags`, because it is pure
    runtime bookkeeping for the join in :mod:`fcrm_caseflow.engine`, not
    one of states.py's own documented sub-state flags.
    """
    dispatched_in_state: tuple[str, ...] = ()
    """``Trigger`` wire values of every *internal* dispatch trigger
    (``internal.*`` — see :mod:`fcrm_caseflow.triggers`'s module
    docstring) already auto-fired since the run last moved into its
    current state. Reset to empty on every transition that actually
    changes state (``to_state != from_state``); never reset, and never
    re-checked against, on a same-state self-loop caused by anything
    *other* than an internal trigger itself (e.g. ``AGENT_DONE_
    NARRATIVE_COMPLETENESS``'s ``COMPLETENESS -> COMPLETENESS`` row).

    This is what stops :mod:`fcrm_caseflow.engine`'s internal-dispatch
    chain from re-firing an unconditional self-loop dispatch trigger
    (``SCREENING_DISPATCH``, ``COMPLETENESS_DISPATCH``, ...) every time a
    *later*, unrelated event happens to leave the run in the same state —
    a real bug caught writing this package's own engine tests (see the
    package report): excluding only the single trigger most recently
    applied is not enough, because the exclusion does not survive past
    the one ``engine.handle()`` call it was computed in, while a
    same-state self-loop can legitimately be driven by a *separate*,
    later wire event.
    """
    band: Optional[int] = None
    """The case's latest known band (1-4), captured opportunistically from
    whichever event payload last carried one — see
    :mod:`fcrm_caseflow.resolve`'s module docstring on why this layer
    cannot compute it itself (no S2 scoring integration here). Persisted
    across events so a later trigger whose guard needs ``ctx.band``
    (``CASE_CLOSE_REQUESTED``'s R1/R2 split) still has it even when the
    triggering event itself did not carry one.
    """

    @property
    def state(self) -> CaseState:
        return CaseState(self.snapshot.state)


def new_case_run_document(
    *, run_id: str, case_id: str, pins: PinnedVersions, trace_id: Optional[str] = None
) -> Document:
    """The document ``case.created`` writes: state ``INTAKE``
    (:func:`fcrm_caseflow.transitions.open_run`), version 0, no prior
    state.
    """
    now = utcnow()
    return {
        "_id": run_id,
        "case_id": case_id,
        "state": CaseState.INTAKE.value,
        "prev_state": None,
        "version": 0,
        "pins": pins.model_dump(mode="json"),
        "flags": RunFlags().model_dump(mode="json"),
        "trace_id": trace_id,
        "root_span_id": None,
        "opened_at": now,
        "updated_at": now,
        "closed_at": None,
        "pending_agents": [],
        "dispatched_in_state": [],
        "band": None,
    }


def case_run_from_document(doc: Document) -> CaseRun:
    """Validate a stored ``case_runs`` document back into :class:`CaseRun`.

    ``closed_at`` is stored as a BSON datetime (like every other timestamp
    on this document) but ``RunSnapshot.closed_at`` is ``Optional[str]``
    (the pure package's own minimal replay projection — states.py); a raw
    ``datetime`` read back from Mongo fails that model's validation unless
    converted here, exactly once, at the Mongo <-> pure-model boundary.
    """
    closed_at = doc.get("closed_at")
    if isinstance(closed_at, datetime):
        closed_at = closed_at.isoformat()
    snapshot = RunSnapshot(
        run_id=doc["_id"],
        case_id=doc["case_id"],
        state=doc["state"],
        flags=RunFlags.model_validate(doc["flags"]),
        version=doc["version"],
        closed_at=closed_at,
    )
    return CaseRun(
        snapshot=snapshot,
        pins=PinnedVersions.model_validate(doc["pins"]),
        prev_state=CaseState(doc["prev_state"]) if doc.get("prev_state") else None,
        trace_id=doc.get("trace_id"),
        root_span_id=doc.get("root_span_id"),
        opened_at=doc.get("opened_at"),
        updated_at=doc.get("updated_at"),
        pending_agents=tuple(doc.get("pending_agents") or ()),
        dispatched_in_state=tuple(doc.get("dispatched_in_state") or ()),
        band=doc.get("band"),
    )


def run_event_document(
    *,
    run_id: str,
    seq: int,
    kind: str,
    trigger: Trigger,
    state_from: CaseState,
    state_to: CaseState,
    message_id: Optional[str],
    context: TransitionContext,
    action: str,
) -> Document:
    """One ``run_events`` row. ``data`` carries exactly what
    :class:`~fcrm_caseflow.eventstore.RecordedTransition` needs to replay
    this row (``trigger`` + the context fields) plus ``action`` for
    debugging — ``eventstore.rebuild`` only reads ``trigger``/``context``
    back out (:func:`recorded_transition_from_document`), so adding
    ``action`` here never risks the replay contract.
    """
    return {
        "run_id": run_id,
        "seq": seq,
        "at": utcnow(),
        "kind": kind,
        "state_from": state_from.value,
        "state_to": state_to.value,
        "message_id": message_id,
        "action": action,
        "data": {
            "trigger": trigger.value,
            "context": context.__dict__,
        },
    }


def recorded_transition_from_document(doc: Document) -> RecordedTransition:
    """The inverse of :func:`run_event_document`'s ``data``: a
    :class:`~fcrm_caseflow.eventstore.RecordedTransition` ``eventstore
    .rebuild`` can fold, straight off a stored ``run_events`` row.
    """
    data = doc["data"]
    return RecordedTransition(
        trigger=Trigger(data["trigger"]),
        context=TransitionContext(**data["context"]),
    )


class CaseRunStore:
    """Typed access to ``case_runs`` and ``run_events`` for one database.

    Every write takes the caller's transaction ``session`` — this class
    never starts one (:mod:`fcrm_caseflow.engine` does, via
    :func:`fcrm_common.mongo.with_transaction`, in the same transaction as
    the :class:`fcrm_common.outbox.Outbox` row).
    """

    def __init__(
        self,
        case_runs: AsyncCollection[Document],
        run_events: AsyncCollection[Document],
    ) -> None:
        self._case_runs = case_runs
        self._run_events = run_events

    async def ensure_indexes(self) -> list[str]:
        """``run_events``' unique ``{run_id, seq}`` (design A §3.5)."""
        names = await self._run_events.create_indexes(
            [
                IndexModel(
                    [("run_id", ASCENDING), ("seq", ASCENDING)],
                    unique=True,
                    name="run_id_seq_unique",
                )
            ]
        )
        return list(names)

    async def get(
        self, run_id: str, *, session: Optional[AsyncClientSession] = None
    ) -> Optional[CaseRun]:
        """The current snapshot for ``run_id``, or ``None`` if no run is open."""
        doc = await self._case_runs.find_one({"_id": run_id}, session=session)
        return case_run_from_document(doc) if doc is not None else None

    async def get_by_case_id(
        self, case_id: str, *, session: Optional[AsyncClientSession] = None
    ) -> Optional[CaseRun]:
        """The open run for ``case_id`` (one open run per case, by
        construction: a new ``case.created`` for an already-open case is a
        poison condition this lane does not expect and will raise on —
        see :mod:`fcrm_caseflow.engine`).
        """
        doc = await self._case_runs.find_one(
            {"case_id": case_id, "closed_at": None},
            sort=[("opened_at", ASCENDING)],
            session=session,
        )
        return case_run_from_document(doc) if doc is not None else None

    async def create(
        self, document: Document, *, session: Optional[AsyncClientSession] = None
    ) -> CaseRun:
        """Insert a freshly opened run (:func:`new_case_run_document`)."""
        await self._case_runs.insert_one(document, session=session)
        return case_run_from_document(document)

    async def apply_transition(
        self,
        run: CaseRun,
        *,
        to_state: CaseState,
        flags: RunFlags,
        trigger: Trigger,
        action: str,
        message_id: Optional[str],
        context: TransitionContext,
        kind: str = "transition",
        session: AsyncClientSession,
    ) -> CaseRun:
        """Persist one applied transition: bump ``version``, move ``state``
        to ``to_state``, append the matching ``run_events`` row. The new
        ``seq`` is the pre-update ``version`` (0-indexed, matching
        ``RunSnapshot.version``'s own "event count" docstring).
        """
        seq = run.snapshot.version
        await self._run_events.insert_one(
            run_event_document(
                run_id=run.snapshot.run_id,
                seq=seq,
                kind=kind,
                trigger=trigger,
                state_from=run.state,
                state_to=to_state,
                message_id=message_id,
                context=context,
                action=action,
            ),
            session=session,
        )
        now = utcnow()
        moved = to_state != run.state
        if moved:
            dispatched_in_state: tuple[str, ...] = ()
        elif trigger.value.startswith("internal."):
            dispatched_in_state = (*run.dispatched_in_state, trigger.value)
        else:
            dispatched_in_state = run.dispatched_in_state
        update: dict[str, Any] = {
            "$set": {
                "state": to_state.value,
                "prev_state": run.state.value,
                "flags": flags.model_dump(mode="json"),
                "updated_at": now,
                "dispatched_in_state": list(dispatched_in_state),
            },
            "$inc": {"version": 1},
        }
        if to_state is CaseState.CLOSED:
            update["$set"]["closed_at"] = now
        await self._case_runs.update_one(
            {"_id": run.snapshot.run_id}, update, session=session
        )
        return CaseRun(
            snapshot=RunSnapshot(
                run_id=run.snapshot.run_id,
                case_id=run.snapshot.case_id,
                state=to_state.value,
                flags=flags,
                version=seq + 1,
                closed_at=now.isoformat() if to_state is CaseState.CLOSED else None,
            ),
            pins=run.pins,
            prev_state=run.state,
            trace_id=run.trace_id,
            root_span_id=run.root_span_id,
            opened_at=run.opened_at,
            updated_at=now,
            pending_agents=run.pending_agents,
            dispatched_in_state=dispatched_in_state,
            band=run.band,
        )

    async def set_band(
        self, run_id: str, band: int, *, session: Optional[AsyncClientSession] = None
    ) -> None:
        """Record the case's latest known band (see :class:`CaseRun.band`)."""
        await self._case_runs.update_one(
            {"_id": run_id}, {"$set": {"band": band}}, session=session
        )

    async def set_pending_agents(
        self,
        run_id: str,
        agents: list[str],
        *,
        session: Optional[AsyncClientSession] = None,
    ) -> None:
        """Replace the pending-join set (a two-agent dispatch just fired)."""
        await self._case_runs.update_one(
            {"_id": run_id}, {"$set": {"pending_agents": agents}}, session=session
        )

    async def remove_pending_agent(
        self,
        run_id: str,
        agent: str,
        *,
        session: Optional[AsyncClientSession] = None,
    ) -> list[str]:
        """Drop ``agent`` from the pending-join set and return what's left
        (``[]`` means every dispatched agent in this join has now reported
        ``agent.done`` — the caller self-raises the pair trigger).
        """
        doc = await self._case_runs.find_one_and_update(
            {"_id": run_id},
            {"$pull": {"pending_agents": agent}},
            session=session,
            return_document=ReturnDocument.AFTER,
        )
        return list((doc or {}).get("pending_agents") or [])

    async def load_recorded_transitions(
        self, run_id: str, *, session: Optional[AsyncClientSession] = None
    ) -> list[RecordedTransition]:
        """Every ``run_events`` row for ``run_id``, in ``seq`` order, as
        :class:`~fcrm_caseflow.eventstore.RecordedTransition` — feed this
        straight to :func:`fcrm_caseflow.eventstore.rebuild` to prove a
        real run replays to its stored snapshot.
        """
        cursor = self._run_events.find(
            {"run_id": run_id}, sort=[("seq", ASCENDING)], session=session
        )
        return [recorded_transition_from_document(doc) async for doc in cursor]
