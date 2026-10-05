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
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Any, Final, Optional

from fcrm_agents.builders import builder_for
from fcrm_agents.context_pack import BuildRequest, PackBuildError, PackOverflowError
from fcrm_agents.runtime import AgentRunResult, RunVerdict
from fcrm_agents.spec import get_spec
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
from fcrm_guardrails import CaseBudget, GuardName, PermitGuard
from fcrm_guardrails.opa_client import OPAClient
from fcrm_tools import ToolError, ToolNotPermittedError, ToolRegistry, ToolScopeError
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
#: version, a tool refusal) -- reported as ``agent.failed``, never retried
#: by the broker, since redelivering the identical message would fail the
#: same way.
_CONTEXT_PACK_ERRORS: Final = (
    PackBuildError,
    PackOverflowError,
    ToolError,
    ToolNotPermittedError,
    ToolScopeError,
)


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
            try:
                pack = await builder_for(job.agent).build(
                    BuildRequest.from_job(job), self._registry
                )
            except _CONTEXT_PACK_ERRORS as exc:
                logger.warning(
                    "context pack build failed; reporting agent.failed",
                    extra={"agent": job.agent.value, "error": type(exc).__name__},
                )
                await self._publish_failed(
                    job, attempt=job.attempt, reason=f"context_pack_error: {exc}"
                )
                await self._write_audit(
                    job, "agent.failed", f"{job.agent.value} context pack failed: {exc}"
                )
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
                await self._write_audit(
                    job,
                    "agent.done",
                    f"{job.agent.value} {result.verdict.value}: "
                    f"{len(result.accepted)} accepted, {len(result.dropped)} dropped",
                )
                return
            reason = self._failure_reason(result)
            await self._publish_failed(job, attempt=attempt_count, reason=reason)
            await self._write_audit(
                job, "agent.failed", f"{job.agent.value} blocked: {reason}"
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
            },
            usage=usage,
            model=model_used,
            attempt=attempt,
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

    async def _write_audit(
        self, job: JobPayload, record_type: str, summary: str
    ) -> None:
        """Best-effort audit write: a write failure is logged, never
        re-raised -- the event is already published and this consumer must
        not turn S4 being slow or down into a redelivered/duplicated
        ``agent.done``/``agent.failed`` (design A §10.3's "S4 is down"
        degrades audit completeness, not job delivery; a replayed hash
        chain gap is visible at ``GET /verify``, which is the intended
        detection path).
        """
        try:
            await self._audit.append(
                RecordAppend(
                    case_id=job.case_id,
                    type=record_type,
                    actor="ai",
                    summary=summary,
                    data={
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
                    "type": record_type,
                },
            )
