"""H1's step mechanism (ADR-28): HTTP-side work, run outside the transaction.

**Why steps exist.** :meth:`fcrm_caseflow.engine.CaseflowEngine.handle` runs
inside the consumer's Mongo transaction, and ``with_transaction`` may re-run
that callback, so nothing slow or non-idempotent (an HTTP call to S1/S2/S3/
S5, an S4 audit record) may happen there. Instead, every trigger that needs
such work enqueues one ``harness.step`` message through the outbox *in the
same transaction as the transition* (the engine's ``step_map``,
:data:`fcrm_caseflow.audit_map.STEP_FOR`). A separate consumer
(:class:`fcrm_caseflow.consumers.HarnessStepConsumer`, queue
``caseflow.steps``) hands it to :class:`StepRunner`, which runs the matching
handler outside any transaction and then finishes with
``CaseflowEngine.complete_step`` in one short transaction. While a step is
pending the engine does not chain internal triggers: the step's result (gates
opened, agents dispatched) is part of what those triggers' guards read.

**What a handler is.** An ``async`` function ``(HarnessStep, StepDeps) ->
StepResult``. Everything it does that touches the outside world (HTTP, S4)
is its own business, but it must be safe to run again: the broker redelivers
a message whose handler failed, and the whole step reruns. :meth:`StepDeps
.append_record` makes the audit writes idempotent by deriving the
``Idempotency-Key`` from the step id. The handler does not write Mongo and
does not publish: it *describes* what should follow in a :class:`StepResult`
and the engine applies that atomically (gates with deterministic ids, agent
jobs, follow-up steps, refs), so a crash anywhere before the commit changes
nothing and a second run of a finished step is a no-op.

**Failure.** :class:`StepRunner` catches only :class:`~fcrm_common.errors
.FcrmError` subclasses, ``httpx`` timeouts and ``pymongo`` errors (what an
upstream or the database can throw), logs them with ``case_id`` / ``run_id``
/ ``step_id`` / ``step`` and re-raises, so :meth:`fcrm_common.events
.Consumer.process` retries and finally dead-letters the message. Nothing is
swallowed and no step is ever marked done that did not finish.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, ClassVar, Final, Optional, Protocol

import httpx
from fcrm_common.errors import FcrmError, NotFoundError, UpstreamTimeoutError
from fcrm_common.ids import deterministic_id
from fcrm_common.logging import bound_context
from fcrm_contracts.api.audit import AuditRecord, RecordAppend
from fcrm_contracts.enums import GateType, StepKind
from fcrm_contracts.events.payloads import HarnessStep
from pymongo.errors import PyMongoError

from fcrm_caseflow.audit_client import AuditClient
from fcrm_caseflow.dispatch import AgentDispatch
from fcrm_caseflow.store import CaseRun, CaseRunStore

logger = logging.getLogger(__name__)

#: What a step can legitimately fail with: an upstream said no or did not
#: answer, or Mongo did. Anything else is a bug and propagates uncaught.
STEP_ERRORS: Final = (FcrmError, httpx.TimeoutException, PyMongoError)

#: Overall deadline for one handler call. Generous (a step may make several
#: HTTP calls and append a dozen audit records) but finite.
DEFAULT_STEP_TIMEOUT_S: Final = 120.0


class UnknownStepKindError(FcrmError):
    """A ``harness.step`` names a kind with no registered handler. Raised, never
    skipped: an unhandled step would leave the run waiting forever.
    """

    status_code: ClassVar[int] = 500
    code: ClassVar[str] = "unknown_step_kind"
    title: ClassVar[str] = "Unknown step kind"


@dataclass(frozen=True)
class GateSpec:
    """One gate a step wants opened.

    Attributes:
        gate_type: Which gate (Guard 7); its required role is fixed by
            :data:`fcrm_caseflow.dispatch.REQUIRED_ROLE`.
        subject_key: Identifies this gate among the run's gates of the same
            type, e.g. a control id or ``"<proposal id>:<item id>"``. With the
            run id and gate type it becomes the gate's deterministic id, so a
            rerun of the step opens nothing twice. It must differ for every
            gate that should exist separately: reusing a key after the first
            gate was decided finds that gate and opens no new one.
        subject: ``{kind, id}`` shown to the UI (``GateOpened.subject``).
    """

    gate_type: GateType
    subject_key: str
    subject: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentJobSpec:
    """One ``agent.run`` a step wants dispatched.

    Attributes:
        dispatch: Which agent, scope and changed fields.
        refs: Becomes ``JobPayload.refs``: the context-pack inputs
            (:class:`fcrm_agents.context_pack.BuildRequest` fields) H2 cannot
            derive itself. References only.
    """

    dispatch: AgentDispatch
    refs: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FollowUpStep:
    """Another step to run after this one, in the same transaction that
    completes it (its id derives from the parent's, so a replay re-derives it).
    """

    kind: StepKind
    args: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class UiEvent:
    """An extra catalogued event for S1's stream (``score.computed``,
    ``critic.findings``...), enqueued through the outbox with the case and
    run ids filled in.

    Attributes:
        event_type: A member of ``fcrm_contracts.events.catalogue
            .EVENT_TYPES``; anything else fails the step.
        payload: The event body.
    """

    event_type: str
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StepResult:
    """What a finished step asks the engine to do, atomically, in
    :meth:`~fcrm_caseflow.engine.CaseflowEngine.complete_step`. Every field
    defaults to "nothing", so a step that only had side effects returns
    ``StepResult()``.

    Attributes:
        gates_to_open: Gates to open, each with a deterministic id (see
            :class:`GateSpec`); one ``gate.opened`` is announced per gate
            actually created.
        agent_jobs_to_dispatch: ``agent.run`` jobs to publish. More than one
            makes the run wait for every one of them (the research and
            assessment join).
        refs_patch: Keys to merge into ``CaseRun.refs``, for later steps.
        follow_up_steps: Steps to enqueue next.
        ui_events: Extra catalogued events to announce.
        band: The band (1-4) S2 returned, if this step scored the case.
            Persisted on the run so R1/R2 routing can read it later.
        counter_deltas: Amounts to add to ``CaseRun.counters``
            (``agents_run``, ``tokens_used``...).
    """

    gates_to_open: tuple[GateSpec, ...] = ()
    agent_jobs_to_dispatch: tuple[AgentJobSpec, ...] = ()
    refs_patch: Mapping[str, Any] = field(default_factory=dict)
    follow_up_steps: tuple[FollowUpStep, ...] = ()
    ui_events: tuple[UiEvent, ...] = ()
    band: Optional[int] = None
    counter_deltas: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class StepDeps:
    """What a handler may use. Later lanes add the typed S1/S2/S3/S5 clients
    here as optional fields; the run is a snapshot taken when the step began.

    Attributes:
        run: The run as loaded just before the handler started.
        audit: The S4 client.
    """

    run: CaseRun
    audit: AuditClient

    async def append_record(
        self,
        step: HarnessStep,
        record_type: str,
        summary: str,
        data: Optional[Mapping[str, Any]] = None,
        *,
        actor: str = "system",
        ordinal: int = 0,
    ) -> AuditRecord:
        """Append one audit record for ``step``, safely repeatable.

        The ``Idempotency-Key`` is derived from the step id, the record type
        and ``ordinal``, so a retried step re-sends the same keys and S4 can
        return the record it already wrote instead of chaining a duplicate.
        Give records of the same type within one step distinct ``ordinal``
        values (three ``guard.input`` records: 0, 1, 2).

        Raises:
            fcrm_common.errors.FcrmError: S4 refused or was unreachable. The
                step fails and is retried: unlike the engine's per-transition
                audit, a step's records are not best-effort.
        """
        key = deterministic_id("idem", step.step_id, record_type, str(ordinal))
        return await self.audit.append(
            RecordAppend(
                case_id=step.case_id,
                type=record_type,
                actor=actor,
                summary=summary,
                data={"run_id": step.run_id, "step_id": step.step_id, **(data or {})},
            ),
            idempotency_key=key,
        )


StepHandler = Callable[[HarnessStep, StepDeps], Awaitable[StepResult]]


class StepRegistry:
    """Which handler runs which :class:`~fcrm_contracts.enums.StepKind`.

    Args:
        handlers: The initial table; injectable so a test or a later lane
            supplies its own.
    """

    def __init__(self, handlers: Optional[Mapping[StepKind, StepHandler]] = None):
        """Start from a copy of ``handlers`` (empty when omitted)."""
        self._handlers: dict[StepKind, StepHandler] = dict(handlers or {})

    def register(self, kind: StepKind, handler: StepHandler) -> None:
        """Add ``handler`` for ``kind``.

        Raises:
            ValueError: ``kind`` already has a handler (replacing one silently
                would hide a wiring mistake).
        """
        if kind in self._handlers:
            raise ValueError(f"a handler is already registered for step {kind.value!r}")
        self._handlers[kind] = handler

    def get(self, kind: StepKind) -> StepHandler:
        """The handler for ``kind``.

        Raises:
            UnknownStepKindError: no handler is registered.
        """
        handler = self._handlers.get(kind)
        if handler is None:
            raise UnknownStepKindError(
                f"no handler is registered for step {kind.value!r}",
                step=kind.value,
            )
        return handler

    @property
    def kinds(self) -> frozenset[StepKind]:
        """The kinds that have a handler."""
        return frozenset(self._handlers)

    def require(self, kinds: Iterable[StepKind]) -> None:
        """Check, at startup, that every kind in ``kinds`` has a handler.

        A trigger wired to a step nobody can run would enqueue it, fail it
        three times and park it, leaving the run waiting; better to refuse to
        start.

        Raises:
            UnknownStepKindError: one or more kinds have no handler; the
                message names them all.
        """
        missing = sorted(k.value for k in set(kinds) - self.kinds)
        if missing:
            raise UnknownStepKindError(
                "step kinds wired to a trigger have no handler: " + ", ".join(missing),
                steps=missing,
            )


class StepCompleter(Protocol):
    """What :class:`StepRunner` needs to finish a step: satisfied by
    :class:`fcrm_caseflow.engine.CaseflowEngine`, declared here so this module
    never imports the engine (which imports this module's result types).
    """

    async def complete_step(
        self,
        run_id: str,
        step_id: str,
        result: StepResult,
        *,
        step: Optional[StepKind] = None,
    ) -> None:
        """Apply ``result`` and mark ``step_id`` done, once."""
        ...


class _CaseLocks:
    """One :class:`asyncio.Lock` per case, forgotten when nobody uses it, so
    a long-lived process does not keep a lock for every case it ever saw.
    """

    def __init__(self) -> None:
        self._entries: dict[str, tuple[asyncio.Lock, int]] = {}

    @asynccontextmanager
    async def hold(self, case_id: str) -> AsyncIterator[None]:
        """Hold ``case_id``'s lock for the ``async with`` body."""
        lock, users = self._entries.get(case_id, (asyncio.Lock(), 0))
        self._entries[case_id] = (lock, users + 1)
        try:
            async with lock:
                yield
        finally:
            lock, users = self._entries[case_id]
            if users <= 1:
                del self._entries[case_id]
            else:
                self._entries[case_id] = (lock, users - 1)

    def __len__(self) -> int:
        return len(self._entries)


class StepRunner:
    """Runs one ``harness.step``: idempotent, serialised per case, then
    completed through the engine.

    Args:
        runs: Reads the run and the ``step_done`` marker (no session: a step
            runs outside any transaction).
        registry: The handlers.
        completer: The engine's ``complete_step``.
        audit: Handed to every handler through :class:`StepDeps`.
        timeout_s: The deadline for one handler call, in seconds.
    """

    def __init__(
        self,
        runs: CaseRunStore,
        registry: StepRegistry,
        completer: StepCompleter,
        audit: AuditClient,
        *,
        timeout_s: float = DEFAULT_STEP_TIMEOUT_S,
    ) -> None:
        """Wire the runner's collaborators; it holds no other state."""
        self._runs = runs
        self._registry = registry
        self._completer = completer
        self._audit = audit
        self._timeout_s = timeout_s
        self._locks = _CaseLocks()

    async def run(self, step: HarnessStep) -> None:
        """Run ``step`` to completion, or fail loudly.

        A step whose ``step_done`` row already exists (a redelivery, or a
        second replica) returns immediately without calling its handler. The
        per-case lock is held from that check through ``complete_step``, so
        two steps of one case never interleave while different cases run
        concurrently.

        Raises:
            fcrm_common.errors.NotFoundError: the run does not exist.
            UnknownStepKindError: no handler is registered for ``step.step``.
            fcrm_common.errors.FcrmError: the handler (or the engine) failed
                on an upstream or business error.
            fcrm_common.errors.UpstreamTimeoutError: the handler ran past the
                runner's deadline.
            httpx.TimeoutException: a handler's own HTTP call timed out.
            pymongo.errors.PyMongoError: Mongo failed.
            Every one is logged with its context and re-raised for the
            broker's retry and dead-letter path.
        """
        with bound_context(
            case_id=step.case_id,
            run_id=step.run_id,
            step_id=step.step_id,
            step=step.step.value,
        ):
            async with self._locks.hold(step.case_id):
                await self._run_locked(step)

    async def _run_locked(self, step: HarnessStep) -> None:
        try:
            run = await self._runs.get(step.run_id)
            if run is None:
                raise NotFoundError(f"no run {step.run_id} for step {step.step_id}")
            if await self._runs.has_step_done(step.run_id, step.step_id):
                logger.info("step already done; skipping")
                return
            handler = self._registry.get(step.step)
            result = await self._call_handler(handler, step, run)
            await self._completer.complete_step(
                step.run_id, step.step_id, result, step=step.step
            )
        except STEP_ERRORS as exc:
            logger.warning(
                "step failed; the broker will retry it",
                exc_info=exc,
                extra={"error": {"type": type(exc).__name__}},
            )
            raise
        logger.info("step done")

    async def _call_handler(
        self, handler: StepHandler, step: HarnessStep, run: CaseRun
    ) -> StepResult:
        """Run ``handler`` under the step's overall deadline. A handler's own
        calls have their own timeouts; this is the backstop that keeps one
        hung step from holding the case lock (and so every later step of the
        case) for ever.
        """
        try:
            async with asyncio.timeout(self._timeout_s) as deadline:
                return await handler(step, StepDeps(run=run, audit=self._audit))
        except TimeoutError as exc:
            if not deadline.expired():
                raise  # the handler's own timeout, not this deadline
            raise UpstreamTimeoutError(
                f"step {step.step.value} ran longer than {self._timeout_s:g}s",
                step=step.step.value,
            ) from exc
