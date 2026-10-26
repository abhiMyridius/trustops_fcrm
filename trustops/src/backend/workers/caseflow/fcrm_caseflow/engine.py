"""The real runtime around the pure state machine: one consumed envelope
in, one (or a short internal chain of) applied transition(s) out, each
persisted through :class:`~fcrm_caseflow.store.CaseRunStore` /
:class:`~fcrm_caseflow.gates.GateStore` and announced through
:class:`fcrm_common.outbox.Outbox` — all in the caller's Mongo
transaction (:mod:`fcrm_caseflow.consumers` opens it, via
:class:`fcrm_common.events.Consumer`'s transactional ``_apply_in_transaction``
path) — then one audit record through S4 (best-effort, same
"already-published, never re-raise" pattern :mod:`fcrm_agent_worker
.handler` already established).

:class:`CaseflowEngine` is the one thing both of this package's consumers
(:mod:`fcrm_caseflow.consumers`) delegate to; the two queues
(``caseflow.events``, ``caseflow.agent-results``) differ only in which
wire events arrive on them, not in how a resolved trigger is applied.

**The internal-dispatch chain.** :mod:`fcrm_caseflow.triggers`'s module
docstring: a handful of ``Trigger`` values are "internal" — Caseflow
raises them against itself the instant the previous step completes, with
no external event. After *every* applied transition (external or
internal), :meth:`CaseflowEngine._chain_internal` looks for exactly one
internal trigger legal from the new state, skipping any already recorded
in :class:`~fcrm_caseflow.store.CaseRun.dispatched_in_state`. That field,
not a plain "exclude the trigger just applied" check, is what stops a
self-looping dispatch row like ``SCREENING_DISPATCH`` — ``(SCREENING,
SCREENING_DISPATCH) -> SCREENING`` — from re-matching itself: excluding
only the immediately-preceding trigger is not enough, because a *later*,
separate ``engine.handle()`` call (a genuinely new wire event, e.g.
``AGENT_DONE_NARRATIVE_COMPLETENESS``'s own ``COMPLETENESS ->
COMPLETENESS`` self-loop) would otherwise start a fresh exclusion set and
wrongly re-fire ``COMPLETENESS_DISPATCH`` a second time — a real bug this
package's own tests caught; see ``dispatched_in_state``'s docstring for
the fix.

**Steps (ADR-28).** Work that needs HTTP cannot run in this transaction (it
may be re-run, and a slow call would hold it open). When a trigger is in the
engine's ``step_map`` (:data:`fcrm_caseflow.audit_map.STEP_FOR`),
:meth:`CaseflowEngine._persist_and_dispatch` enqueues a ``harness.step``
through the outbox in the same transaction, with an id derived from the
triggering message so a redelivery re-derives it, and records it in the
run's ``pending_steps``. While any step is pending
:meth:`CaseflowEngine._chain_internal` does not chain.
:meth:`CaseflowEngine.complete_step` closes a step in one short transaction
and only then lets the chain continue. See :mod:`fcrm_caseflow.steps`.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextvars import ContextVar
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Final, Optional, Protocol

from fcrm_common.events import build_envelope
from fcrm_common.ids import deterministic_id, new_id
from fcrm_common.metrics import FcrmMetrics, get_metrics
from fcrm_common.mongo import Document, with_transaction
from fcrm_common.outbox import Outbox
from fcrm_contracts.api.audit import RecordAppend
from fcrm_contracts.common import GateDecision, Principal
from fcrm_contracts.enums import (
    AgentName,
    CaseState,
    GateStatus,
    GateType,
    Role,
    StepKind,
)
from fcrm_contracts.events.envelope import Envelope
from fcrm_contracts.events.payloads import (
    AgentFailed,
    CaseIntakeRecover,
    GateOpened,
    HarnessStep,
)
from fcrm_guardrails.base import AUDIT_TYPES, GuardName, GuardResult, GuardStatus
from pymongo import AsyncMongoClient
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.errors import PyMongoError

from fcrm_caseflow.audit_client import AuditClient
from fcrm_caseflow.audit_map import RECORDS_FOR, STEP_FOR
from fcrm_caseflow.budget import (
    UNMETERED_AGENTS,
    BudgetRefusal,
    CaseBudgetStore,
    JobRequest,
    reservation_tokens,
    usage_tokens,
)
from fcrm_caseflow.dispatch import (
    REQUIRED_ROLE,
    AgentDispatch,
    DispatchPlan,
    build_ingest_envelope,
    build_job_envelope,
    dispatch_for,
    dispatch_refs,
    done_join_keys,
    gate_to_open,
    ingest_for,
    join_keys,
    trigger_gate_key,
)
from fcrm_caseflow.exceptions import IllegalTransition
from fcrm_caseflow.flags import next_flags
from fcrm_caseflow.gates import (
    GateDecisionError,
    GateStore,
    deterministic_gate_id,
    gate_document,
)
from fcrm_caseflow.handoff import handoff_subject, manual_done_payload
from fcrm_caseflow.oversight_runs import (
    GLOBAL_EVENT_TYPES,
    OversightRuns,
    oversight_result_trigger,
)
from fcrm_caseflow.raw_inputs import RawInputsReader, inputs_fields
from fcrm_caseflow.resolve import (
    agent_done_agent,
    agent_done_pair_members,
    agent_done_single,
    context_from_payload,
    gate_decided_trigger,
    resolve_simple,
)
from fcrm_caseflow.steps import GateCloseSpec, GateSpec, StepResult
from fcrm_caseflow.store import (
    AgentResultStore,
    CaseRun,
    CaseRunStore,
    new_case_run_document,
    pins_from_case_event_data,
)
from fcrm_caseflow.transitions import TransitionContext, TransitionResult, apply
from fcrm_caseflow.triggers import Trigger

logger = logging.getLogger(__name__)

#: Events the catch-all ``#`` binding of ``caseflow.events`` delivers to H1 but
#: that belong to no case run: the context worker's index lifecycle. They are
#: acknowledged and skipped; raising :class:`OrphanEventError` for them would
#: send every index build to the retry queue and then the DLQ.
NON_CASE_EVENT_TYPES = frozenset({"index.build", "index.built", "index.failed"})

_INTERNAL_PREFIX = "internal."

#: The two terminal events of an agent job: each carries the whole job's usage.
_AGENT_RESULT_TYPES = frozenset({"agent.done", "agent.failed"})

#: The PO's intake gates whose decision moves nothing but is still recorded
#: (``intake.po_action``): the wire values of their ``gate_type``.
_PO_ACTION_GATES = frozenset(
    {
        GateType.PO_CONFIRM_CATEGORY.value,
        GateType.PO_CONFIRM_VALUE.value,
        GateType.PO_REUSE.value,
        GateType.PO_ANSWER_GAPS.value,
    }
)

#: The metric errors :func:`flush_pending_metrics` drops with a warning: a
#: metric never breaks the consumer that recorded it.
_METRIC_ERRORS = (ValueError, TypeError, KeyError, AttributeError)


class _Instrument(StrEnum):
    """The H1 instrument an :class:`_Observation` is recorded into (named after
    its :class:`~fcrm_common.metrics.FcrmMetrics` field).
    """

    STAGE_TRANSITION = "case_stage_transitions_total"
    SUBMIT_TO_DECIDE = "case_submit_to_decide_seconds"
    GATE_WAIT = "gate_wait_seconds"


@dataclasses.dataclass(frozen=True)
class _Observation:
    """One metric value waiting for its transaction to commit, with the
    instrument set of the engine that observed it.
    """

    metrics: FcrmMetrics
    instrument: _Instrument
    value: float
    attributes: Mapping[str, str]


#: The observations of the current attempt, per asyncio task. A transaction may
#: run its callback more than once, and a failed one is retried as a new
#: delivery, so nothing is recorded at the commit point itself: each attempt
#: starts an empty buffer (:func:`_begin_metrics_attempt`, at the top of
#: :meth:`CaseflowEngine.handle` and of :meth:`CaseflowEngine.complete_step`'s
#: transaction) and the caller flushes it only after the commit
#: (:func:`flush_pending_metrics`). A crash between commit and flush loses the
#: observations; nothing is ever counted twice.
_PENDING_METRICS: ContextVar[Optional[list[_Observation]]] = ContextVar(
    "fcrm_caseflow_pending_metrics", default=None
)


def _begin_metrics_attempt() -> None:
    """Start an empty observation buffer for this transaction attempt."""
    _PENDING_METRICS.set([])


def _buffer_metric(observation: _Observation) -> None:
    """Keep ``observation`` until the current attempt's commit is flushed."""
    pending = _PENDING_METRICS.get()
    if pending is None:
        pending = []
        _PENDING_METRICS.set(pending)
    pending.append(observation)


def _record_metric(observation: _Observation) -> None:
    """Record one observation into its instrument."""
    metrics = observation.metrics
    if observation.instrument is _Instrument.STAGE_TRANSITION:
        metrics.case_stage_transitions_total.add(
            observation.value, observation.attributes
        )
    elif observation.instrument is _Instrument.SUBMIT_TO_DECIDE:
        metrics.case_submit_to_decide_seconds.record(
            observation.value, observation.attributes
        )
    else:
        metrics.gate_wait_seconds.record(observation.value, observation.attributes)


def flush_pending_metrics() -> None:
    """Record the current attempt's buffered observations and clear them.

    Call it only after the attempt's transaction committed: the Caseflow
    consumers do so for a ``PROCESSED`` delivery
    (:mod:`fcrm_caseflow.consumers`), and :meth:`CaseflowEngine.complete_step`
    after its transaction returns. A value the SDK refuses is logged and
    dropped; a metric never fails the caller.
    """
    pending = _PENDING_METRICS.get()
    _PENDING_METRICS.set(None)
    for observation in pending or ():
        try:
            _record_metric(observation)
        except _METRIC_ERRORS as exc:
            logger.warning(
                "caseflow metric dropped",
                extra={
                    "metric": observation.instrument.value,
                    "error": {"type": type(exc).__name__},
                },
            )


def discard_pending_metrics() -> None:
    """Drop the current attempt's buffered observations unrecorded (its
    transaction did not commit, or committed nothing new).
    """
    _PENDING_METRICS.set(None)


def _seconds_between(start: datetime, end: datetime) -> float:
    """``end - start`` in seconds, never negative (clock skew between S1 and
    H1); a naive datetime is taken as UTC.
    """
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    return max(0.0, (end - start).total_seconds())


#: S1's events for a clause the analyst added or removed (ADR-74): each starts
#: ``clause.record`` and moves nothing.
_CLAUSE_TRIGGERS: Final[Mapping[str, Trigger]] = {
    Trigger.CASE_CLAUSE_ADDED.value: Trigger.CASE_CLAUSE_ADDED,
    Trigger.CASE_CLAUSE_REMOVED.value: Trigger.CASE_CLAUSE_REMOVED,
}


class Transact(Protocol):
    """Runs ``fn(session)`` in one Mongo transaction and returns its result.

    :func:`mongo_transact` is the production implementation; a test passes a
    function that calls ``fn`` with a fake session. As with
    :func:`fcrm_common.mongo.with_transaction`, ``fn`` may run more than once,
    so it must do every write through ``session`` and nothing else.
    """

    async def __call__[R](self, fn: Callable[[AsyncClientSession], Awaitable[R]]) -> R:
        """Run ``fn`` transactionally."""
        ...


def mongo_transact(client: AsyncMongoClient[Document]) -> Transact:
    """A :class:`Transact` over ``client``'s replica set
    (:func:`fcrm_common.mongo.with_transaction`).
    """

    async def transact[R](fn: Callable[[AsyncClientSession], Awaitable[R]]) -> R:
        return await with_transaction(client, fn)

    return transact


def _joined(
    dispatches: Sequence[AgentDispatch], envelopes: Sequence[Envelope]
) -> list[str]:
    """The pending-join keys of ``dispatches`` published as ``envelopes``
    (same order): see :func:`fcrm_caseflow.dispatch.join_keys`.
    """
    return join_keys(
        [
            (dispatch.agent, str(envelope.payload["job_id"]))
            for dispatch, envelope in zip(dispatches, envelopes, strict=True)
        ]
    )


class OrphanEventError(Exception):
    """A non-``case.created`` event names a ``case_id``/``run_id`` with no
    open run. Raised rather than silently dropped: on a well-formed bus
    this should never happen (``case.created`` always precedes everything
    else for a case), so it is worth a retry/DLQ entry rather than a
    swallowed log line.
    """


class DuplicateCaseCreatedError(Exception):
    """A second ``case.created`` arrived for a case that already has an
    open run — "one open run per case" is this package's own invariant,
    not yet one Mongo enforces with a unique index, so this is the
    defensive check standing in for it.
    """


class CaseflowEngine:
    """Applies one envelope's worth of state-machine work, and closes the
    harness steps its transitions enqueue.

    Args:
        runs: ``case_runs`` / ``run_events``.
        gates: ``gates``.
        outbox: The transactional outbox every announcement goes through.
        audit: S4, for the per-transition audit record.
        step_map: Trigger -> step kind. A trigger in the map enqueues that
            step in the transaction that applied it. Defaults to
            :data:`fcrm_caseflow.audit_map.STEP_FOR` (empty until the cores
            land); tests inject entries.
        transact: Opens the one short transaction :meth:`complete_step` runs
            in (:func:`mongo_transact`). ``handle`` never needs it: its caller
            already owns the transaction. Without it ``complete_step`` raises.
        results: Where each ``agent.done`` is kept as it arrives, for a step
            that merges several jobs (:class:`~fcrm_caseflow.store
            .AgentResultStore`). Without it nothing is stored.
        ingest_bucket: The bucket H3 writes a document's canonical Markdown
            into, named in every ``ingest.run`` H1 publishes.
        records_for: Step kind -> the audit record types it writes. When the
            step a trigger enqueues writes a record of the trigger's own
            type (``case.created``, ``case.submitted``), the engine skips its
            generic per-transition record of that type, so the chain does not
            carry the same type twice.
        oversight: Finds or opens the run of an oversight event that names no
            open run (``examiner.asked``, ``jurisdiction.list_published``;
            :mod:`fcrm_caseflow.oversight_runs`). Without it those two events
            are refused rather than treated as orphans of a case.
        raw_inputs: Reads what a failed job was given (kept by H2), to show on
            the handoff gate opened for it. Without it the gate names the
            reference and carries no text.
        budgets: The per-case token ledger (:mod:`fcrm_caseflow.budget`):
            every job is reserved before it is published and settled when its
            result arrives. Without it no cap is enforced.
        metrics: Instrument set for the stage-transition, submit-to-decide
            and gate-wait metrics (defaults to the process-wide one). They
            are buffered per attempt and recorded by :meth:`flush_metrics`
            once the transaction has committed.
    """

    def __init__(
        self,
        runs: CaseRunStore,
        gates: GateStore,
        outbox: Outbox,
        audit: AuditClient,
        *,
        step_map: Mapping[Trigger, StepKind] = STEP_FOR,
        transact: Optional[Transact] = None,
        results: Optional[AgentResultStore] = None,
        ingest_bucket: str = "canonical",
        records_for: Mapping[StepKind, tuple[str, ...]] = RECORDS_FOR,
        oversight: Optional[OversightRuns] = None,
        raw_inputs: Optional[RawInputsReader] = None,
        budgets: Optional[CaseBudgetStore] = None,
        metrics: Optional[FcrmMetrics] = None,
    ) -> None:
        self._runs = runs
        self._gates = gates
        self._outbox = outbox
        self._audit = audit
        self._step_map = step_map
        self._transact = transact
        self._results = results
        self._ingest_bucket = ingest_bucket
        self._records_for = records_for
        self._oversight = oversight
        self._raw_inputs = raw_inputs
        self._budgets = budgets
        self._metrics = metrics if metrics is not None else get_metrics()

    # -- metrics, recorded only after a commit --------------------------

    def flush_metrics(self) -> None:
        """Record the current attempt's buffered observations; call it only
        after the attempt's transaction committed (:func:`flush_pending_metrics`).
        """
        flush_pending_metrics()

    def discard_metrics(self) -> None:
        """Drop the current attempt's buffered observations unrecorded
        (:func:`discard_pending_metrics`).
        """
        discard_pending_metrics()

    async def _observe_transition(
        self, run: CaseRun, result: TransitionResult, session: AsyncClientSession
    ) -> None:
        """Buffer the metrics of one persisted transition: a move into a new
        state counts once under ``to`` (a self-loop is not a move), and a move
        into CLOSED also buffers the time since the run's ``case.submitted``
        (skipped when the run has no such row).
        """
        if result.to_state == result.from_state:
            return
        _buffer_metric(
            _Observation(
                self._metrics,
                _Instrument.STAGE_TRANSITION,
                1.0,
                {"to": result.to_state.value},
            )
        )
        closed_at = run.snapshot.closed_at
        if result.to_state is not CaseState.CLOSED or closed_at is None:
            return
        try:
            submitted = await self._runs.submitted_at(
                run.snapshot.run_id, session=session
            )
        except PyMongoError as exc:
            logger.warning(
                "submit time unreadable; submit-to-decide not recorded",
                extra={
                    "case_id": run.snapshot.case_id,
                    "error": {"type": type(exc).__name__},
                },
            )
            return
        if submitted is None:
            return
        _buffer_metric(
            _Observation(
                self._metrics,
                _Instrument.SUBMIT_TO_DECIDE,
                _seconds_between(submitted, datetime.fromisoformat(closed_at)),
                {},
            )
        )

    async def handle(
        self, envelope: Envelope, session: Optional[AsyncClientSession]
    ) -> None:
        """Apply ``envelope`` to whichever run it names.

        Raises:
            OrphanEventError: no open run matches.
            DuplicateCaseCreatedError: a second ``case.created`` for an
                already-open case.
            fcrm_caseflow.exceptions.IllegalTransition: the resolved
                trigger has no legal transition from the run's current
                state.
            fcrm_caseflow.gates.GateDecisionError: Guard 7 refused a
                ``gate.decided`` event.
            ValueError, KeyError: a payload this layer could not make
                sense of (an unrecognised ``gate_type``/``decision``
                string, a missing/unknown ``agent`` on ``agent.done``).
            Every one of these propagates out of ``handle`` so the base
            ``Consumer``/``deliver_with_retry`` routes the message to
            retry or the DLQ exactly like any other handler failure —
            this engine never swallows a resolution it does not
            understand.
            ValueError: ``session`` is ``None`` — this engine requires the
                transactional path (every write commits with its outbox
                row atomically, CLAUDE.md's repo-wide rule); construct its
                consumers with a real Mongo ``client`` (see
                :func:`fcrm_caseflow.app.build_app`) rather than calling it
                with ``client=None``.
        """
        if session is None:
            raise ValueError(
                "CaseflowEngine requires a transactional session; configure "
                "its Consumer with a Mongo client (see fcrm_caseflow.app"
                ".build_app) so every state write commits with its outbox "
                "row atomically"
            )
        _begin_metrics_attempt()
        if envelope.type in NON_CASE_EVENT_TYPES:
            logger.debug("skipped %s: not a case event", envelope.type)
            return
        if envelope.type == "case.created":
            await self._open_run(envelope, session)
            return
        if envelope.type in GLOBAL_EVENT_TYPES:
            await self._handle_global(envelope, session)
            return
        run = await self._load_run(envelope, session)
        if run is None:
            if await self._run_is_closed(envelope, session):
                logger.info(
                    "skipped %s: the case's run is closed",
                    envelope.type,
                    extra={"case_id": envelope.case_id, "run_id": envelope.run_id},
                )
                return
            raise OrphanEventError(
                f"{envelope.type} for case={envelope.case_id} run={envelope.run_id} "
                "has no open run"
            )
        await self._settle_result(envelope, run, session)
        result_trigger = oversight_result_trigger(envelope)
        if result_trigger is not None:
            await self._handle_oversight_result(envelope, run, result_trigger, session)
            return
        if self._is_handoff_decision(envelope):
            await self._handle_handoff_decided(envelope, run, session)
            return
        resolution = await self._resolve(envelope, run, session)
        if resolution is None:
            # No primary trigger for this event (an informational-only
            # gate decision such as ACK, or an unmapped/self-echoed event —
            # see fcrm_caseflow.resolve's module docstring). Still give the
            # internal chain a chance: deciding an ACK gate has no trigger
            # of its own, but it can be exactly what makes
            # ALL_ITEMS_DECIDED's guard (all_items_decided AND acks_done)
            # newly legal, and nothing else will ever re-check that for
            # this run if this call does not. A found gap, same shape as
            # D-15 — see the package report.
            run = await self._po_action_step(envelope, run, session)
            run = await self._ack_step(envelope, run, session)
            run = await self._reflag_ack_step(envelope, run, session)
            run = await self._clause_step(envelope, run, session)
            base_ctx = TransitionContext(flags=run.snapshot.flags, band=run.band)
            await self._chain_internal(run, base_ctx, session, envelope.event_id)
            return
        trigger, ctx = resolution
        await self._apply_and_chain(
            run, trigger, ctx, envelope.event_id, envelope.payload, session
        )

    # -- opening a run -----------------------------------------------

    async def _open_run(self, envelope: Envelope, session: AsyncClientSession) -> None:
        if envelope.case_id is None:
            raise ValueError("case.created without a case_id")
        existing = await self._runs.get_by_case_id(envelope.case_id, session=session)
        if existing is not None:
            raise DuplicateCaseCreatedError(envelope.case_id)
        data = envelope.payload.get("data")
        pins = pins_from_case_event_data(data if isinstance(data, dict) else {})
        document = new_case_run_document(
            run_id=new_id("run"),
            case_id=envelope.case_id,
            pins=pins,
            trace_id=envelope.trace_id,
        )
        run = await self._runs.create(document, session=session)
        # Opening a run is not a state transition, but it can still start a
        # step (``intake.open``: field set, ``workflow.started``).
        await self._enqueue_step_for(
            run,
            Trigger.CASE_CREATED,
            envelope.event_id,
            envelope.event_id,
            envelope.payload,
            session,
        )
        if not self._step_writes_record(Trigger.CASE_CREATED):
            await self._write_audit_raw(
                case_id=run.snapshot.case_id,
                trigger_value="case.created",
                actor="system",
                summary="run opened; pins recorded",
                data={"run_id": run.snapshot.run_id},
                origin=envelope.event_id,
            )

    # -- oversight (scene 20) ------------------------------------------

    async def _handle_global(
        self, envelope: Envelope, session: AsyncClientSession
    ) -> None:
        """An oversight event that names no open run: find or open the run it
        belongs to and enqueue the step-only trigger's step (no transition).

        Raises:
            ValueError: The engine was built without ``oversight``.
        """
        if self._oversight is None:
            raise ValueError(
                f"{envelope.type} needs the engine's oversight runs "
                "(fcrm_caseflow.oversight_runs.OversightRuns)"
            )
        target = await self._oversight.target_for(envelope, session)
        await self._enqueue_step_for(
            target.run,
            target.trigger,
            envelope.event_id,
            envelope.event_id,
            envelope.payload,
            session,
        )

    async def _handle_oversight_result(
        self,
        envelope: Envelope,
        run: CaseRun,
        trigger: Trigger,
        session: AsyncClientSession,
    ) -> None:
        """An ``agent.done`` / ``agent.failed`` of the examiner or the watchlist:
        keep the result, then enqueue the step that answers or flags. It never
        reaches the transition table, so a failed examiner on a closed run is a
        refusal, not an illegal transition.
        """
        if envelope.type == "agent.done" and self._results is not None:
            await self._results.save(envelope.payload, session=session)
        await self._enqueue_step_for(
            run,
            trigger,
            envelope.event_id,
            envelope.event_id,
            envelope.payload,
            session,
        )

    async def _reflag_ack_step(
        self, envelope: Envelope, run: CaseRun, session: AsyncClientSession
    ) -> CaseRun:
        """Start ``reflag.ack`` for an analyst's acknowledgement of a re-flag, in
        this transaction and without a transition: the step records it on the
        re-flagged case's chain. Any other event leaves the run unchanged.
        """
        if (
            envelope.type != "gate.decided"
            or envelope.payload.get("gate_type") != GateType.REFLAG_ACK.value
        ):
            return run
        return await self._enqueue_step_for(
            run,
            Trigger.GATE_DECIDED_REFLAG_ACK,
            envelope.event_id,
            envelope.event_id,
            envelope.payload,
            session,
        )

    # -- loading the target run --------------------------------------

    async def _load_run(
        self, envelope: Envelope, session: AsyncClientSession
    ) -> Optional[CaseRun]:
        if envelope.run_id is not None:
            run = await self._runs.get(envelope.run_id, session=session)
            if run is not None:
                return run
        if envelope.case_id is not None:
            return await self._runs.get_by_case_id(envelope.case_id, session=session)
        return None

    async def _run_is_closed(
        self, envelope: Envelope, session: AsyncClientSession
    ) -> bool:
        """Whether the case has a run and it is closed: an event that arrives
        after the close (the committee's ``outcome.set`` follows the tally that
        closed the run) can never apply, so it is acknowledged, not retried and
        dead-lettered as if the run were still to come.
        """
        if envelope.case_id is None:
            return False
        run = await self._runs.get_any_by_case_id(envelope.case_id, session=session)
        return run is not None and run.snapshot.closed_at is not None

    # -- resolving a trigger ------------------------------------------

    async def _resolve(
        self,
        envelope: Envelope,
        run: CaseRun,
        session: AsyncClientSession,
    ) -> Optional[tuple[Trigger, TransitionContext]]:
        intake_failure = await self._resolve_intake_failure(envelope, run, session)
        if intake_failure is not None:
            return intake_failure
        if envelope.type == Trigger.CASE_INTAKE_RECOVER.value:
            return await self._resolve_intake_recover(envelope, run, session)
        simple = resolve_simple(envelope)
        if simple is not None:
            return simple.trigger, simple.context
        if envelope.type == "gate.decided":
            return await self._resolve_gate_decided(envelope, run, session)
        if envelope.type == "agent.done":
            return await self._resolve_agent_done(envelope, run, session)
        logger.info(
            "no trigger mapping for %s; treating as informational",
            envelope.type,
            extra={"case_id": envelope.case_id, "run_id": envelope.run_id},
        )
        return None

    async def _resolve_intake_failure(
        self,
        envelope: Envelope,
        run: CaseRun,
        session: AsyncClientSession,
    ) -> Optional[tuple[Trigger, TransitionContext]]:
        """An intake job that failed while the case is still in INTAKE (D-130):
        clear its join entry, so the others' last ``agent.done`` publishes, and
        keep the run with the owner (``INTAKE_JOB_FAILED``), never HANDOFF.
        ``None`` for every other ``agent.failed`` and every other event.
        """
        if envelope.type != "agent.failed" or run.state is not CaseState.INTAKE:
            return None
        failed = AgentFailed.model_validate(envelope.payload)
        if failed.agent is not AgentName.INTAKE:
            return None
        await self._clear_join_key(run, AgentName.INTAKE, failed.job_id, session)
        return Trigger.INTAKE_JOB_FAILED, TransitionContext()

    async def _resolve_intake_recover(
        self,
        envelope: Envelope,
        run: CaseRun,
        session: AsyncClientSession,
    ) -> Optional[tuple[Trigger, TransitionContext]]:
        """``case.intake_recover`` (D-130): for a run in HANDOFF suspended from
        INTAKE, close its open handoff gates (the stalled intake jobs: what they
        would have read the owner now enters), drop their join entries, and
        resume to INTAKE. Any other run is left alone (logged, acknowledged).
        """
        recover = CaseIntakeRecover.model_validate(envelope.payload)
        if (
            run.state is not CaseState.HANDOFF
            or run.suspended_from is not CaseState.INTAKE
        ):
            logger.warning(
                "case.intake_recover for a run not held from INTAKE; ignored",
                extra={"case_id": envelope.case_id, "state": run.state.value},
            )
            return None
        for gate_id in await self._gates.open_ids(
            run.snapshot.case_id,
            GateType.HANDOFF,
            run_id=run.snapshot.run_id,
            session=session,
        ):
            await self._gates.close_if_open(
                gate_id,
                status=GateStatus.EXPIRED,
                decision="superseded",
                reason=recover.reason,
                session=session,
            )
        for key in list(run.pending_agents):
            if key.startswith(AgentName.INTAKE.value):
                await self._runs.remove_pending_agent(
                    run.snapshot.run_id, key, session=session
                )
        return Trigger.CASE_INTAKE_RECOVER, TransitionContext(
            resume_state=CaseState.INTAKE
        )

    async def _resolve_gate_decided(
        self,
        envelope: Envelope,
        run: CaseRun,
        session: AsyncClientSession,
    ) -> Optional[tuple[Trigger, TransitionContext]]:
        decision, gate_doc = await self._decide_gate(envelope, session)
        gate_type = GateType(gate_doc["gate_type"])
        if gate_type is GateType.PO_CONFIRM_CATEGORY:
            remaining = await self._gates.count_open(
                decision.case_id,
                GateType.PO_CONFIRM_CATEGORY,
                run_id=decision.run_id,
                session=session,
            )
            if remaining > 0:
                return None
            gaps = await self._gates.count_open(
                decision.case_id,
                GateType.PO_ANSWER_GAPS,
                run_id=decision.run_id,
                session=session,
            )
            return Trigger.GATE_DECIDED_PO_CONFIRM_CATEGORY_ALL, TransitionContext(
                gaps_open=gaps > 0
            )
        trigger = gate_decided_trigger(gate_type, decision.decision)
        if trigger is None:
            return None
        ctx = self._gate_decision_context(trigger, decision, envelope.payload)
        return trigger, ctx

    async def _decision_and_gate(
        self, envelope: Envelope, session: AsyncClientSession
    ) -> tuple[GateDecision, Document]:
        """The decision a ``gate.decided`` event carries and the gate it names,
        nothing changed yet.

        Raises:
            GateDecisionError: no such gate.
        """
        known = set(GateDecision.model_fields)
        decision = GateDecision.model_validate(
            {k: v for k, v in envelope.payload.items() if k in known}
        )
        gate_doc = await self._gates.get(decision.gate_id, session=session)
        if gate_doc is None:
            result = GuardResult(
                guard=GuardName.HUMAN_GATE,
                status=GuardStatus.BLOCKED,
                summary=f"no gate {decision.gate_id!r} is open for {decision.case_id}",
                details={"gate_id": decision.gate_id},
                audit_type=AUDIT_TYPES[GuardName.HUMAN_GATE],
            )
            raise GateDecisionError(result)
        return decision, gate_doc

    async def _guard_gate(
        self,
        decision: GateDecision,
        gate_doc: Document,
        session: AsyncClientSession,
    ) -> None:
        """Guard 7: check ``decision`` against its gate and mark the gate decided.

        Raises:
            GateDecisionError: Guard 7 refused the decision.
        """
        principal = Principal(
            sub=decision.decided_by,
            roles=[Role(gate_doc["required_role"])],
            name=decision.decided_by,
            typ="access",
        )
        await self._gates.decide(decision, principal, session=session)
        opened_at = gate_doc.get("opened_at")
        if isinstance(opened_at, datetime):
            _buffer_metric(
                _Observation(
                    self._metrics,
                    _Instrument.GATE_WAIT,
                    _seconds_between(opened_at, decision.decided_at),
                    {"gate_type": str(gate_doc["gate_type"])},
                )
            )

    async def _decide_gate(
        self, envelope: Envelope, session: AsyncClientSession
    ) -> tuple[GateDecision, Document]:
        """Guard 7 over a ``gate.decided`` event: find its gate, check the decision
        against it and mark the gate decided. Returns the decision and the gate
        document as it was before.

        Raises:
            GateDecisionError: no such gate, or Guard 7 refused the decision.
        """
        decision, gate_doc = await self._decision_and_gate(envelope, session)
        await self._guard_gate(decision, gate_doc, session)
        return decision, gate_doc

    @staticmethod
    def _is_handoff_decision(envelope: Envelope) -> bool:
        """A ``gate.decided`` for a ``handoff`` gate: the analyst completed a failed
        step by hand."""
        return (
            envelope.type == "gate.decided"
            and envelope.payload.get("gate_type") == GateType.HANDOFF.value
        )

    async def _handle_handoff_decided(
        self, envelope: Envelope, run: CaseRun, session: AsyncClientSession
    ) -> None:
        """The analyst completed a failed step by hand (flow F-path).

        The output the decision carries is checked like an agent's
        (:func:`~fcrm_caseflow.handoff.manual_done_payload`) and becomes the
        ``agent.done`` of the failed job with ``model`` ``human``; the engine then
        handles it exactly like the agent's own. While another failed job's
        handoff gate is still open the run stays in ``HANDOFF`` (this output is
        kept and its join entry cleared); the decision that closes the last one
        resumes the run to the state it was suspended from, and the join, if this
        completes it, fires the trigger the agent's ``agent.done`` would have.

        Raises:
            GateDecisionError: Guard 7 refused the decision.
            fcrm_caseflow.handoff.HandoffError: the gate names no job or the
                output fails its checks (the transaction rolls back: the gate
                stays open).
        """
        decision, gate_doc = await self._decision_and_gate(envelope, session)
        # The output is checked before the gate is marked decided, so a refusal
        # leaves the gate open whether or not the caller's transaction rolls back.
        done = manual_done_payload(decision, gate_doc.get("subject") or {})
        await self._guard_gate(decision, gate_doc, session)
        message_id = envelope.event_id
        remaining = await self._gates.count_open(
            decision.case_id, GateType.HANDOFF, run_id=decision.run_id, session=session
        )
        if remaining == 0 and run.state is CaseState.HANDOFF:
            run = await self._resume_from_handoff(envelope, run, session)
        else:
            # Another failed job still waits: the run stays in HANDOFF, and the
            # step still records this completion and tells S1 where the run is.
            run = await self._enqueue_step_for(
                run,
                Trigger.GATE_DECIDED_HANDOFF,
                message_id,
                message_id,
                envelope.payload,
                session,
            )
        done_envelope = build_envelope(
            "agent.done",
            done,
            case_id=run.snapshot.case_id,
            run_id=run.snapshot.run_id,
        )
        resolution = await self._resolve_agent_done(done_envelope, run, session)
        if resolution is None:
            base = TransitionContext(flags=run.snapshot.flags, band=run.band)
            await self._chain_internal(run, base, session, message_id)
            return
        trigger, ctx = resolution
        await self._apply_and_chain(
            run, trigger, ctx, f"{message_id}/human", done, session
        )

    async def _resume_from_handoff(
        self, envelope: Envelope, run: CaseRun, session: AsyncClientSession
    ) -> CaseRun:
        """Move a run out of ``HANDOFF`` to the state it was suspended from
        (``GATE_DECIDED_HANDOFF``, with ``resume_state`` in its context so a
        replay lands in the same place). Returns the resumed run.
        """
        trigger = Trigger.GATE_DECIDED_HANDOFF
        ctx = TransitionContext(
            flags=run.snapshot.flags, band=run.band, resume_state=run.suspended_from
        )
        ctx = await self._refresh_gate_derived_ctx(run, ctx, session)
        result = apply(run.state, trigger, ctx)
        return await self._persist_and_dispatch(
            run,
            trigger,
            ctx,
            result,
            envelope.event_id,
            envelope.event_id,
            envelope.payload,
            session,
        )

    async def _po_action_step(
        self, envelope: Envelope, run: CaseRun, session: AsyncClientSession
    ) -> CaseRun:
        """Start ``intake.po_action`` for a PO decision that moves nothing (a
        change type confirmed while others are open, a value corrected, values
        reused, a question answered), in this transaction and without a
        transition: the run stays where it is and only the step records what
        the PO did. Outside INTAKE, or for any other gate, the run is returned
        unchanged.
        """
        if envelope.type != "gate.decided" or run.state is not CaseState.INTAKE:
            return run
        if envelope.payload.get("gate_type") not in _PO_ACTION_GATES:
            return run
        return await self._enqueue_step_for(
            run,
            Trigger.GATE_DECIDED_PO_ACTION,
            envelope.event_id,
            envelope.event_id,
            envelope.payload,
            session,
        )

    async def _ack_step(
        self, envelope: Envelope, run: CaseRun, session: AsyncClientSession
    ) -> CaseRun:
        """Start ``review.ack`` for an acknowledgement (R3), in this transaction
        and without a transition: the run stays where it is, the step records
        what the analyst acknowledged, and the chain that follows its completion
        re-checks whether every item and acknowledgement is now done.
        """
        if (
            envelope.type != "gate.decided"
            or envelope.payload.get("gate_type") != GateType.ACK.value
        ):
            return run
        return await self._enqueue_step_for(
            run,
            Trigger.GATE_DECIDED_ACK,
            envelope.event_id,
            envelope.event_id,
            envelope.payload,
            session,
        )

    async def _clause_step(
        self, envelope: Envelope, run: CaseRun, session: AsyncClientSession
    ) -> CaseRun:
        """Start ``clause.record`` for a clause the analyst added from a corpus
        search or removed (ADR-74), in this transaction and without a transition:
        the run stays where it is (review or recommend) and the step writes the
        audit record. Any other event leaves the run unchanged.
        """
        trigger = _CLAUSE_TRIGGERS.get(envelope.type)
        if trigger is None:
            return run
        return await self._enqueue_step_for(
            run,
            trigger,
            envelope.event_id,
            envelope.event_id,
            envelope.payload,
            session,
        )

    @staticmethod
    def _gate_decision_context(
        trigger: Trigger, decision: GateDecision, payload: dict[str, Any]
    ) -> TransitionContext:
        if trigger is Trigger.GATE_DECIDED_FAST_LANE_CONFIRM:
            confirmed = str(decision.decision).strip().lower() in {
                "confirm",
                "confirmed",
                "true",
                "yes",
            }
            return TransitionContext(fast_lane_confirmed=confirmed)
        if trigger is Trigger.GATE_DECIDED_REVIEW_ITEM:
            changed = (
                decision.before != decision.after
                if decision.before is not None and decision.after is not None
                else bool(payload.get("rating_or_control_changed", False))
            )
            return TransitionContext(
                rating_or_control_changed=changed,
                band_changed=bool(payload.get("band_changed", False)),
                boundary_flagged=bool(payload.get("boundary_flagged", False)),
            )
        return TransitionContext()

    async def _resolve_agent_done(
        self,
        envelope: Envelope,
        run: CaseRun,
        session: AsyncClientSession,
    ) -> Optional[tuple[Trigger, TransitionContext]]:
        agent = agent_done_agent(envelope)
        if await self._is_repeat_done(envelope, run, session):
            return None
        job_id = envelope.payload.get("job_id")
        cleared = await self._clear_join_key(run, agent, job_id, session)
        if run.state is CaseState.HANDOFF:
            # A job that finishes while a person holds the case: its result is
            # kept and its join entry cleared, but no trigger fires. The one
            # that completes the failed job does, once the run has resumed.
            logger.info(
                "agent.done while in handoff; no trigger until the run resumes",
                extra={"case_id": envelope.case_id, "agent": agent.value},
            )
            return None
        state = run.state
        single = agent_done_single(state, agent)
        if single is not None:
            # A job of a fan-out (one intake job per document) may be one of
            # several; the trigger fires when the last one reports done.
            if cleared:
                return None
            return single, context_from_payload(envelope.payload)
        pair = agent_done_pair_members(state)
        if pair is None:
            logger.warning(
                "agent.done for %s in state %s has no mapping; ignoring",
                agent.value,
                state.value,
                extra={"case_id": envelope.case_id, "run_id": envelope.run_id},
            )
            return None
        members, trigger = pair
        if agent not in members:
            logger.warning(
                "agent.done for %s is not part of the %s join in state %s; ignoring",
                agent.value,
                trigger.value,
                state.value,
            )
            return None
        if cleared is None:
            cleared = await self._runs.remove_pending_agent(
                run.snapshot.run_id, agent.value, session=session
            )
        if cleared:
            return None
        return trigger, context_from_payload(envelope.payload)

    async def _is_repeat_done(
        self, envelope: Envelope, run: CaseRun, session: AsyncClientSession
    ) -> bool:
        """Keep an ``agent.done``'s result and say whether it is a repeat.

        The result is kept before anything else: the proposals exist nowhere
        else, and a step that merges several jobs reads them back from here. A
        repeat is a second ``agent.done`` for a job whose result is already
        stored: H2 ran a redelivered job a second time and published again
        under a new event id, which the consumer's message-id dedupe cannot
        see. Applying it would enqueue a second step and write every record
        again, so the caller ignores it.
        """
        if self._results is None:
            return False
        if await self._results.save(envelope.payload, session=session):
            return False
        logger.info(
            "agent.done for job %s was already applied; ignoring the repeat",
            envelope.payload.get("job_id"),
            extra={"case_id": envelope.case_id, "run_id": run.snapshot.run_id},
        )
        return True

    async def _clear_join_key(
        self,
        run: CaseRun,
        agent: AgentName,
        job_id: Any,
        session: AsyncClientSession,
    ) -> Optional[list[str]]:
        """Remove the pending-join entry this ``agent.done`` answers, and
        return what is still pending (``[]``: the join is complete). ``None``
        when no entry of the run's join matches (no join is in progress for
        this agent).
        """
        keys = done_join_keys(agent, job_id if isinstance(job_id, str) else None)
        for key in keys:
            if key in run.pending_agents:
                return await self._runs.remove_pending_agent(
                    run.snapshot.run_id, key, session=session
                )
        return None

    # -- applying + persisting one transition, then chaining ----------

    async def _apply_and_chain(
        self,
        run: CaseRun,
        trigger: Trigger,
        ctx: TransitionContext,
        message_id: str,
        payload: dict[str, Any],
        session: AsyncClientSession,
    ) -> None:
        ctx = dataclasses.replace(
            ctx,
            flags=run.snapshot.flags,
            band=ctx.band if ctx.band is not None else run.band,
        )
        # The guards that read open-gate counts see H1's own gates, never a
        # value a payload carried (resolve.H1_DERIVED_CONTEXT_FIELDS).
        ctx = await self._refresh_gate_derived_ctx(run, ctx, session)
        result = apply(run.state, trigger, ctx)
        run = await self._persist_and_dispatch(
            run, trigger, ctx, result, message_id, message_id, payload, session
        )
        await self._chain_internal(run, ctx, session, message_id)

    async def _persist_and_dispatch(
        self,
        run: CaseRun,
        trigger: Trigger,
        ctx: TransitionContext,
        result: TransitionResult,
        message_id: Optional[str],
        origin: str,
        payload: dict[str, Any],
        session: AsyncClientSession,
    ) -> CaseRun:
        """Persist one applied transition and everything it announces.

        ``origin`` is the id of the envelope or completed step whose handling
        led here. A chained (internal) trigger has no message of its own
        (``message_id is None``), so a step it enqueues derives its id from
        ``origin`` plus the trigger: stable across redeliveries of the
        message that started the chain.
        """
        new_flags = next_flags(trigger, ctx, run.snapshot.flags)
        run = await self._runs.apply_transition(
            run,
            to_state=result.to_state,
            flags=new_flags,
            trigger=trigger,
            action=result.action,
            message_id=message_id,
            context=ctx,
            session=session,
        )
        await self._observe_transition(run, result, session)
        if ctx.band is not None and ctx.band != run.band:
            await self._runs.set_band(run.snapshot.run_id, ctx.band, session=session)
            run = dataclasses.replace(run, band=ctx.band)
        refusal = await self._dispatch(run, trigger, ctx, payload, session)
        run = await self._enqueue_step_for(
            run, trigger, message_id, origin, payload, session
        )
        await self._write_audit(run, trigger, result, origin)
        if refusal is not None:
            run = await self._hand_off(run, refusal, origin, session)
        return run

    async def _dispatch(
        self,
        run: CaseRun,
        trigger: Trigger,
        ctx: TransitionContext,
        payload: dict[str, Any],
        session: AsyncClientSession,
    ) -> Optional[BudgetRefusal]:
        """Publish what ``trigger`` dispatches, open its gate, hand a document
        to H3. Returns the refusal when the case's token cap said no to the jobs
        (nothing was published; the caller hands the case to the analyst), else
        ``None``.
        """
        plan: DispatchPlan = dispatch_for(trigger, ctx, run.snapshot.flags, payload)
        envelopes = [
            build_job_envelope(job, run, refs=dispatch_refs(job, run.refs))
            for job in plan.agents
        ]
        refusal = await self._publish_jobs(run, plan.agents, envelopes, session)
        if refusal is not None:
            return refusal
        if len(plan.agents) > 1:
            await self._runs.set_pending_agents(
                run.snapshot.run_id,
                _joined(plan.agents, envelopes),
                session=session,
            )
        ingest = ingest_for(trigger, payload)
        if ingest is not None:
            await self._outbox.enqueue(
                session,
                build_ingest_envelope(ingest, run, target_bucket=self._ingest_bucket),
            )
        gate_type = gate_to_open(trigger, ctx)
        if gate_type is GateType.HANDOFF:
            # The subject names the failed job, so the analyst's decision can be
            # matched to it and a replay of the failure opens the same gate.
            subject = handoff_subject(payload, run.suspended_from)
            subject.update(await self._inputs_subject(subject, session))
            await self._open_gate(
                run, gate_type, session, subject=subject, key=subject["job_id"]
            )
        elif gate_type is not None:
            await self._open_gate(run, gate_type, session)
        return None

    async def _inputs_subject(
        self, subject: dict[str, str], session: AsyncClientSession
    ) -> dict[str, str]:
        """The gate-subject fields that carry the failed job's inputs (a bounded
        text, its digest, whether it was cut), read from what H2 kept; empty
        when H2 named none or the reader is not wired.
        """
        ref = subject.get("raw_inputs_ref")
        if ref is None or self._raw_inputs is None:
            return {}
        found = await self._raw_inputs.read(ref, session=session)
        if found is None:
            logger.warning("the failed job's inputs are not there", extra={"ref": ref})
            return {"raw_inputs_error": "the inputs were not kept"}
        return inputs_fields(found)

    async def _open_gate(
        self,
        run: CaseRun,
        gate_type: GateType,
        session: AsyncClientSession,
        *,
        subject: Optional[Mapping[str, str]] = None,
        key: Optional[str] = None,
    ) -> None:
        """Open the gate a transition calls for. The three gates a step must
        know the id of (:func:`~fcrm_caseflow.dispatch.trigger_gate_key`), and
        any gate given a ``key`` (a handoff gate: the failed job's id), get a
        deterministic id and are opened at most once (a replayed trigger finds
        the gate it opened); any other gets a fresh one.
        """
        key = key if key is not None else trigger_gate_key(gate_type, run)
        gate_id = (
            deterministic_gate_id(run.snapshot.run_id, gate_type, key)
            if key is not None
            else new_id("gate")
        )
        required_role = REQUIRED_ROLE[gate_type]
        document: Document = gate_document(
            gate_id=gate_id,
            case_id=run.snapshot.case_id,
            run_id=run.snapshot.run_id,
            gate_type=gate_type,
            required_role=required_role,
            subject=dict(subject or {}),
        )
        if key is not None:
            if not await self._gates.open_if_absent(document, session=session):
                logger.info(
                    "gate already open; not announced again",
                    extra={"case_id": run.snapshot.case_id, "gate_id": gate_id},
                )
                return
        else:
            await self._gates.open(document, session=session)
        await self._announce_gate_opened(
            run, gate_id, gate_type, required_role, subject or {}, session
        )

    async def _announce_gate_opened(
        self,
        run: CaseRun,
        gate_id: str,
        gate_type: GateType,
        required_role: Role,
        subject: Mapping[str, str],
        session: AsyncClientSession,
        *,
        case_id: Optional[str] = None,
    ) -> None:
        opened = GateOpened(
            gate_id=gate_id,
            gate_type=gate_type,
            case_id=case_id or run.snapshot.case_id,
            run_id=run.snapshot.run_id,
            subject=dict(subject),
            required_role=required_role.value,
        )
        env = build_envelope(
            "gate.opened",
            opened.model_dump(mode="json"),
            case_id=case_id or run.snapshot.case_id,
            run_id=run.snapshot.run_id,
        )
        await self._outbox.enqueue(session, env)

    async def _chain_internal(
        self,
        run: CaseRun,
        ctx: TransitionContext,
        session: AsyncClientSession,
        origin: str,
    ) -> None:
        """Auto-fire internal triggers until none are left legal from the
        current state. ``run.dispatched_in_state`` (already correct by the
        time this is called — :meth:`_persist_and_dispatch` maintains it on
        every write, including the one that preceded this call) is what
        stops an unconditional self-loop dispatch trigger
        (``SCREENING_DISPATCH``, ...) from re-firing on a *later*, separate
        ``engine.handle()`` call that happens to leave the run in the same
        state (see :class:`~fcrm_caseflow.store.CaseRun.dispatched_in_state`'s
        docstring for the bug this specifically fixes).

        Returns without chaining while a harness step is pending: the step's
        result (gates opened, agents dispatched) decides what the next
        trigger's guard sees, so :meth:`complete_step` resumes the chain once
        the step is done. A trigger that enqueues a step mid-chain stops the
        chain at that point for the same reason.

        ``origin`` is the id of the envelope or completed step that started
        this chain (see :meth:`_persist_and_dispatch`).
        """
        while True:
            if run.pending_steps:
                logger.debug(
                    "internal chain waits for %d pending step(s)",
                    len(run.pending_steps),
                    extra={"case_id": run.snapshot.case_id},
                )
                return
            ctx = await self._refresh_gate_derived_ctx(run, ctx, session)
            candidate = self._first_internal_trigger(
                run.state, ctx, run.dispatched_in_state
            )
            if candidate is None:
                return
            result = apply(run.state, candidate, ctx)
            run = await self._persist_and_dispatch(
                run, candidate, ctx, result, None, origin, {}, session
            )

    # -- harness steps (ADR-28) ------------------------------------------

    async def _enqueue_step_for(
        self,
        run: CaseRun,
        trigger: Trigger,
        message_id: Optional[str],
        origin: str,
        payload: dict[str, Any],
        session: AsyncClientSession,
    ) -> CaseRun:
        """Enqueue the step ``trigger`` maps to, if any, in this transaction."""
        kind = self._step_map.get(trigger)
        if kind is None:
            return run
        trigger_id = f"{origin}/{trigger.value}" if message_id is None else message_id
        args = {"trigger": trigger.value, "message_id": message_id, "payload": payload}
        return await self._enqueue_step(run, kind, trigger_id, args, session)

    async def _enqueue_step(
        self,
        run: CaseRun,
        kind: StepKind,
        trigger_id: str,
        args: Mapping[str, Any],
        session: AsyncClientSession,
    ) -> CaseRun:
        """Mark a step pending and publish its ``harness.step`` through the
        outbox, both in ``session``'s transaction. The id is
        ``deterministic_id("step", run_id, kind, trigger_id)``: the same
        triggering message always yields the same step.
        """
        run_id, case_id = run.snapshot.run_id, run.snapshot.case_id
        step_id = deterministic_id("step", run_id, kind.value, trigger_id)
        await self._runs.add_pending_step(run_id, step_id, session=session)
        body = HarnessStep(
            step_id=step_id, step=kind, case_id=case_id, run_id=run_id, args=dict(args)
        )
        envelope = build_envelope(
            "harness.step",
            body.model_dump(mode="json"),
            case_id=case_id,
            run_id=run_id,
        )
        await self._outbox.enqueue(session, envelope)
        if step_id in run.pending_steps:
            return run
        return dataclasses.replace(run, pending_steps=(*run.pending_steps, step_id))

    async def complete_step(
        self,
        run_id: str,
        step_id: str,
        result: StepResult,
        *,
        step: Optional[StepKind] = None,
    ) -> None:
        """Close a harness step in ONE short transaction, exactly once.

        In that transaction: record the ``step_done`` run event (a step that
        already has one is a no-op, so redelivery and replay are safe), drop
        the step from ``pending_steps``, open each gate in ``result`` with a
        deterministic id (an existing gate is left alone and announced no
        second time), publish the agent jobs, enqueue the follow-up steps,
        announce the UI events, merge refs, bump counters, record the band,
        and finally resume the internal chain (which proceeds only if no step
        is still pending). The transitions' metrics are recorded after the
        transaction commits (:meth:`flush_metrics`).

        Args:
            run_id: The run the step belongs to.
            step_id: The step's deterministic id (``HarnessStep.step_id``).
            result: What the handler asked for.
            step: The step's kind, kept on the ``step_done`` row for the
                reader of ``run_events``.

        Raises:
            ValueError: the engine was built without a ``transact``.
            OrphanEventError: no run ``run_id``.
            fcrm_common.events.UnknownEventTypeError: a ``UiEvent`` names an
                uncatalogued type (the whole transaction rolls back).
            pymongo.errors.PyMongoError: Mongo failed; the step is retried.
        """
        if self._transact is None:
            raise ValueError(
                "complete_step needs the engine's transact (fcrm_caseflow.engine"
                ".mongo_transact) to open its transaction"
            )

        async def work(session: AsyncClientSession) -> None:
            _begin_metrics_attempt()
            await self._complete_step_in(run_id, step_id, result, step, session)

        await self._transact(work)
        # Reached only once the transaction committed. A failed attempt's
        # observations stay unflushed and the next attempt starts empty.
        self.flush_metrics()

    async def _complete_step_in(
        self,
        run_id: str,
        step_id: str,
        result: StepResult,
        step: Optional[StepKind],
        session: AsyncClientSession,
    ) -> None:
        run = await self._runs.get(run_id, session=session)
        if run is None:
            raise OrphanEventError(f"step {step_id} names run {run_id}, which is gone")
        if await self._runs.has_step_done(run_id, step_id, session=session):
            logger.info(
                "step already completed; nothing to apply",
                extra={"case_id": run.snapshot.case_id, "step_id": step_id},
            )
            return
        run = await self._runs.record_step_done(
            run, step_id, step.value if step else "unknown", session=session
        )
        remaining = await self._runs.remove_pending_step(
            run_id, step_id, session=session
        )
        run = dataclasses.replace(run, pending_steps=tuple(remaining))
        run = await self._apply_step_result(run, step_id, result, session)
        ctx = TransitionContext(flags=run.snapshot.flags, band=run.band)
        await self._chain_internal(run, ctx, session, step_id)

    async def _apply_step_result(
        self,
        run: CaseRun,
        step_id: str,
        result: StepResult,
        session: AsyncClientSession,
    ) -> CaseRun:
        for spec in result.gates_to_open:
            await self._open_step_gate(run, spec, session)
        for close in result.gates_to_close:
            await self._close_step_gate(run, close, session)
        refusal = await self._dispatch_step_jobs(run, result, session)
        for index, follow_up in enumerate(result.follow_up_steps):
            run = await self._enqueue_step(
                run,
                follow_up.kind,
                f"{step_id}/follow/{index}",
                follow_up.args,
                session,
            )
        for event in result.ui_events:
            await self._outbox.enqueue(
                session,
                build_envelope(
                    event.event_type,
                    event.payload,
                    case_id=run.snapshot.case_id,
                    run_id=run.snapshot.run_id,
                ),
            )
        await self._runs.merge_refs(
            run.snapshot.run_id, result.refs_patch, session=session
        )
        await self._runs.bump_counters(
            run.snapshot.run_id, result.counter_deltas, session=session
        )
        # The chain that follows reads this run object, and the jobs it
        # dispatches take their refs from it (``dispatch_refs``): the patch just
        # written must be in it, or the first job after a step runs without
        # what the step was there to leave (the screening job and the domains).
        run = dataclasses.replace(
            run,
            refs={**run.refs, **result.refs_patch},
            counters={
                **run.counters,
                **{
                    k: run.counters.get(k, 0) + v
                    for k, v in result.counter_deltas.items()
                },
            },
        )
        if result.band is not None and result.band != run.band:
            await self._runs.set_band(run.snapshot.run_id, result.band, session=session)
            run = dataclasses.replace(run, band=result.band)
        if refusal is not None:
            run = await self._hand_off(run, refusal, step_id, session)
        return run

    async def _open_step_gate(
        self, run: CaseRun, spec: GateSpec, session: AsyncClientSession
    ) -> None:
        """Open ``spec`` with ``deterministic_id("gate", run, type, subject)``;
        a gate that already exists is left alone and not announced again.
        """
        gate_id = deterministic_gate_id(
            run.snapshot.run_id, spec.gate_type, spec.subject_key
        )
        required_role = REQUIRED_ROLE[spec.gate_type]
        gate_case_id = spec.case_id or run.snapshot.case_id
        document: Document = gate_document(
            gate_id=gate_id,
            case_id=gate_case_id,
            run_id=run.snapshot.run_id,
            gate_type=spec.gate_type,
            required_role=required_role,
            subject=dict(spec.subject),
        )
        if not await self._gates.open_if_absent(document, session=session):
            logger.info(
                "gate already open; not announced again",
                extra={"case_id": run.snapshot.case_id, "gate_id": gate_id},
            )
            return
        await self._announce_gate_opened(
            run,
            gate_id,
            spec.gate_type,
            required_role,
            spec.subject,
            session,
            case_id=gate_case_id,
        )

    async def _close_step_gate(
        self, run: CaseRun, spec: GateCloseSpec, session: AsyncClientSession
    ) -> None:
        """Close the gate ``spec`` names if it is still open (a gate that was
        never opened, or is already decided, is left alone): a step that
        supersedes an item or a pack ends the gate that item or pack had.
        """
        gate_id = deterministic_gate_id(
            run.snapshot.run_id, spec.gate_type, spec.subject_key
        )
        closed = await self._gates.close_if_open(
            gate_id,
            status=spec.status,
            decision=spec.decision,
            reason=spec.reason,
            session=session,
        )
        logger.info(
            "gate closed by a step" if closed else "gate not open; nothing to close",
            extra={"case_id": run.snapshot.case_id, "gate_id": gate_id},
        )

    async def _dispatch_step_jobs(
        self, run: CaseRun, result: StepResult, session: AsyncClientSession
    ) -> Optional[BudgetRefusal]:
        """Publish the jobs a step asked for, or return the refusal of the case's
        token cap (nothing published, no join recorded).
        """
        jobs = result.agent_jobs_to_dispatch
        envelopes = [
            build_job_envelope(job.dispatch, run, refs=job.refs, pins=job.pins)
            for job in jobs
        ]
        dispatches = [job.dispatch for job in jobs]
        refusal = await self._publish_jobs(run, dispatches, envelopes, session)
        if refusal is not None:
            return refusal
        if len(jobs) > 1:
            await self._runs.set_pending_agents(
                run.snapshot.run_id,
                _joined(dispatches, envelopes),
                session=session,
            )
        return None

    # -- the per-case token ledger (ADR-14, ADR-27) ----------------------------

    async def _publish_jobs(
        self,
        run: CaseRun,
        dispatches: Sequence[AgentDispatch],
        envelopes: Sequence[Envelope],
        session: AsyncClientSession,
    ) -> Optional[BudgetRefusal]:
        """Reserve one attempt of each job's tier budget against the case's
        ledger, then publish the jobs, in the caller's transaction. All or none:
        if the case cannot pay for every metered job nothing is published and the
        refusal comes back. Without a ledger every job is published.
        """
        requests = [
            JobRequest(
                job_id=str(envelope.payload["job_id"]),
                agent=dispatch.agent,
                tokens=reservation_tokens(dispatch.agent),
            )
            for dispatch, envelope in zip(dispatches, envelopes, strict=True)
            if dispatch.agent not in UNMETERED_AGENTS
        ]
        if self._budgets is not None and requests:
            outcome = await self._budgets.reserve(
                run.snapshot.case_id, run.snapshot.run_id, requests, session=session
            )
            if not outcome.granted:
                return BudgetRefusal(tuple(requests), outcome.asked, outcome.ledger)
        for envelope in envelopes:
            await self._outbox.enqueue(session, envelope)
        return None

    async def _settle_result(
        self, envelope: Envelope, run: CaseRun, session: AsyncClientSession
    ) -> None:
        """Settle the ledger for an ``agent.done`` or ``agent.failed``: drop the
        job's reservation and add the tokens its whole run used (refused attempts
        included), once per job id. Any other event is left alone.

        A result with no ``job_id`` cannot be matched to a reservation (H2 never
        sends one without it): it is logged and left unsettled, and its
        resolution goes on as before.

        Raises:
            KeyError: the payload names no ``agent``.
            ValueError: ``agent`` is not an agent name.
        """
        if self._budgets is None or envelope.type not in _AGENT_RESULT_TYPES:
            return
        payload = envelope.payload
        job_id = payload.get("job_id")
        if not isinstance(job_id, str):
            logger.warning(
                "agent result without a job id; the case ledger is not settled",
                extra={"case_id": run.snapshot.case_id, "type": envelope.type},
            )
            return
        usage = payload.get("usage")
        outcome = await self._budgets.settle(
            run.snapshot.case_id,
            run.snapshot.run_id,
            job_id,
            agent_done_agent(envelope),
            usage_tokens(usage) if isinstance(usage, Mapping) else 0,
            session=session,
        )
        if not outcome.applied:
            logger.info(
                "agent result already settled; ledger unchanged",
                extra={"case_id": run.snapshot.case_id, "job_id": job_id},
            )

    async def _hand_off(
        self,
        run: CaseRun,
        refusal: BudgetRefusal,
        origin: str,
        session: AsyncClientSession,
    ) -> CaseRun:
        """Open the human handoff for a dispatch the cap refused.

        An audit record ``budget.exhausted`` (and the same facts on the ledger,
        which outlive a down audit service), then the transition a failed job
        takes (``AGENT_FAILED_AFTER_RETRY``: any state but CLOSED to HANDOFF)
        with an ``agent.failed`` payload naming the first refused job, so the
        handoff gate and its steps see the same shape as for any failed agent.
        Results already computed are untouched: nothing here deletes or
        supersedes one.
        """
        ledger = refusal.ledger
        first = refusal.first
        reason = (
            f"budget_exhausted: {first.agent.value} needs {refusal.asked} tokens, "
            f"{ledger.spent} spent and {ledger.reserved} reserved of {ledger.cap}"
        )
        logger.warning(
            "case token cap refused a dispatch; handing off",
            extra={"case_id": run.snapshot.case_id, "agent": first.agent.value},
        )
        if self._budgets is not None:
            await self._budgets.record_refusal(
                run.snapshot.case_id, refusal, session=session
            )
        await self._write_audit_raw(
            case_id=run.snapshot.case_id,
            origin=origin,
            trigger_value="budget.exhausted",
            actor="system",
            summary=f"Guard 3 · case token budget exhausted: {reason}",
            data={
                "run_id": run.snapshot.run_id,
                "agent": first.agent.value,
                "agents": [job.agent.value for job in refusal.jobs],
                "job_ids": [job.job_id for job in refusal.jobs],
                "requested": refusal.asked,
                "spent": ledger.spent,
                "reserved": ledger.reserved,
                "cap": ledger.cap,
            },
        )
        trigger = Trigger.AGENT_FAILED_AFTER_RETRY
        ctx = TransitionContext(flags=run.snapshot.flags, band=run.band)
        try:
            result = apply(run.state, trigger, ctx)
        except IllegalTransition:
            logger.error(
                "budget refusal in a state with no handoff; the run stays put",
                extra={"case_id": run.snapshot.case_id, "state": run.state.value},
            )
            return run
        failed = AgentFailed(
            job_id=first.job_id,
            case_id=run.snapshot.case_id,
            run_id=run.snapshot.run_id,
            agent=first.agent,
            attempt=0,
            reason=reason,
        ).model_dump(mode="json")
        return await self._persist_and_dispatch(
            run, trigger, ctx, result, None, origin, failed, session
        )

    @staticmethod
    def _first_internal_trigger(
        state: CaseState, ctx: TransitionContext, already_dispatched: tuple[str, ...]
    ) -> Optional[Trigger]:
        for trigger in Trigger:
            if not trigger.value.startswith(_INTERNAL_PREFIX):
                continue
            if trigger.value in already_dispatched:
                continue
            try:
                apply(state, trigger, ctx)
            except IllegalTransition:
                continue
            return trigger
        return None

    async def _refresh_gate_derived_ctx(
        self,
        run: CaseRun,
        ctx: TransitionContext,
        session: AsyncClientSession,
    ) -> TransitionContext:
        case_id, run_id = run.snapshot.case_id, run.snapshot.run_id
        acks_open, items_open, gaps_open = (
            await self._gates.count_open(
                case_id, GateType.ACK, run_id=run_id, session=session
            ),
            await self._gates.count_open(
                case_id, GateType.REVIEW_ITEM, run_id=run_id, session=session
            ),
            await self._gates.count_open(
                case_id, GateType.PO_ANSWER_GAPS, run_id=run_id, session=session
            ),
        )
        return dataclasses.replace(
            ctx,
            acks_done=acks_open == 0,
            all_items_decided=items_open == 0,
            gaps_open=gaps_open > 0,
        )

    # -- audit ----------------------------------------------------------

    def _step_writes_record(self, trigger: Trigger) -> bool:
        """Whether the step ``trigger`` enqueues writes a record of the
        trigger's own type, in which case the engine's generic record of that
        type would only duplicate it.
        """
        kind = self._step_map.get(trigger)
        return kind is not None and trigger.value in self._records_for.get(kind, ())

    async def _write_audit(
        self,
        run: CaseRun,
        trigger: Trigger,
        result: TransitionResult,
        origin: str,
    ) -> None:
        if self._step_writes_record(trigger):
            return
        await self._write_audit_raw(
            case_id=run.snapshot.case_id,
            origin=f"{origin}/{result.from_state.value}/{result.to_state.value}",
            trigger_value=trigger.value,
            actor="system",
            summary=result.action,
            data={
                "run_id": run.snapshot.run_id,
                "state_from": result.from_state.value,
                "state_to": result.to_state.value,
            },
        )

    async def _write_audit_raw(
        self,
        *,
        case_id: str,
        origin: str,
        trigger_value: str,
        actor: str,
        summary: str,
        data: dict[str, Any],
    ) -> None:
        """Best-effort: a slow/down S4 degrades audit completeness, never
        job delivery or state progress (mirrors
        :meth:`fcrm_agent_worker.handler.AgentJobConsumer._write_audit`
        exactly, including the rationale in its docstring).

        This runs inside the Mongo transaction (a known ADR-28 deviation: the
        record must not be lost when the transaction commits), and
        ``with_transaction`` re-runs a transaction on a transient error. The
        ``Idempotency-Key`` is derived from ``origin`` (the message or step that
        led here) and the record type, so a re-run sends the key it sent the
        first time and S4 returns the record it already chained.
        """
        try:
            await self._audit.append(
                RecordAppend(
                    case_id=case_id,
                    type=trigger_value,
                    actor=actor,
                    summary=summary,
                    data=data,
                ),
                idempotency_key=deterministic_id(
                    "idem", "engine", origin, trigger_value
                ),
            )
        except Exception:  # noqa: BLE001 - audit must never crash the engine
            logger.exception(
                "audit write failed; state already committed",
                extra={"case_id": case_id, "type": trigger_value, "origin": origin},
            )
