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
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Any, Optional

from fcrm_common.events import build_envelope
from fcrm_common.ids import new_id
from fcrm_common.mongo import Document
from fcrm_common.outbox import Outbox
from fcrm_contracts.api.audit import RecordAppend
from fcrm_contracts.common import GateDecision, Principal
from fcrm_contracts.enums import CaseState, GateType, Role
from fcrm_contracts.events.envelope import Envelope
from fcrm_contracts.events.payloads import GateOpened
from fcrm_guardrails.base import AUDIT_TYPES, GuardName, GuardResult, GuardStatus
from pymongo.asynchronous.client_session import AsyncClientSession

from fcrm_caseflow.audit_client import AuditClient
from fcrm_caseflow.dispatch import (
    REQUIRED_ROLE,
    DispatchPlan,
    build_job_envelope,
    dispatch_for,
    gate_to_open,
)
from fcrm_caseflow.exceptions import IllegalTransition
from fcrm_caseflow.flags import next_flags
from fcrm_caseflow.gates import GateDecisionError, GateStore, gate_document
from fcrm_caseflow.resolve import (
    agent_done_agent,
    agent_done_pair_members,
    agent_done_single,
    context_from_payload,
    gate_decided_trigger,
    resolve_simple,
)
from fcrm_caseflow.store import (
    CaseRun,
    CaseRunStore,
    new_case_run_document,
    pins_from_case_event_data,
)
from fcrm_caseflow.transitions import TransitionContext, TransitionResult, apply
from fcrm_caseflow.triggers import Trigger

logger = logging.getLogger(__name__)

_INTERNAL_PREFIX = "internal."


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
    """Applies one envelope's worth of state-machine work."""

    def __init__(
        self,
        runs: CaseRunStore,
        gates: GateStore,
        outbox: Outbox,
        audit: AuditClient,
    ) -> None:
        self._runs = runs
        self._gates = gates
        self._outbox = outbox
        self._audit = audit

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
            base_ctx = TransitionContext(flags=run.snapshot.flags, band=run.band)
            await self._chain_internal(run, base_ctx, session)
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
        state = run.state
        single = agent_done_single(state, agent)
        if single is not None:
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
        remaining = await self._runs.remove_pending_agent(
            run.snapshot.run_id, agent.value, session=session
        )
        if remaining:
            return None
        return trigger, context_from_payload(envelope.payload)

    # -- applying + persisting one transition, then chaining ----------

    async def _apply_and_chain(
        self,
        run: CaseRun,
        trigger: Trigger,
        ctx: TransitionContext,
        message_id: Optional[str],
        payload: dict[str, Any],
        session: AsyncClientSession,
    ) -> None:
        ctx = dataclasses.replace(
            ctx,
            flags=run.snapshot.flags,
            band=ctx.band if ctx.band is not None else run.band,
        )
        result = apply(run.state, trigger, ctx)
        run = await self._persist_and_dispatch(
            run, trigger, ctx, result, message_id, payload, session
        )
        await self._chain_internal(run, ctx, session)

    async def _persist_and_dispatch(
        self,
        run: CaseRun,
        trigger: Trigger,
        ctx: TransitionContext,
        result: TransitionResult,
        message_id: Optional[str],
        payload: dict[str, Any],
        session: AsyncClientSession,
    ) -> CaseRun:
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
        for job in plan.agents:
            env = build_job_envelope(job, run)
            await self._outbox.enqueue(session, env)
        if len(plan.agents) > 1:
            await self._runs.set_pending_agents(
                run.snapshot.run_id,
                [job.agent.value for job in plan.agents],
                session=session,
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
        gate_id = new_id("gate")
        required_role = REQUIRED_ROLE[gate_type]
        document: Document = gate_document(
            gate_id=gate_id,
            case_id=run.snapshot.case_id,
            run_id=run.snapshot.run_id,
            gate_type=gate_type,
            required_role=required_role,
        )
        await self._gates.open(document, session=session)
        opened = GateOpened(
            gate_id=gate_id,
            gate_type=gate_type,
            case_id=run.snapshot.case_id,
            run_id=run.snapshot.run_id,
            subject={},
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
        """
        while True:
            ctx = await self._refresh_gate_derived_ctx(run, ctx, session)
            candidate = self._first_internal_trigger(
                run.state, ctx, run.dispatched_in_state
            )
            if candidate is None:
                return
            result = apply(run.state, candidate, ctx)
            run = await self._persist_and_dispatch(
                run, candidate, ctx, result, None, {}, session
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

    async def _write_audit(
        self, run: CaseRun, trigger: Trigger, result: TransitionResult
    ) -> None:
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
