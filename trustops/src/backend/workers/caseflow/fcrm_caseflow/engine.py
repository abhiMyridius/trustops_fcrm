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
from typing import Any, Optional, Protocol

from fcrm_common.events import build_envelope
from fcrm_common.ids import deterministic_id, new_id
from fcrm_common.mongo import Document, with_transaction
from fcrm_common.outbox import Outbox
from fcrm_contracts.api.audit import RecordAppend
from fcrm_contracts.common import GateDecision, Principal
from fcrm_contracts.enums import AgentName, CaseState, GateType, Role, StepKind
from fcrm_contracts.events.envelope import Envelope
from fcrm_contracts.events.payloads import GateOpened, HarnessStep
from fcrm_guardrails.base import AUDIT_TYPES, GuardName, GuardResult, GuardStatus
from pymongo import AsyncMongoClient
from pymongo.asynchronous.client_session import AsyncClientSession

from fcrm_caseflow.audit_client import AuditClient
from fcrm_caseflow.audit_map import RECORDS_FOR, STEP_FOR
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

_INTERNAL_PREFIX = "internal."

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
        if envelope.type == "case.created":
            await self._open_run(envelope, session)
            return
        run = await self._load_run(envelope, session)
        if run is None:
            raise OrphanEventError(
                f"{envelope.type} for case={envelope.case_id} run={envelope.run_id} "
                "has no open run"
            )
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

    # -- resolving a trigger ------------------------------------------

    async def _resolve(
        self,
        envelope: Envelope,
        run: CaseRun,
        session: AsyncClientSession,
    ) -> Optional[tuple[Trigger, TransitionContext]]:
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

    async def _resolve_gate_decided(
        self,
        envelope: Envelope,
        run: CaseRun,
        session: AsyncClientSession,
    ) -> Optional[tuple[Trigger, TransitionContext]]:
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
        gate_type = GateType(gate_doc["gate_type"])
        principal = Principal(
            sub=decision.decided_by,
            roles=[Role(gate_doc["required_role"])],
            name=decision.decided_by,
            typ="access",
        )
        await self._gates.decide(decision, principal, session=session)
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
        if self._results is not None:
            # Kept before anything else: the proposals exist nowhere else, and
            # a step that merges several jobs reads them back from here.
            await self._results.save(envelope.payload, session=session)
        state = run.state
        job_id = envelope.payload.get("job_id")
        cleared = await self._clear_join_key(run, agent, job_id, session)
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
        if ctx.band is not None and ctx.band != run.band:
            await self._runs.set_band(run.snapshot.run_id, ctx.band, session=session)
            run = dataclasses.replace(run, band=ctx.band)
        await self._dispatch(run, trigger, ctx, payload, session)
        run = await self._enqueue_step_for(
            run, trigger, message_id, origin, payload, session
        )
        await self._write_audit(run, trigger, result)
        return run

    async def _dispatch(
        self,
        run: CaseRun,
        trigger: Trigger,
        ctx: TransitionContext,
        payload: dict[str, Any],
        session: AsyncClientSession,
    ) -> None:
        plan: DispatchPlan = dispatch_for(trigger, ctx, run.snapshot.flags, payload)
        envelopes = [
            build_job_envelope(job, run, refs=dispatch_refs(job, run.refs))
            for job in plan.agents
        ]
        for env in envelopes:
            await self._outbox.enqueue(session, env)
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
        if gate_type is not None:
            await self._open_gate(run, gate_type, session)

    async def _open_gate(
        self,
        run: CaseRun,
        gate_type: GateType,
        session: AsyncClientSession,
    ) -> None:
        """Open the gate a transition calls for. The three gates a step must
        know the id of (:func:`~fcrm_caseflow.dispatch.trigger_gate_key`) get a
        deterministic id and are opened at most once (a replayed trigger finds
        the gate it opened); any other gets a fresh one.
        """
        key = trigger_gate_key(gate_type, run)
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
            run, gate_id, gate_type, required_role, {}, session
        )

    async def _announce_gate_opened(
        self,
        run: CaseRun,
        gate_id: str,
        gate_type: GateType,
        required_role: Role,
        subject: Mapping[str, str],
        session: AsyncClientSession,
    ) -> None:
        opened = GateOpened(
            gate_id=gate_id,
            gate_type=gate_type,
            case_id=run.snapshot.case_id,
            run_id=run.snapshot.run_id,
            subject=dict(subject),
            required_role=required_role.value,
        )
        env = build_envelope(
            "gate.opened",
            opened.model_dump(mode="json"),
            case_id=run.snapshot.case_id,
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
        is still pending).

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
            await self._complete_step_in(run_id, step_id, result, step, session)

        await self._transact(work)

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
        await self._dispatch_step_jobs(run, result, session)
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
        document: Document = gate_document(
            gate_id=gate_id,
            case_id=run.snapshot.case_id,
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
            run, gate_id, spec.gate_type, required_role, spec.subject, session
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
    ) -> None:
        jobs = result.agent_jobs_to_dispatch
        envelopes = [
            build_job_envelope(job.dispatch, run, refs=job.refs) for job in jobs
        ]
        for env in envelopes:
            await self._outbox.enqueue(session, env)
        if len(jobs) > 1:
            await self._runs.set_pending_agents(
                run.snapshot.run_id,
                _joined([job.dispatch for job in jobs], envelopes),
                session=session,
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
        self, run: CaseRun, trigger: Trigger, result: TransitionResult
    ) -> None:
        if self._step_writes_record(trigger):
            return
        await self._write_audit_raw(
            case_id=run.snapshot.case_id,
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
        trigger_value: str,
        actor: str,
        summary: str,
        data: dict[str, Any],
    ) -> None:
        """Best-effort: a slow/down S4 degrades audit completeness, never
        job delivery or state progress (mirrors
        :meth:`fcrm_agent_worker.handler.AgentJobConsumer._write_audit`
        exactly, including the rationale in its docstring).
        """
        try:
            await self._audit.append(
                RecordAppend(
                    case_id=case_id,
                    type=trigger_value,
                    actor=actor,
                    summary=summary,
                    data=data,
                )
            )
        except Exception:  # noqa: BLE001 - audit must never crash the engine
            logger.exception(
                "audit write failed; state already committed",
                extra={"case_id": case_id, "type": trigger_value},
            )
