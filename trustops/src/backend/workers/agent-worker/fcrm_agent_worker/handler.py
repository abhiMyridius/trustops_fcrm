"""The ``agent-worker.run`` consumer: H2's one integration point with the
bus (design A §10.6).

One ``agent.run`` message -> a claim on the job
(:class:`fcrm_agent_worker.jobs.AgentJobStore`) -> its context pack, or its
parts when the input does not fit (ADR-77) -> :func:`fcrm_agent_worker.parts
.run_agent_parts` (per part: pre-guards, the live model call, guards 4-5, with
H2's own internal retry across the tier's attempts; then the merge) ->
``agent.done`` or ``agent.failed`` (never both; never neither) in H2's outbox
-> audit records through S4.
H1 (``fcrm_caseflow``) reacts to exactly those two terminal events
(``fcrm_caseflow.triggers.Trigger.AGENT_FAILED_AFTER_RETRY`` -- "after its
one retry" -- confirms H2, not H1, owns the attempt loop); H2 never
touches case state directly.

**No transaction around the model call (ADR-79).** The consumer is built
without a Mongo client, so the base :class:`fcrm_common.events.Consumer` writes
its dedupe marker *after* :meth:`AgentJobConsumer.handle` returns rather than
inside a transaction wrapped around it (a strong-tier job outlives Mongo's 60 s
transaction limit, and an aborted transaction re-ran the job and paid Bedrock
again). The job is claimed first in ``agent_jobs`` under a lease sized to its
worst case (:func:`fcrm_agent_worker.jobs.lease_seconds`): a redelivered job
that is done is acknowledged with nothing sent, one that is still running
raises :class:`fcrm_agent_worker.jobs.JobInFlightError` into the broker's retry
and DLQ path. The terminal event is never published directly: one short
transaction marks the job done and writes the event to H2's outbox
(``agent_outbox``), which H2's own relay publishes (:meth:`AgentJobConsumer
._finish`). Metrics and the S4 audit records follow the commit, best-effort,
as before.

A guard ``BLOCKED`` verdict (any stage) is not a bug: it is reported
faithfully as ``agent.failed`` so H1 can open the human handoff gate
(design A §3.1, ``HANDOFF``) -- this consumer never retries a blocked job
itself and never marks a job done it did not actually finish.

**Audit vocabulary.** H2 writes the guard-side records of the walkthrough
seed (``demo.audit[].type``) through S4 once the terminal event is committed:
``guard.permit`` (Guard 2, with the allowed tools), one ``guard.output`` per
Guard 4 result ("<agent> attempt n: k of n claims carry sources", or the
rejection), ``agent.retry`` between two attempts, and ``agent.failed`` on a
terminal failure. There is deliberately **no** ``agent.done`` record: what an
agent proposed is recorded by H1 as ``proposal.*`` when it consumes
``agent.done``. These records carry ``job_id``, so they can interleave with
H1's for the same job; S4 orders them by arrival.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Optional, Protocol

from fcrm_agents.builders import NarrativePackBuilder, builder_for
from fcrm_agents.consolidate import needs_reduce
from fcrm_agents.context_pack import (
    BuildRequest,
    ContextPack,
    PackBuildError,
    PackOverflowError,
)
from fcrm_agents.pricing import PriceTable
from fcrm_agents.runtime import AgentRunResult, AttemptRecord, RunVerdict
from fcrm_agents.spec import AgentSpec, get_spec
from fcrm_common.errors import AuthzUnavailableError
from fcrm_common.events import (
    Consumer,
    Publisher,
    build_envelope,
    default_exchange,
)
from fcrm_common.logging import bound_context
from fcrm_common.metrics import FcrmMetrics, get_metrics
from fcrm_common.mongo import Document, with_transaction
from fcrm_common.outbox import Outbox
from fcrm_contracts.api.audit import RecordAppend
from fcrm_contracts.enums import AgentName, Tier
from fcrm_contracts.events.envelope import Envelope
from fcrm_contracts.events.payloads import AgentDone, AgentFailed, JobPayload
from fcrm_contracts.proposals.screening import ScreeningProposal
from fcrm_contracts.versions import PinnedVersions
from fcrm_guardrails import CaseBudget, GuardName, GuardResult, PermitGuard
from fcrm_guardrails.opa_client import OPAClient
from fcrm_tools import ToolError, ToolNotPermittedError, ToolRegistry, ToolScopeError
from pydantic import ValidationError
from pymongo import AsyncMongoClient
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.errors import PyMongoError

from fcrm_agent_worker.audit_client import AuditClient
from fcrm_agent_worker.job_metrics import (
    OUTCOME_CONTEXT_PACK_ERROR,
    JobUsage,
    job_outcome,
    metered_factory,
    record_job_metrics,
)
from fcrm_agent_worker.jobs import (
    AgentJobStore,
    ClaimOutcome,
    JobInFlightError,
    lease_seconds,
)
from fcrm_agent_worker.live_runtime import (
    ModelFactory,
    live_model_for_slot,
)
from fcrm_agent_worker.parts import parts_summary, run_agent_parts
from fcrm_agent_worker.raw_inputs import RawInputsSink
from fcrm_agent_worker.topology import (
    AGENT_WORKER_RUN_QUEUE,
    ROUTING_KEY_AGENT_DONE,
    ROUTING_KEY_AGENT_FAILED,
)

logger = logging.getLogger(__name__)

InstructionsFor = Callable[[AgentName, Optional[str], PinnedVersions], Optional[str]]

#: The routing key each terminal event is published with.
_TERMINAL_ROUTING_KEYS: Final[Mapping[str, str]] = {
    "agent.done": ROUTING_KEY_AGENT_DONE,
    "agent.failed": ROUTING_KEY_AGENT_FAILED,
}


class Transact(Protocol):
    """Runs ``fn(session)`` in one Mongo transaction and returns its result.

    A copy of ``fcrm_caseflow.engine.Transact`` (H2 does not import H1):
    ``fcrm_caseflow.engine.mongo_transact`` is the production implementation
    and a test passes a function that calls ``fn`` with a fake session. As
    with :func:`fcrm_common.mongo.with_transaction`, ``fn`` may run more than
    once, so it must do every write through ``session`` and nothing else.
    """

    async def __call__[R](self, fn: Callable[[AsyncClientSession], Awaitable[R]]) -> R:
        """Run ``fn`` transactionally."""
        ...


def mongo_transact(client: AsyncMongoClient[Document]) -> Transact:
    """A :class:`Transact` over ``client``'s replica set
    (:func:`fcrm_common.mongo.with_transaction`), as ``fcrm_caseflow.engine``'s.
    """

    async def transact[R](fn: Callable[[AsyncClientSession], Awaitable[R]]) -> R:
        return await with_transaction(client, fn)

    return transact


#: Design A §10.6's canonical ``AgentFailed.reason`` vocabulary, by the
#: guard that stopped the job. No table maps ``GuardName`` -> this
#: vocabulary in the built code, so this is this module's own synthesis
#: (a judgment call, not a contract) -- the full guard summary still rides
#: along in the reason string for anyone debugging a specific failure.
_FAILURE_REASON_CODE: Final[dict[GuardName, str]] = {
    GuardName.INPUT_SCAN: "guard1_blocked",
    GuardName.PERMIT: "permit_denied",
    GuardName.BUDGET: "budget",
    GuardName.OUTPUT_SHAPE: "guard4_rejected",
    GuardName.RULES: "rules_rejected",
}

#: Context-pack construction failures that are a legitimate, deterministic
#: "this job cannot run" outcome (a missing builder input, a stale pinned
#: version, a tool refusal, ``refs`` that are not ``BuildRequest`` fields) --
#: reported as ``agent.failed``, never retried by the broker, since
#: redelivering the identical message would fail the same way.
_CONTEXT_PACK_ERRORS: Final = (
    PackBuildError,
    PackOverflowError,
    ToolError,
    ToolNotPermittedError,
    ToolScopeError,
    ValidationError,
)


@dataclass(frozen=True)
class AuditEntry:
    """One S4 record H2 means to write for a job.

    Attributes:
        record_type: The seed's audit vocabulary (``guard.permit``...).
        summary: The one-line text a reader of the chain sees.
        data: Extra structured facts, merged under ``run_id``/``job_id``/
            ``agent``.
    """

    record_type: str
    summary: str
    data: Mapping[str, Any] = field(default_factory=dict)


def pack_refs(job: JobPayload) -> dict[str, Any]:
    """The ``BuildRequest`` inputs for ``job``: its ``refs`` as H1 sent them.

    A narrative job's ``scope`` is its mode (``completeness`` or
    ``recommendation``); when H1 left ``mode`` out of ``refs`` it is taken
    from ``scope``, because the builder otherwise defaults to ``completeness``
    and a recommendation job would silently run as a completeness check.
    """
    refs = dict(job.refs)
    if (
        job.agent is AgentName.NARRATIVE
        and "mode" not in refs
        and job.scope in NarrativePackBuilder.MODES
    ):
        refs["mode"] = job.scope
    return refs


def _shape_results(result: AgentRunResult, attempt_index: int) -> list[GuardResult]:
    """Guard 4 results of one attempt (a failed model call is reported under
    Guard 4's name too, see ``live_runtime.LiveRun.call_model``).
    """
    return [
        r
        for r in result.attempts[attempt_index].guard_results
        if r.guard is GuardName.OUTPUT_SHAPE
    ]


#: How many of Guard 4's reasons, and how many characters of each, the
#: ``guard.output`` record keeps: enough for an analyst or a developer to see why
#: an attempt was rejected (the first live run's narrative was, with nothing to
#: say why), never the model's whole output.
MAX_REJECTION_REASONS: Final = 5
MAX_REJECTION_REASON_CHARS: Final = 300


def _rejection(shape: GuardResult) -> dict[str, Any]:
    """Why Guard 4 did not pass an attempt: the failing items and the reasons
    it sent back, or nothing when it passed.
    """
    failed = shape.details.get("failed_items")
    if shape.passed or not failed:
        return {}
    feedback = shape.details.get("feedback") or []
    return {
        "failed_items": list(failed)[:MAX_REJECTION_REASONS],
        "reasons": [
            str(reason)[:MAX_REJECTION_REASON_CHARS]
            for reason in feedback[:MAX_REJECTION_REASONS]
        ],
    }


def _output_summary(
    agent: str, attempt: AttemptRecord, shape: GuardResult, *, split: bool
) -> str:
    """``<agent> attempt n: k of n claims carry sources`` for a result that may
    proceed, otherwise the guard's own rejection text. A split job's records
    name the part (``part i``, or ``merge`` for the reduce call).
    """
    claims, verified = shape.details.get("claims"), shape.details.get("verified")
    where = ""
    if split:
        where = "merge " if attempt.part == 0 else f"part {attempt.part} "
    if shape.passed and claims is not None and verified is not None:
        return (
            f"{agent} {where}attempt {attempt.attempt}: {verified} of {claims} "
            "claims carry sources"
        )
    return f"{agent} {where}{shape.summary}" if split else shape.summary


def _split(result: AgentRunResult) -> bool:
    """Whether the job's input was split (its attempts answered more than one
    part, or a reduce call merged them)."""
    return len({a.part for a in result.attempts}) > 1 or any(
        a.part != 1 for a in result.attempts
    )


def audit_entries(
    result: AgentRunResult, spec: AgentSpec, *, failed_reason: Optional[str] = None
) -> list[AuditEntry]:
    """The records to write for a finished job, in chain order: ``guard.permit``,
    then for each attempt its ``guard.output`` (and an ``agent.retry`` before
    the next attempt), then ``agent.failed`` on a terminal failure. Never an
    ``agent.done``.
    """
    agent = spec.name.value
    entries: list[AuditEntry] = []
    for permit in (r for r in result.pre_guards if r.guard is GuardName.PERMIT):
        tools = list(spec.tools)
        text = (
            f"Guard 2 · {agent} agent permitted: {', '.join(tools)}"
            if permit.passed
            else f"Guard 2 · {agent} agent denied: {permit.summary}"
        )
        entries.append(
            AuditEntry("guard.permit", text, {"allowed": permit.passed, "tools": tools})
        )
    split = _split(result)
    for index, attempt in enumerate(result.attempts):
        for shape in _shape_results(result, index):
            entries.append(
                AuditEntry(
                    "guard.output",
                    _output_summary(agent, attempt, shape, split=split),
                    {
                        "attempt": attempt.attempt,
                        "status": shape.status.value,
                        "claims_total": shape.details.get("claims"),
                        "claims_with_source": shape.details.get("verified"),
                        **({"part": attempt.part} if split else {}),
                        **_rejection(shape),
                    },
                )
            )
        following = (
            result.attempts[index + 1] if index + 1 < len(result.attempts) else None
        )
        if following is not None and following.part == attempt.part:
            entries.append(
                AuditEntry(
                    "agent.retry",
                    f"{agent} retried once with the error shown · {following.slot} "
                    "model slot",
                    {"attempt": following.attempt, "slot": following.slot},
                )
            )
    if failed_reason is not None:
        entries.append(
            AuditEntry("agent.failed", f"{agent} blocked: {failed_reason}", {})
        )
    return entries


def guard_summary(result: AgentRunResult) -> dict[str, int]:
    """``AgentDone.output["guard_summary"]``: how many claims the last Guard 4
    pass saw, how many carried a verified source, and how many items were
    dropped. For a split job, the last pass of each part (and of the reduce
    call), summed.
    """
    last: dict[int, dict[str, Any]] = {}
    for index, attempt in enumerate(result.attempts):
        shapes = _shape_results(result, index)
        if shapes:
            last[attempt.part] = shapes[-1].details
    return {
        "claims_total": sum(int(d.get("claims", 0)) for d in last.values()),
        "claims_with_source": sum(int(d.get("verified", 0)) for d in last.values()),
        "dropped": len(result.dropped),
    }


def fast_lane_proposed(agent: AgentName, result: AgentRunResult) -> Optional[bool]:
    """The screening agent's own R4 proposal, copied to the top level of
    ``agent.done`` for H1's guard; ``None`` for every other agent.
    """
    if agent is not AgentName.SCREENING:
        return None
    for item in result.accepted:
        if isinstance(item, ScreeningProposal):
            return item.fast_lane
    return False


class AgentJobConsumer(Consumer):
    """Consumes ``agent-worker.run`` (:data:`AGENT_WORKER_RUN_QUEUE`)."""

    def __init__(
        self,
        consumed: AsyncCollection[Document],
        publisher: Publisher,
        *,
        registry: ToolRegistry,
        permit_client: OPAClient,
        audit: AuditClient,
        tiers_config: Mapping[str, Any],
        transact: Transact,
        jobs: AgentJobStore,
        outbox: Outbox,
        instructions_for: InstructionsFor = lambda _agent, _mode, _versions: None,
        model_factory: ModelFactory = live_model_for_slot,
        tool_call_cap: Optional[int] = None,
        max_attempts: int = 3,
        raw_inputs: Optional[RawInputsSink] = None,
        metrics: Optional[FcrmMetrics] = None,
        max_parts: int = 4,
        reduce_tier: Tier = Tier.CHEAP,
        output_floor_tokens: int = 1024,
        reduce_instructions_for: InstructionsFor = (
            lambda _agent, _mode, _versions: None
        ),
    ) -> None:
        """Wire one job's dependencies.

        Args:
            consumed: The ``consumed_messages`` collection (idempotency). The
                base consumer is built without a Mongo client, so the marker
                is written after :meth:`handle` succeeds (module docstring).
            publisher: The broker adapter (:class:`fcrm_agent_worker
                .publisher.FastStreamPublisher` in production), used here only
                for the base consumer's retry and DLQ republishing: the
                terminal events go through ``outbox``.
            registry: The tool registry context-pack builders and the
                model's tools call (seed-backed today; see package
                ``README``/report for the live-tools gap).
            permit_client: Guard 2's OPA client (``fcrm_common.authz
                .OPAClient`` in production, fails closed).
            audit: The S4 ``POST /records`` client.
            tiers_config: Parsed ``ai/models/tiers.yaml`` (never a model id
                itself -- ADR-04).
            transact: Runs the short terminal transaction
                (:func:`mongo_transact` in production).
            jobs: The ``agent_jobs`` ledger each job is claimed in and
                marked done in.
            outbox: H2's outbox (``agent_outbox``), written in the same
                transaction as :meth:`AgentJobStore.mark_done`.
            instructions_for: ``(agent, mode, pinned versions)`` -> the
                agent's rendered system prompt text (``None`` runs with no
                system instructions; production wiring renders through
                :class:`fcrm_prompts.InstructionRenderer`).
            model_factory: Builds the attempt's model
                (:func:`fcrm_agent_worker.live_runtime.live_model_for_slot`
                in production; a fake in tests).
            tool_call_cap: ``AGENT_MAX_TOOL_CALLS`` setting, when tighter
                than the agent's own ``tool_call_cap``.
            max_attempts: Broker-level retries before the DLQ (distinct
                from the guard chain's own attempt budget).
            raw_inputs: Where a failed job's inputs are kept for the
                human handoff; ``None`` keeps none (a failure then names
                no ``raw_inputs_ref``).
            metrics: The instruments each finished job is recorded into
                (:mod:`fcrm_agent_worker.job_metrics`); the process-wide set
                by default, an in-memory one in tests.
            max_parts: ``AGENT_MAX_PARTS``: the most parts one job's input
                may be split into. It also sizes the job's lease
                (:func:`fcrm_agent_worker.jobs.lease_seconds`).
            reduce_tier: ``AGENT_REDUCE_TIER``: the tier of the one reduce
                call that joins a split narrative/recommendation.
            output_floor_tokens: ``AGENT_OUTPUT_FLOOR_TOKENS``: the least
                output allowance a model request is given.
            reduce_instructions_for: The reduce call's rendered system prompt,
                same shape as ``instructions_for``.
        """
        super().__init__(
            AGENT_WORKER_RUN_QUEUE,
            consumed,
            publisher,
            client=None,
            max_attempts=max_attempts,
        )
        self._registry = registry
        self._raw_inputs = raw_inputs
        # A timeout or an unreachable OPA is asked once more within the
        # deadline; a deny never is (D-130).
        self._permit = PermitGuard(
            permit_client, retry_on=(TimeoutError, AuthzUnavailableError)
        )
        self._audit = audit
        self._tiers_config = tiers_config
        self._metrics = metrics if metrics is not None else get_metrics()
        self._prices = PriceTable.from_tiers(tiers_config)
        self._instructions_for = instructions_for
        self._model_factory = model_factory
        self._tool_call_cap = tool_call_cap
        self._transact = transact
        self._jobs = jobs
        self._outbox = outbox
        self._max_parts = max_parts
        self._reduce_tier = reduce_tier
        self._output_floor_tokens = output_floor_tokens
        self._reduce_instructions_for = reduce_instructions_for

    async def handle(
        self, envelope: Envelope, session: Optional[AsyncClientSession]
    ) -> None:
        """Claim one ``agent.run`` job, run it with no transaction open, and
        commit its terminal event to the outbox.

        ``session`` is always ``None`` (the consumer has no client).

        Raises:
            pydantic.ValidationError: ``envelope.payload`` is not a
                ``JobPayload`` (poison; the base ``Consumer``/
                ``deliver_with_retry`` already handle an unparseable
                envelope -- this is a parseable envelope with the wrong
                payload shape, equally not worth retrying blindly, so it
                is allowed to propagate into the same retry/DLQ path).
            JobInFlightError: Another claim holds a live lease on this job;
                the delivery is retried later instead of running it twice.
        """
        job = JobPayload.model_validate(envelope.payload)
        started = time.perf_counter()
        with bound_context(
            case_id=job.case_id,
            run_id=job.run_id,
            job_id=job.job_id,
            agent=job.agent.value,
        ):
            spec = get_spec(job.agent)
            claimed = await self._jobs.claim(
                job.job_id,
                self._lease_s(spec),
                case_id=job.case_id,
                run_id=job.run_id,
                agent=job.agent.value,
            )
            if claimed is ClaimOutcome.DONE:
                logger.info(
                    "agent job already finished; its event is in the outbox",
                    extra={"job_id": job.job_id},
                )
                return
            if claimed is ClaimOutcome.RUNNING:
                raise JobInFlightError(job.job_id)
            try:
                await self._run_claimed(job, spec, started)
            except Exception:
                await self._release(job)
                raise
            finally:
                self._jobs.forget(job.job_id)

    def _lease_s(self, spec: AgentSpec) -> int:
        """The job's lease: its tier's worst case at :attr:`_max_parts` parts
        (the part count is not known until the pack is built, and a lease that
        runs out mid-call lets a redelivery run the job a second time).
        """
        return lease_seconds(spec.tier, self._max_parts)

    async def _release(self, job: JobPayload) -> None:
        """Give the claim up after the job raised, so the broker's retry can
        claim it again at once; a failure here is logged, never raised over
        the job's own error (the lease then simply expires).
        """
        try:
            await self._jobs.release(job.job_id)
        except PyMongoError:
            logger.exception(
                "releasing the agent job claim failed; it expires with its lease",
                extra={"job_id": job.job_id},
            )

    async def _run_claimed(
        self, job: JobPayload, spec: AgentSpec, started: float
    ) -> None:
        """Build the pack, run the agent and finish the job (this process
        holds the claim)."""
        try:
            packs = await self._build_packs(job)
        except _CONTEXT_PACK_ERRORS as exc:
            await self._fail_context_pack(job, spec, started, exc)
            return

        first = packs[0]
        job_usage = JobUsage()

        async def renew() -> None:
            await self._jobs.renew(job.job_id, self._lease_s(spec))

        outcome = await run_agent_parts(
            spec,
            packs,
            job.job_id,
            registry=self._registry,
            permit=self._permit,
            budget=CaseBudget(job.case_id),
            instructions=self._instructions_for(job.agent, first.mode, first.versions),
            tiers_config=self._tiers_config,
            model_factory=metered_factory(self._model_factory, job_usage),
            tool_call_cap=self._tool_call_cap,
            starting_attempt=job.attempt,
            output_floor=self._output_floor_tokens,
            reduce_tier=self._reduce_tier,
            reduce_instructions=(
                self._reduce_instructions_for(job.agent, first.mode, first.versions)
                if len(packs) > 1 and needs_reduce(spec, first.mode)
                else None
            ),
            on_part_done=renew if len(packs) > 1 else None,
        )
        result, model_used, usage = outcome.result, outcome.model_used, outcome.usage
        attempt_count = len(result.attempts) or job.attempt
        if result.verdict in (RunVerdict.PASS, RunVerdict.PASS_WITH_DROPS):
            await self._finish(
                job,
                spec,
                self._done_envelope(
                    job,
                    result,
                    model_used,
                    usage,
                    attempt_count,
                    parts=parts_summary(outcome.parts),
                ),
                outcome=job_outcome(result),
                started=started,
                entries=audit_entries(result, spec),
                usage=job_usage,
                result=result,
            )
            return
        reason = self._failure_reason(result)
        ref = await self._keep_inputs(job, outcome.failed_pack or first)
        await self._finish(
            job,
            spec,
            self._failed_envelope(
                job,
                attempt=attempt_count,
                reason=reason,
                raw_inputs_ref=ref,
                usage=usage,
            ),
            outcome=job_outcome(result),
            started=started,
            entries=audit_entries(result, spec, failed_reason=reason),
            usage=job_usage,
            result=result,
        )

    async def _finish(
        self,
        job: JobPayload,
        spec: AgentSpec,
        envelope: Envelope,
        *,
        outcome: str,
        started: float,
        entries: Sequence[AuditEntry],
        usage: Optional[JobUsage] = None,
        result: Optional[AgentRunResult] = None,
    ) -> bool:
        """Commit ``envelope`` (``agent.done`` or ``agent.failed``) as the
        job's one terminal event, then record metrics and write ``entries``.

        One short transaction marks the job done
        (:meth:`AgentJobStore.mark_done`) and writes the event to H2's outbox,
        so the job is done exactly when its event will be published. When
        ``mark_done`` finds the claim gone (the lease ran out and another
        worker took the job over) nothing is enqueued and no audit record is
        written: the other worker's run announces and records the job. The
        metrics are recorded either way, since this run's model calls were
        made and paid for.

        Args:
            job: The job.
            spec: Its agent spec.
            envelope: The terminal event, built once outside the transaction
                so a retried transaction enqueues the same ``event_id``.
            outcome: The metrics outcome (``job_metrics.job_outcome``, or
                ``OUTCOME_CONTEXT_PACK_ERROR``), also stored on the job.
            started: ``time.perf_counter()`` when the delivery arrived.
            entries: The S4 records to write after the commit.
            usage: The job's metered model usage, when it reached a model.
            result: The run's result, when it ran.

        Returns:
            Whether this call committed the event.

        Raises:
            Whatever the transaction raises (Mongo down, the outbox refusing
            a session with no transaction): the job's claim is released by
            :meth:`handle` and the delivery is retried.
        """
        routing_key = _TERMINAL_ROUTING_KEYS[envelope.type]

        async def commit(session: AsyncClientSession) -> bool:
            if not await self._jobs.mark_done(
                job.job_id, outcome=outcome, event_id=envelope.event_id, session=session
            ):
                return False
            await self._outbox.enqueue(
                session,
                envelope,
                exchange=default_exchange(envelope.type),
                routing_key=routing_key,
            )
            return True

        committed = await self._transact(commit)
        self._record(job, spec, started, outcome, usage, result)
        if not committed:
            logger.warning(
                "agent job was taken over by another worker; not announcing it",
                extra={"job_id": job.job_id, "event_type": envelope.type},
            )
            return False
        await self._write_audit_entries(job, entries)
        return True

    def _record(
        self,
        job: JobPayload,
        spec: AgentSpec,
        started: float,
        outcome: str,
        usage: Optional[JobUsage] = None,
        result: Optional[AgentRunResult] = None,
    ) -> None:
        """Record the finished job's metrics, after its terminal transaction
        (a failed commit raises first, so a broker retry is not counted)."""
        record_job_metrics(
            self._metrics,
            self._prices,
            agent=job.agent.value,
            tier=spec.tier.value,
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
            outcome=outcome,
            usage=usage,
            result=result,
        )

    async def _build_packs(self, job: JobPayload) -> list[ContextPack]:
        """The job's context pack, or its parts when the input does not fit
        the tier (ADR-77; at most :attr:`_max_parts`). ``job.refs`` go to the
        builder unchanged (:func:`pack_refs`).

        Raises:
            One of ``_CONTEXT_PACK_ERRORS``: the build failed
                deterministically (redelivering the same message fails the
                same way), which :meth:`_fail_context_pack` reports.
        """
        return await builder_for(job.agent).build_parts(
            BuildRequest.from_job(job, **pack_refs(job)),
            self._registry,
            max_parts=self._max_parts,
        )

    async def _fail_context_pack(
        self, job: JobPayload, spec: AgentSpec, started: float, exc: Exception
    ) -> None:
        """Report a context-pack build failure as the job's terminal
        ``agent.failed``, never a broker retry, through :meth:`_finish`."""
        logger.warning(
            "context pack build failed; reporting agent.failed",
            extra={"agent": job.agent.value, "error": type(exc).__name__},
        )
        ref = await self._keep_inputs(job, None, error=str(exc))
        await self._finish(
            job,
            spec,
            self._failed_envelope(
                job,
                attempt=job.attempt,
                reason=f"context_pack_error: {exc}",
                raw_inputs_ref=ref,
            ),
            outcome=OUTCOME_CONTEXT_PACK_ERROR,
            started=started,
            entries=[
                AuditEntry(
                    "agent.failed",
                    f"{job.agent.value} context pack failed: {exc}",
                )
            ],
        )

    @staticmethod
    def _failure_reason(result: AgentRunResult) -> str:
        stopped = result.stopped_at
        code = (
            "guard4_rejected"
            if stopped is None
            else _FAILURE_REASON_CODE.get(stopped, "guard4_rejected")
        )
        return f"{code}: {result.reason}" if result.reason else code

    @staticmethod
    def _done_envelope(
        job: JobPayload,
        result: AgentRunResult,
        model_used: str,
        usage: dict[str, int],
        attempt: int,
        *,
        parts: Optional[list[dict[str, Any]]] = None,
    ) -> Envelope:
        """The ``agent.done`` event for a job that passed; ``parts`` (one row
        per part, :func:`~fcrm_agent_worker.parts.parts_summary`) is added to
        the output only when the input was split."""
        output: dict[str, Any] = {
            "accepted": [item.model_dump(mode="json") for item in result.accepted],
            "dropped": result.dropped,
            "notes_for_analyst": result.notes_for_analyst,
            "guard_summary": guard_summary(result),
            "attempts": attempt,
        }
        if parts:
            output["parts"] = parts
        payload = AgentDone(
            job_id=job.job_id,
            case_id=job.case_id,
            run_id=job.run_id,
            agent=job.agent,
            output=output,
            usage=usage,
            model=model_used,
            attempt=attempt,
            fast_lane_proposed=fast_lane_proposed(job.agent, result),
        )
        return build_envelope(
            "agent.done",
            payload.model_dump(mode="json"),
            case_id=job.case_id,
            run_id=job.run_id,
        )

    async def _keep_inputs(
        self,
        job: JobPayload,
        pack: Optional[ContextPack],
        *,
        error: Optional[str] = None,
    ) -> Optional[str]:
        """Keep what the failed job was given, for the person who finishes it:
        the reference to name in ``agent.failed``, or ``None``.
        """
        if self._raw_inputs is None:
            return None
        return await self._raw_inputs.save(job, pack, error=error)

    @staticmethod
    def _failed_envelope(
        job: JobPayload,
        *,
        attempt: int,
        reason: str,
        raw_inputs_ref: Optional[str] = None,
        usage: Optional[dict[str, int]] = None,
    ) -> Envelope:
        """The ``agent.failed`` event for a job that cannot finish."""
        payload = AgentFailed(
            job_id=job.job_id,
            case_id=job.case_id,
            run_id=job.run_id,
            agent=job.agent,
            attempt=attempt,
            reason=reason,
            raw_inputs_ref=raw_inputs_ref,
            usage=dict(usage or {}),
        )
        return build_envelope(
            "agent.failed",
            payload.model_dump(mode="json"),
            case_id=job.case_id,
            run_id=job.run_id,
        )

    async def _write_audit_entries(
        self, job: JobPayload, entries: Sequence[AuditEntry]
    ) -> None:
        """Write ``entries`` in order, best-effort. After the first failure
        the rest of this job's records are skipped (logged), so an S4 that is
        down costs one timeout, not one per record.
        """
        for index, entry in enumerate(entries):
            if await self._write_audit(job, entry):
                continue
            skipped = len(entries) - index - 1
            if skipped:
                logger.warning(
                    "skipping %d remaining audit record(s) for this job",
                    skipped,
                    extra={"case_id": job.case_id, "job_id": job.job_id},
                )
            return

    async def _write_audit(self, job: JobPayload, entry: AuditEntry) -> bool:
        """Best-effort audit write: a write failure is logged, never
        re-raised -- the event is already committed and this consumer must
        not turn S4 being slow or down into a redelivered/duplicated
        ``agent.done``/``agent.failed`` (design A §10.3's "S4 is down"
        degrades audit completeness, not job delivery; a replayed hash
        chain gap is visible at ``GET /verify``, which is the intended
        detection path).

        Returns:
            Whether the record was written.
        """
        try:
            await self._audit.append(
                RecordAppend(
                    case_id=job.case_id,
                    type=entry.record_type,
                    actor="ai",
                    summary=entry.summary,
                    data={
                        **entry.data,
                        "run_id": job.run_id,
                        "job_id": job.job_id,
                        "agent": job.agent.value,
                    },
                )
            )
        except Exception:  # noqa: BLE001 - audit must never crash job delivery
            logger.exception(
                "audit write failed; event already committed",
                extra={
                    "case_id": job.case_id,
                    "job_id": job.job_id,
                    "type": entry.record_type,
                },
            )
            return False
        return True
