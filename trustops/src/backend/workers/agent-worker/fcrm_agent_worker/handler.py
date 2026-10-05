"""The ``agent-worker.run`` consumer: H2's one integration point with the
bus (design A §10.6).

One ``agent.run`` message -> :func:`fcrm_agent_worker.live_runtime
.run_agent_live` (pre-guards, the live model call, guards 4-5, with H2's
own internal retry across the tier's attempts) -> an audit record through
S4 -> ``agent.done`` or ``agent.failed`` (never both; never neither).
H1 (``fcrm_caseflow``) reacts to exactly those two terminal events
(``fcrm_caseflow.triggers.Trigger.AGENT_FAILED_AFTER_RETRY`` -- "after its
one retry" -- confirms H2, not H1, owns the attempt loop); H2 never
touches case state directly.

A guard ``BLOCKED`` verdict (any stage) is not a bug: it is reported
faithfully as ``agent.failed`` so H1 can open the human handoff gate
(design A §3.1, ``HANDOFF``) -- this consumer never retries a blocked job
itself and never marks a job done it did not actually finish.

**Audit vocabulary.** H2 writes the guard-side records of the walkthrough
seed (``demo.audit[].type``) through S4 once the terminal event is published:
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
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Optional

from fcrm_agents.builders import NarrativePackBuilder, builder_for
from fcrm_agents.context_pack import (
    BuildRequest,
    ContextPack,
    PackBuildError,
    PackOverflowError,
)
from fcrm_agents.runtime import AgentRunResult, RunVerdict
from fcrm_agents.spec import AgentSpec, get_spec
from fcrm_common.events import (
    Consumer,
    Publisher,
    build_envelope,
    default_exchange,
    publish,
)
from fcrm_common.logging import bound_context
from fcrm_common.mongo import Document
from fcrm_contracts.api.audit import RecordAppend
from fcrm_contracts.enums import AgentName
from fcrm_contracts.events.envelope import Envelope
from fcrm_contracts.events.payloads import AgentDone, AgentFailed, JobPayload
from fcrm_contracts.proposals.screening import ScreeningProposal
from fcrm_guardrails import CaseBudget, GuardName, GuardResult, PermitGuard
from fcrm_guardrails.opa_client import OPAClient
from fcrm_tools import ToolError, ToolNotPermittedError, ToolRegistry, ToolScopeError
from pydantic import ValidationError
from pymongo import AsyncMongoClient
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.collection import AsyncCollection

from fcrm_agent_worker.audit_client import AuditClient
from fcrm_agent_worker.live_runtime import (
    ModelFactory,
    live_model_for_slot,
    run_agent_live,
)
from fcrm_agent_worker.topology import (
    AGENT_WORKER_RUN_QUEUE,
    ROUTING_KEY_AGENT_DONE,
    ROUTING_KEY_AGENT_FAILED,
)

logger = logging.getLogger(__name__)

InstructionsFor = Callable[[AgentName], Optional[str]]

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


def _output_summary(agent: str, attempt: int, shape: GuardResult) -> str:
    """``<agent> attempt n: k of n claims carry sources`` for a result that may
    proceed, otherwise the guard's own rejection text.
    """
    claims, verified = shape.details.get("claims"), shape.details.get("verified")
    if shape.passed and claims is not None and verified is not None:
        return f"{agent} attempt {attempt}: {verified} of {claims} claims carry sources"
    return shape.summary


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
    for index, attempt in enumerate(result.attempts):
        for shape in _shape_results(result, index):
            entries.append(
                AuditEntry(
                    "guard.output",
                    _output_summary(agent, attempt.attempt, shape),
                    {
                        "attempt": attempt.attempt,
                        "status": shape.status.value,
                        "claims_total": shape.details.get("claims"),
                        "claims_with_source": shape.details.get("verified"),
                    },
                )
            )
        if index + 1 < len(result.attempts):
            following = result.attempts[index + 1]
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
    dropped.
    """
    shapes = [
        shape
        for index in range(len(result.attempts))
        for shape in _shape_results(result, index)
    ]
    details = shapes[-1].details if shapes else {}
    return {
        "claims_total": int(details.get("claims", 0)),
        "claims_with_source": int(details.get("verified", 0)),
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
        instructions_for: InstructionsFor = lambda _agent: None,
        model_factory: ModelFactory = live_model_for_slot,
        tool_call_cap: Optional[int] = None,
        client: Optional[AsyncMongoClient[Document]] = None,
        max_attempts: int = 3,
    ) -> None:
        """Wire one job's dependencies.

        Args:
            consumed: The ``consumed_messages`` collection (idempotency).
            publisher: The broker adapter (:class:`fcrm_agent_worker
                .publisher.FastStreamPublisher` in production).
            registry: The tool registry context-pack builders and the
                model's tools call (seed-backed today; see package
                ``README``/report for the live-tools gap).
            permit_client: Guard 2's OPA client (``fcrm_common.authz
                .OPAClient`` in production, fails closed).
            audit: The S4 ``POST /records`` client.
            instructions_for: The agent's rendered system prompt text
                (``None`` runs with no system instructions -- full
                Jinja-templated prompt rendering is a separate, not-yet-
                built concern; see the package report).
            tiers_config: Parsed ``ai/models/tiers.yaml`` (never a model id
                itself -- ADR-04).
            model_factory: Builds the attempt's model
                (:func:`fcrm_agent_worker.live_runtime.live_model_for_slot`
                in production; a fake in tests).
            tool_call_cap: ``AGENT_MAX_TOOL_CALLS`` setting, when tighter
                than the agent's own ``tool_call_cap``.
            client: A replica-set client, enabling transactional dedupe
                (see ``fcrm_common.events.Consumer``).
            max_attempts: Broker-level retries before the DLQ (distinct
                from the guard chain's own attempt budget).
        """
        super().__init__(
            AGENT_WORKER_RUN_QUEUE,
            consumed,
            publisher,
            client=client,
            max_attempts=max_attempts,
        )
        self._registry = registry
        self._permit = PermitGuard(permit_client)
        self._audit = audit
        self._tiers_config = tiers_config
        self._instructions_for = instructions_for
        self._model_factory = model_factory
        self._tool_call_cap = tool_call_cap

    async def handle(
        self, envelope: Envelope, session: Optional[AsyncClientSession]
    ) -> None:
        """Run one ``agent.run`` job and publish its terminal event.

        Raises:
            pydantic.ValidationError: ``envelope.payload`` is not a
                ``JobPayload`` (poison; the base ``Consumer``/
                ``deliver_with_retry`` already handle an unparseable
                envelope -- this is a parseable envelope with the wrong
                payload shape, equally not worth retrying blindly, so it
                is allowed to propagate into the same retry/DLQ path).
        """
        job = JobPayload.model_validate(envelope.payload)
        with bound_context(
            case_id=job.case_id,
            run_id=job.run_id,
            job_id=job.job_id,
            agent=job.agent.value,
        ):
            pack = await self._build_pack(job)
            if pack is None:
                return

            spec = get_spec(job.agent)
            budget = CaseBudget(job.case_id)
            result, model_used, usage = await run_agent_live(
                spec,
                pack,
                job.job_id,
                registry=self._registry,
                permit=self._permit,
                budget=budget,
                instructions=self._instructions_for(job.agent),
                tiers_config=self._tiers_config,
                model_factory=self._model_factory,
                tool_call_cap=self._tool_call_cap,
                starting_attempt=job.attempt,
            )
            attempt_count = len(result.attempts) or job.attempt
            if result.verdict in (RunVerdict.PASS, RunVerdict.PASS_WITH_DROPS):
                await self._publish_done(job, result, model_used, usage, attempt_count)
                await self._write_audit_entries(job, audit_entries(result, spec))
                return
            reason = self._failure_reason(result)
            await self._publish_failed(job, attempt=attempt_count, reason=reason)
            await self._write_audit_entries(
                job, audit_entries(result, spec, failed_reason=reason)
            )

    async def _build_pack(self, job: JobPayload) -> Optional[ContextPack]:
        """The job's context pack, or ``None`` after reporting ``agent.failed``.

        ``job.refs`` go to the builder unchanged (:func:`pack_refs`). A build
        failure is deterministic (redelivering the same message fails the same
        way), so it is a terminal ``agent.failed``, not a broker retry.
        """
        try:
            return await builder_for(job.agent).build(
                BuildRequest.from_job(job, **pack_refs(job)), self._registry
            )
        except _CONTEXT_PACK_ERRORS as exc:
            logger.warning(
                "context pack build failed; reporting agent.failed",
                extra={"agent": job.agent.value, "error": type(exc).__name__},
            )
            await self._publish_failed(
                job, attempt=job.attempt, reason=f"context_pack_error: {exc}"
            )
            await self._write_audit_entries(
                job,
                [
                    AuditEntry(
                        "agent.failed",
                        f"{job.agent.value} context pack failed: {exc}",
                    )
                ],
            )
            return None

    @staticmethod
    def _failure_reason(result: AgentRunResult) -> str:
        stopped = result.stopped_at
        code = (
            "guard4_rejected"
            if stopped is None
            else _FAILURE_REASON_CODE.get(stopped, "guard4_rejected")
        )
        return f"{code}: {result.reason}" if result.reason else code

    async def _publish_done(
        self,
        job: JobPayload,
        result: AgentRunResult,
        model_used: str,
        usage: dict[str, int],
        attempt: int,
    ) -> None:
        payload = AgentDone(
            job_id=job.job_id,
            case_id=job.case_id,
            run_id=job.run_id,
            agent=job.agent,
            output={
                "accepted": [item.model_dump(mode="json") for item in result.accepted],
                "dropped": result.dropped,
                "notes_for_analyst": result.notes_for_analyst,
                "guard_summary": guard_summary(result),
                "attempts": attempt,
            },
            usage=usage,
            model=model_used,
            attempt=attempt,
            fast_lane_proposed=fast_lane_proposed(job.agent, result),
        )
        env = build_envelope(
            "agent.done",
            payload.model_dump(mode="json"),
            case_id=job.case_id,
            run_id=job.run_id,
        )
        await publish(
            self._publisher, default_exchange("agent.done"), ROUTING_KEY_AGENT_DONE, env
        )

    async def _publish_failed(
        self, job: JobPayload, *, attempt: int, reason: str
    ) -> None:
        payload = AgentFailed(
            job_id=job.job_id,
            case_id=job.case_id,
            run_id=job.run_id,
            agent=job.agent,
            attempt=attempt,
            reason=reason,
        )
        env = build_envelope(
            "agent.failed",
            payload.model_dump(mode="json"),
            case_id=job.case_id,
            run_id=job.run_id,
        )
        await publish(
            self._publisher,
            default_exchange("agent.failed"),
            ROUTING_KEY_AGENT_FAILED,
            env,
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
        re-raised -- the event is already published and this consumer must
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
                "audit write failed; event already published",
                extra={
                    "case_id": job.case_id,
                    "job_id": job.job_id,
                    "type": entry.record_type,
                },
            )
            return False
        return True
