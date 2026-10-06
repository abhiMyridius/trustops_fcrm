"""One job, live, through the real guard chain (design B §5.1-§5.2).

This is :func:`fcrm_agents.runtime.run_agent_offline`'s live twin: the same
pre-guards (1 input scan, 2 permit, 3 budget) -> attempt loop (the model
call, then 4 output shape, 5 rules) -> settle shape, but the model call
goes to a real Bedrock ``FallbackModel`` (:mod:`fcrm_agents.models`)
instead of a fixed-output ``FunctionModel``. One ``agent.run`` message
drives the *whole* job, both attempts if needed, exactly like the offline
runtime — confirmed against ``fcrm_caseflow.triggers.Trigger
.AGENT_FAILED_AFTER_RETRY`` (H1 reacts to one terminal ``agent.failed``
"after its one retry", never a per-attempt redispatch), so H2 never
publishes a message for an in-progress retry; only the final
``agent.done``/``agent.failed`` crosses back to the bus
(:mod:`fcrm_agent_worker.handler`).

The guard orchestration (pre-guards once, then an attempt loop up to the
tier's ``max_attempts`` calling guards 4-5 after each) is deliberately
parallel to, not shared with, ``fcrm_agents.runtime.OfflineRun``: that
class's attempt loop is wired to a ``FunctionModel`` and its two private
helpers (``_schema_failure``, ``_drop``) are module-private by convention.
Duplicating the ~40 lines of orchestration here keeps this package's
blast radius contained to the one package H2 owns, at the cost of two
copies that must be kept in step by hand; a shared ``AttemptLoop`` base in
``fcrm_agents.runtime`` would remove that cost and is a reasonable
follow-up once both call sites are live.

A ``model_factory`` callable is the one seam between this module and a
real Bedrock call: production wiring passes :func:`live_model_for_slot`,
and tests pass a ``FunctionModel``/``TestModel`` factory instead — the
guard pipeline, budget enforcement and error mapping below are exercised
exactly as written either way.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from re import compile as re_compile
from typing import Any, Final, Optional

from fcrm_agents.context_pack import ContextPack
from fcrm_agents.models import (
    TierModels,
    bedrock_chain,
    build_model,
    model_settings_for,
    tier_models,
)
from fcrm_agents.runtime import (
    HARNESS_ROLE,
    AgentDeps,
    AgentRunResult,
    AttemptRecord,
    RunVerdict,
    agent_capabilities,
    request_limit,
    tool_functions,
)
from fcrm_agents.spec import AgentSpec
from fcrm_guardrails import (
    BudgetExceededError,
    BudgetGuard,
    CaseBudget,
    GuardChain,
    GuardContext,
    GuardName,
    GuardResult,
    GuardStatus,
    InputScanGuard,
    OutputShapeGuard,
    PermitGuard,
    RulesGuard,
    ScanText,
    attempt_plan,
)
from fcrm_guardrails.base import AUDIT_TYPES
from fcrm_guardrails.budget import TIER_BUDGETS, AttemptPlan, ModelSlot
from fcrm_guardrails.context import AvailableSource
from fcrm_tools import ToolRegistry
from pydantic import BaseModel
from pydantic_ai import (
    Agent,
    RunUsage,
    ToolOutput,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
    UsageLimits,
)
from pydantic_ai.exceptions import FallbackExceptionGroup
from pydantic_ai.models import Model
from pydantic_ai.providers.bedrock import BedrockProvider

logger = logging.getLogger(__name__)

_OUTPUT_INDEX_PATTERN: Final = re_compile(r"^outputs\[(\d+)\]")

#: Keys of the usage breakdown ``AgentDone.usage`` wants.
UsageBreakdown = dict[str, int]
USAGE_KEYS: Final = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
)

ModelFactory = Callable[[TierModels, AttemptPlan], Model]


def live_model_for_slot(
    models: TierModels,
    plan: AttemptPlan,
    *,
    provider: Optional[BedrockProvider] = None,
    region_fallback: Optional[BedrockProvider] = None,
) -> Model:
    """The real model for one attempt (design B §5.2, ``ModelSlot``):
    attempt 1 runs the tier's full ``FallbackModel(primary, *fallbacks)``
    (:func:`fcrm_agents.models.build_model`); attempt 2 runs a
    ``FallbackModel`` of the fallback models alone, so a Guard 4 retry
    never lands back on the model whose output it just rejected.

    With ``region_fallback`` each chain ends with its first model in the second
    region (``BEDROCK_REGION_FALLBACK``): attempt 1's primary, attempt 2's first
    fallback, so the retry still never returns to the rejected model.
    """
    if plan.slot is ModelSlot.PRIMARY_WITH_FALLBACK:
        return build_model(models, provider=provider, region_fallback=region_fallback)
    return bedrock_chain(
        models, models.fallbacks, provider=provider, region_fallback=region_fallback
    )


def _bedrock_provider(region: str) -> BedrockProvider:
    """A Bedrock provider for ``region`` over the default credential chain."""
    return BedrockProvider(region_name=region)


def model_factory_for(
    region_fallback: Optional[str],
    primary_region: Optional[str] = None,
    *,
    provider_for: Callable[[str], BedrockProvider] = _bedrock_provider,
) -> ModelFactory:
    """The :data:`ModelFactory` H2 runs with: :func:`live_model_for_slot`, with a
    second Bedrock region as the last fallback of every chain when
    ``BEDROCK_REGION_FALLBACK`` names one that is not the primary region.

    Args:
        region_fallback: ``settings.bedrock_region_fallback``; empty or ``None``
            means no second region.
        primary_region: The region the default credential chain resolves
            (``settings.aws_region``); a fallback equal to it adds nothing and is
            ignored.
        provider_for: Builds the provider for a region (a fake in tests). boto3
            builds its client lazily, so this makes no network call.

    Returns:
        A factory with the ``(TierModels, AttemptPlan)`` signature.
    """
    region = (region_fallback or "").strip()
    if not region or region == (primary_region or "").strip():
        return live_model_for_slot
    provider = provider_for(region)
    logger.info("model chains end with a second Bedrock region: %s", region)

    def factory(models: TierModels, plan: AttemptPlan) -> Model:
        return live_model_for_slot(models, plan, region_fallback=provider)

    return factory


def _drop(
    outputs: Sequence[BaseModel], item_ids: Sequence[str]
) -> tuple[list[BaseModel], list[str]]:
    """Split ``outputs`` into kept/dropped by Guard 4's ``outputs[i]`` ids
    (mirrors ``fcrm_agents.runtime._drop``; duplicated rather than
    imported, since that name is private to its module -- see module doc).
    """
    indexes = {
        int(m.group(1))
        for item in item_ids
        if (m := _OUTPUT_INDEX_PATTERN.match(item)) is not None
    }
    kept = [o for i, o in enumerate(outputs) if i not in indexes]
    return kept, [f"outputs[{i}]" for i in sorted(indexes)]


FEEDBACK_LINES: Final = 8
"""Failing items a retry is shown (the first ones; the rest repeat the same
mistake)."""
FEEDBACK_CHARS: Final = 240

RETRY_HEADER: Final = (
    "## previous_attempt\n"
    "Your previous answer was refused by the checks below. Answer again, "
    "through the output tool, with the complete result. Fix each item from "
    "the sources shown above, or leave out any item you cannot source."
)


def retry_text(user_text: str, feedback: Sequence[str]) -> str:
    """``user_text`` with the reasons the previous attempt was refused
    appended, or unchanged when there are none. The feedback is Guard 4's own
    per-item lines (``details["feedback"]``): code, never model text."""
    if not feedback:
        return user_text
    lines = "\n".join(
        f"- {line[:FEEDBACK_CHARS]}" for line in feedback[:FEEDBACK_LINES]
    )
    return f"{user_text}\n\n{RETRY_HEADER}\n{lines}"


def describe_failure(exc: BaseException) -> str:
    """One line saying why a model call failed: the error and, when Pydantic AI
    wrapped one (an output that failed validation is reported as "exceeded
    maximum output retries"), what it wrapped."""
    text = str(exc)
    cause = exc.__cause__
    if cause is not None:
        text = f"{text}: {type(cause).__name__}: {cause}"
    return " ".join(text.split())[:400]


def _model_name(model: Model) -> str:
    """A loggable model identifier (never a raw model id outside
    ``ai/models/tiers.yaml`` per ADR-04 -- this reads the *runtime*
    object's own name, already sourced from that file)."""
    return str(getattr(model, "model_name", type(model).__name__))


def _usage_of(usage: RunUsage) -> UsageBreakdown:
    """The four-key usage breakdown ``AgentDone.usage`` wants, from one
    attempt's Pydantic AI :class:`~pydantic_ai.RunUsage`."""
    return {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_read_tokens": usage.cache_read_tokens,
        "cache_write_tokens": usage.cache_write_tokens,
    }


class LiveRun:
    """One job: pre-guards once, then an attempt loop with a live model
    call and guards 4-5, up to the tier's ``max_attempts``.
    """

    def __init__(
        self,
        spec: AgentSpec,
        pack: ContextPack,
        job_id: str,
        *,
        registry: ToolRegistry,
        permit: PermitGuard,
        budget: CaseBudget,
        instructions: Optional[str],
        tiers_config: Mapping[str, Any],
        model_factory: ModelFactory = live_model_for_slot,
        tool_call_cap: Optional[int] = None,
        starting_attempt: int = 1,
    ) -> None:
        """Hold the job's inputs.

        Args:
            spec: The agent's static definition (tier, tools, output type).
            pack: Its pre-built context pack.
            job_id: H1's job id (``JobPayload.job_id``); the budget
                reservation key and the ``GuardContext.job_id``.
            registry: The tools the model may call.
            permit: Guard 2 (OPA agent-to-tool permits).
            budget: This job's token ledger (:class:`CaseBudget`).
            instructions: The agent's system prompt text, or ``None``.
            tiers_config: Parsed ``ai/models/tiers.yaml``.
            model_factory: Builds the model for one attempt
                (:func:`live_model_for_slot` in production).
            tool_call_cap: Lowers ``spec.tool_call_cap`` when H2's
                ``AGENT_MAX_TOOL_CALLS`` setting is tighter (design A
                §10.6's "refuses tool loops beyond max_tool_calls").
            starting_attempt: Normally 1; honours ``JobPayload.attempt``
                when H1 ever resumes a job past its first attempt.
        """
        self.spec = spec
        self.pack = pack
        self.job_id = job_id
        self.registry = registry
        self.permit = permit
        self.budget = budget
        self.instructions = instructions
        self.models = tier_models(spec.tier, tiers_config)
        self.max_attempts = TIER_BUDGETS[spec.tier].max_attempts
        self.model_factory = model_factory
        self.tool_call_cap = (
            spec.tool_call_cap
            if tool_call_cap is None
            else min(tool_call_cap, spec.tool_call_cap)
        )
        self.starting_attempt = starting_attempt
        self.model_used = ""
        self.usage_total: UsageBreakdown = {}
        self.tool_sources: list[AvailableSource] = []
        self.feedback: list[str] = []
        self.base = GuardContext(
            case_id=pack.case_id,
            run_id=pack.run_id,
            job_id=job_id,
            agent=spec.name,
            tier=spec.tier,
            mode=pack.mode,
            role=HARNESS_ROLE,
        )
        self.result = AgentRunResult(
            agent=spec.name.value,
            case_id=pack.case_id,
            run_id=pack.run_id,
            verdict=RunVerdict.BLOCKED,
            pack_hash=pack.pack_hash,
            pack_token_estimate=pack.token_estimate,
        )

    def _block(self, guard: GuardName, reason: str) -> AgentRunResult:
        self.result.verdict = RunVerdict.BLOCKED
        self.result.stopped_at = guard
        self.result.reason = reason
        return self.result

    async def pre_guards(self) -> Optional[str]:
        """Guards 1-3; the masked user text, or ``None`` if blocked."""
        try:
            self.budget.reserve(self.job_id, self.spec.name, self.spec.tier)
        except BudgetExceededError as exc:
            self._block(GuardName.BUDGET, str(exc))
            return None
        ctx = self.base.model_copy(
            update={
                "input_texts": [
                    ScanText(name=name, text=text)
                    for name, text in self.pack.sections.items()
                ],
                "tools": list(self.spec.tools),
                "budget": self.budget,
                "reservation_id": self.job_id,
            }
        )
        chain = GuardChain([InputScanGuard(), self.permit, BudgetGuard()])
        results = await chain.run_pre(ctx)
        self.result.pre_guards = results
        if results and not results[-1].passed:
            self.budget.release(self.job_id)
            self._block(results[-1].guard, results[-1].summary)
            return None
        masked = results[0].transformed if results else {}
        return "\n\n".join(
            f"## {name}\n{masked.get(name, text)}"
            for name, text in self.pack.sections.items()
        )

    async def call_model(
        self, attempt: int, user_text: str
    ) -> tuple[Optional[list[BaseModel]], AttemptRecord]:
        """One live model call for ``attempt``."""
        plan = attempt_plan(self.spec.tier, attempt)
        record = AttemptRecord(attempt=attempt, slot=plan.slot.value, guard_results=[])
        model = self.model_factory(self.models, plan)
        self.model_used = _model_name(model)
        agent: Agent[AgentDeps, Any] = Agent(
            model,
            output_type=ToolOutput(
                self.spec.output_type(), name=f"{self.spec.name.value}_output"
            ),
            instructions=self.instructions,
            model_settings=model_settings_for(
                self.models, max_tokens_factor=plan.max_tokens_factor
            ),
            retries=0,
            tools=tool_functions(self.spec, self.registry)
            if self.tool_call_cap
            else [],
            capabilities=agent_capabilities(),
            name=self.spec.name.value,
        )
        deps = AgentDeps(
            case_id=self.pack.case_id,
            run_id=self.pack.run_id,
            agent=self.spec.name.value,
            registry=self.registry,
            permit=self.permit,
            versions=self.pack.versions,
            tool_call_cap=self.tool_call_cap,
        )
        self.tool_sources = deps.tool_sources
        record.tools_called = deps.tools_called
        usage = RunUsage()
        try:
            run = await agent.run(
                user_text,
                deps=deps,
                usage=usage,
                usage_limits=UsageLimits(
                    total_tokens_limit=plan.token_limit,
                    request_limit=request_limit(self.tool_call_cap),
                ),
            )
        except UsageLimitExceeded as exc:
            record.guard_results.append(
                GuardResult(
                    guard=GuardName.BUDGET,
                    status=GuardStatus.BLOCKED,
                    summary=f"attempt {attempt}: usage limit exceeded ({exc})",
                    audit_type=AUDIT_TYPES[GuardName.BUDGET],
                )
            )
            return None, record
        except (UnexpectedModelBehavior, FallbackExceptionGroup, TimeoutError) as exc:
            # No dedicated GuardName covers a live model/transport failure
            # (a schema failure shares output_shape's slot with the
            # offline path; a FallbackExceptionGroup means every model in
            # the chain failed). A non-final attempt is a retry candidate,
            # same as a Guard 4 schema rejection; the last attempt blocks.
            status = (
                GuardStatus.RETRY
                if attempt < self.max_attempts
                else GuardStatus.BLOCKED
            )
            detail = describe_failure(exc)
            logger.warning(
                "model call failed (%s, attempt %d): %s",
                self.spec.name.value,
                attempt,
                detail,
                extra={"agent": self.spec.name.value, "attempt": attempt},
            )
            if isinstance(exc, UnexpectedModelBehavior):
                self.feedback = [
                    "your answer was not a valid call of the output tool "
                    f"({detail[:FEEDBACK_CHARS]}); call it once with the "
                    "complete result"
                ]
            record.guard_results.append(
                GuardResult(
                    guard=GuardName.OUTPUT_SHAPE,
                    status=status,
                    summary=f"attempt {attempt}: model call failed: {detail}",
                    details={"error": type(exc).__name__},
                    audit_type=AUDIT_TYPES[GuardName.OUTPUT_SHAPE],
                )
            )
            return None, record
        finally:
            self._bill(record, usage)
        return list(run.output), record

    def _bill(self, record: AttemptRecord, usage: RunUsage) -> None:
        """Charge an attempt's tokens to the job, whatever became of the attempt.

        A call whose answer was refused, cut off or timed out still spent its
        tokens; counting only the attempt that succeeded under-reported the case
        ledger (the first live run's retried narrative showed ``[0, 8386]``).
        """
        spent = _usage_of(usage)
        record.tokens_used = spent["input_tokens"] + spent["output_tokens"]
        record.tool_calls = usage.tool_calls
        for key in USAGE_KEYS:
            self.usage_total[key] = self.usage_total.get(key, 0) + spent[key]

    async def post_guards(
        self,
        outputs: list[BaseModel],
        attempt: int,
        record: AttemptRecord,
        *,
        last: bool,
    ) -> Optional[bool]:
        """Guard 4 then Guard 5 (mirrors
        ``fcrm_agents.runtime.OfflineRun.post_guards``): ``True`` finished,
        ``False`` blocked, ``None`` retry on the next attempt.
        """
        ctx = self.base.model_copy(
            update={
                "attempt": attempt,
                "outputs": outputs,
                "sources": [*self.tool_sources, *self.pack.sources],
                "may_be_empty": self.spec.may_be_empty,
            }
        )
        shape = await OutputShapeGuard().check(ctx)
        record.guard_results.append(shape)
        if shape.status is GuardStatus.RETRY:
            if not last:
                self.feedback = [
                    str(line) for line in shape.details.get("feedback", [])
                ]
                return None
            self._block(GuardName.OUTPUT_SHAPE, f"{shape.summary}; no attempt left")
            return False
        if shape.status is GuardStatus.BLOCKED:
            self._block(GuardName.OUTPUT_SHAPE, shape.summary)
            return False
        kept, dropped = _drop(outputs, shape.details.get("drop", []))
        rules = await RulesGuard().check(ctx.model_copy(update={"outputs": kept}))
        record.guard_results.append(rules)
        if not rules.passed:
            self._block(GuardName.RULES, rules.summary)
            return False
        self.result.accepted = kept
        self.result.dropped = dropped
        self.result.notes_for_analyst = list(shape.details.get("notes_for_analyst", []))
        self.result.verdict = RunVerdict.PASS_WITH_DROPS if dropped else RunVerdict.PASS
        self.result.stopped_at = None
        self.result.reason = None
        return True

    async def attempt(
        self, attempt: int, user_text: str, *, last: bool
    ) -> Optional[bool]:
        """Run one attempt end to end (see :meth:`post_guards`). A retry is
        shown why the last answer was refused (ADR-14: "the validation error
        appended")."""
        produced, record = await self.call_model(
            attempt, retry_text(user_text, self.feedback) if attempt > 1 else user_text
        )
        self.result.attempts.append(record)
        self.result.tokens_used += record.tokens_used
        if produced is None:
            failure = record.guard_results[-1]
            if last or failure.guard is GuardName.BUDGET:
                self._block(failure.guard, failure.summary)
                return False
            return None
        return await self.post_guards(produced, attempt, record, last=last)


async def run_agent_live(
    spec: AgentSpec,
    pack: ContextPack,
    job_id: str,
    *,
    registry: ToolRegistry,
    permit: PermitGuard,
    budget: CaseBudget,
    instructions: Optional[str],
    tiers_config: Mapping[str, Any],
    model_factory: ModelFactory = live_model_for_slot,
    tool_call_cap: Optional[int] = None,
    starting_attempt: int = 1,
) -> tuple[AgentRunResult, str, UsageBreakdown]:
    """Run one job live through the real guard chain (module doc).

    Returns ``(result, model_used, usage)``: ``result`` is exactly what
    ``run_agent_offline`` would hand H1 (same type, same verdict
    semantics); ``model_used`` is the last attempt's and ``usage`` is the whole
    job's, every attempt included, for ``AgentDone.model``/``AgentDone.usage``
    (H1 sums it into the case's token total).
    """
    run = LiveRun(
        spec,
        pack,
        job_id,
        registry=registry,
        permit=permit,
        budget=budget,
        instructions=instructions,
        tiers_config=tiers_config,
        model_factory=model_factory,
        tool_call_cap=tool_call_cap,
        starting_attempt=starting_attempt,
    )
    user_text = await run.pre_guards()
    if user_text is not None:
        for attempt in range(run.starting_attempt, run.max_attempts + 1):
            done = await run.attempt(
                attempt, user_text, last=attempt == run.max_attempts
            )
            if done is not None:
                break
        run.budget.settle(run.job_id, run.result.tokens_used)
    logger.info(
        "live agent run finished",
        extra={
            "agent": spec.name.value,
            "verdict": run.result.verdict.value,
            "attempts": len(run.result.attempts),
            "tokens": run.result.tokens_used,
            "usage": dict(run.usage_total),
        },
    )
    return run.result, run.model_used, run.usage_total
