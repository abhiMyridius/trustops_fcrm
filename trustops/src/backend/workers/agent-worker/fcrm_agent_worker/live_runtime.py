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
``agent.done``/``agent.failed`` crosses back to the bus, through H2's outbox
(:mod:`fcrm_agent_worker.handler`). A job whose input was split runs one
:class:`LiveRun` per part, and the narrative recommendation's reduce call one
more (:mod:`fcrm_agent_worker.parts`, :mod:`fcrm_agent_worker.reduce`).

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

import asyncio
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
from pydantic import BaseModel, TypeAdapter, ValidationError
from pydantic_ai import (
    Agent,
    AgentRetries,
    RunUsage,
    ToolOutput,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
    UsageLimits,
    capture_run_messages,
)
from pydantic_ai.exceptions import FallbackExceptionGroup
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model
from pydantic_ai.providers.bedrock import BedrockProvider

from fcrm_agent_worker.allowance import (
    OUTPUT_FLOOR_TOKENS,
    OutputAllowanceModel,
    estimate_prompt_tokens,
)
from fcrm_agent_worker.bedrock_client import SharedBedrockProvider

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

#: A shared client's connection pool when the caller does not size it.
DEFAULT_POOL_CONNECTIONS: Final = 10


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


def _shared_provider(
    region: Optional[str], max_pool_connections: int
) -> SharedBedrockProvider:
    """A lazily built, shared provider for ``region`` (``None``: the default
    chain's region)."""
    return SharedBedrockProvider(region, max_pool_connections=max_pool_connections)


ProviderFor = Callable[[Optional[str], int], SharedBedrockProvider]


def model_factory_for(
    region_fallback: Optional[str],
    primary_region: Optional[str] = None,
    *,
    max_pool_connections: int = DEFAULT_POOL_CONNECTIONS,
    provider_for: ProviderFor = _shared_provider,
) -> ModelFactory:
    """The :data:`ModelFactory` H2 runs with: :func:`live_model_for_slot` over
    one shared Bedrock client for the primary region (D-130), with a second
    Bedrock region, also shared, as the last fallback of every chain when
    ``BEDROCK_REGION_FALLBACK`` names one that is not the primary region.

    Nothing is built here: each client is built on the first job that needs it
    (:class:`~fcrm_agent_worker.bedrock_client.SharedBedrockProvider`), so a
    missing region fails that job, never the harness's startup.

    Args:
        region_fallback: ``settings.bedrock_region_fallback``; empty or ``None``
            means no second region.
        primary_region: ``settings.aws_region``; ``None`` leaves the region to
            the default credential chain. A fallback equal to it adds nothing.
        max_pool_connections: Each client's connection pool: the agent jobs
            that run at once (``AGENT_PREFETCH``).
        provider_for: Builds the lazy provider for a region (a fake in tests).

    Returns:
        A factory with the ``(TierModels, AttemptPlan)`` signature.
    """
    primary_name = (primary_region or "").strip() or None
    primary = provider_for(primary_name, max_pool_connections)
    region = (region_fallback or "").strip()
    fallback: Optional[SharedBedrockProvider] = None
    if region and region != (primary_name or ""):
        fallback = provider_for(region, max_pool_connections)
        logger.info("model chains end with a second Bedrock region: %s", region)

    def factory(models: TierModels, plan: AttemptPlan) -> Model:
        return live_model_for_slot(
            models,
            plan,
            provider=primary.get(),
            region_fallback=fallback.get() if fallback is not None else None,
        )

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


EMPTY_ANSWER_FEEDBACK: Final = (
    "your answer had no items, and this agent's answer must have at least one; "
    "call the output tool once with every item the sources above support"
)
"""A retry's feedback when the last answer was empty (and the job's input was
not split): an empty list from an agent that must answer is a failed answer,
not a finding."""

CUT_OFF_FEEDBACK: Final = (
    "your answer was cut off at the output limit before the output tool call "
    "was complete; give the complete result more briefly: fewer items, short "
    "notes, each quote verbatim but only the sentence that supports the item"
)
"""A retry's feedback when the last answer stopped at ``max_tokens``: a call
cut short is not a schema mistake, and "not a valid call" sent the model back
to write the same length again."""


def stop_reason(messages: Sequence[ModelMessage]) -> Optional[str]:
    """Why the last model response of a run stopped (``"length"`` when it hit
    ``max_tokens``), or ``None`` when no response came back."""
    for message in reversed(messages):
        if isinstance(message, ModelResponse):
            return message.finish_reason
    return None


MAX_FAILURE_ERRORS: Final = 4
MAX_FAILURE_CHARS: Final = 600


def _where(loc: Sequence[Any]) -> str:
    """A validation error's path without Pydantic's own type descriptors
    (``function-before[...]``, ``list[union[...]]``): ``0.ExtractionProposal.
    fields.8.source_ref``.
    """
    return ".".join(
        str(part)
        for part in loc
        if "[" not in str(part) and "function-" not in str(part)
    )


def _relevant_errors(
    errors: Sequence[Mapping[str, Any]], members: frozenset[str]
) -> list[Mapping[str, Any]]:
    """The errors worth showing, most useful first.

    - Pydantic AI validates a wrapped output twice (as sent, and as JSON text,
      ``json[...]`` in the path); the second only repeats the first in a less
      helpful form (it stops at the first raw line break, FCRM-2026-0154), so
      it is shown only when it is the only error.
    - An item of a union output is reported once per member it failed to be.
      Only the member it came closest to (fewest errors) is shown: otherwise the
      four errors shown were "Field required" for members it never meant to be,
      and the real problem (an extra key) never reached the retry.
    """
    direct = [e for e in errors if not any("json[" in str(p) for p in e["loc"])]
    errors = direct or list(errors)

    def member_of(err: Mapping[str, Any]) -> tuple[Optional[int], Optional[str]]:
        loc = list(err["loc"])
        for i, part in enumerate(loc):
            if isinstance(part, int):
                nxt = loc[i + 1] if i + 1 < len(loc) else None
                return part, nxt if nxt in members else None
        return None, None

    counts: dict[tuple[Optional[int], Optional[str]], int] = {}
    for err in errors:
        key = member_of(err)
        counts[key] = counts.get(key, 0) + 1
    closest: dict[Optional[int], str] = {}
    for (index, member), _count in sorted(counts.items(), key=lambda kv: kv[1]):
        if member is not None and index not in closest:
            closest[index] = member
    return [
        err
        for err in errors
        if (key := member_of(err))[1] is None or closest.get(key[0]) == key[1]
    ]


def describe_failure(exc: BaseException, members: frozenset[str] = frozenset()) -> str:
    """One line saying why a model call failed: the error and, when Pydantic AI
    wrapped one (an output that failed validation is reported as "exceeded
    maximum output retries"), what it wrapped. A validation error is shown as
    where each problem is and what it is (the first few, chosen by
    :func:`_relevant_errors`; ``members`` are the agent's output type names),
    not as the repr of the validator it ran: that put the useful part past the
    point the line was cut, in the audit record and in what the retry was shown.
    """
    text = str(exc)
    cause = exc.__cause__
    if isinstance(cause, ValidationError):
        problems = "; ".join(
            f"{where}: {err['msg']}" if (where := _where(err["loc"])) else err["msg"]
            for err in _relevant_errors(cause.errors(), members)[:MAX_FAILURE_ERRORS]
        )
        text = f"{text.split(':')[0]}: {problems}"
    elif cause is not None:
        text = f"{text}: {type(cause).__name__}: {cause}"
    return " ".join(text.split())[:MAX_FAILURE_CHARS]


def _model_name(model: Model) -> str:
    """A loggable model identifier (never a raw model id outside
    ``ai/models/tiers.yaml`` per ADR-04 -- this reads the *runtime*
    object's own name, already sourced from that file). Read through an
    :class:`OutputAllowanceModel`, which names the model it wraps."""
    if isinstance(model, OutputAllowanceModel):
        model = model.wrapped
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
        reservation_id: Optional[str] = None,
        part: int = 1,
        output_floor: int = OUTPUT_FLOOR_TOKENS,
    ) -> None:
        """Hold the job's inputs.

        Args:
            spec: The agent's static definition (tier, tools, output type).
            pack: Its pre-built context pack.
            job_id: H1's job id (``JobPayload.job_id``); the
                ``GuardContext.job_id``, and the budget reservation key unless
                ``reservation_id`` names another.
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
            reservation_id: The budget reservation this run reserves, settles
                and releases (``job_id`` by default; one per part of a
                chunked job, e.g. ``f"{job_id}#p2"``).
            part: Stamped on every ``AttemptRecord`` (1 when the job is not
                split; 0 for a consolidation call).
            output_floor: The smallest output allowance a request is given
                (:class:`~fcrm_agent_worker.allowance.OutputAllowanceModel`).
        """
        self.spec = spec
        self.pack = pack
        self.job_id = job_id
        self.reservation_id = reservation_id or job_id
        self.part = part
        self.output_floor = output_floor
        self.registry = registry
        self.permit = permit
        self.budget = budget
        self.instructions = instructions
        self.models = tier_models(spec.tier, tiers_config)
        self.max_attempts = TIER_BUDGETS[spec.tier].max_attempts
        self.members = frozenset(t.__name__ for t in spec.output_types)
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
        self._output_schema: Optional[dict[str, Any]] = None
        self.base = GuardContext(
            case_id=pack.case_id,
            run_id=pack.run_id,
            job_id=job_id,
            agent=spec.name,
            tier=spec.tier,
            mode=pack.mode,
            role=HARNESS_ROLE,
            reservation_id=self.reservation_id,
        )
        self.result = AgentRunResult(
            agent=spec.name.value,
            case_id=pack.case_id,
            run_id=pack.run_id,
            verdict=RunVerdict.BLOCKED,
            pack_hash=pack.pack_hash,
            pack_token_estimate=pack.token_estimate,
        )

    @property
    def _split_part(self) -> bool:
        """Whether this run answers one part of a split job (not the whole
        input, and not the reduce call that merges the parts)."""
        return self.pack.part_count > 1 and self.pack.part_index > 0

    def _block(self, guard: GuardName, reason: str) -> AgentRunResult:
        self.result.verdict = RunVerdict.BLOCKED
        self.result.stopped_at = guard
        self.result.reason = reason
        return self.result

    async def pre_guards(self) -> Optional[str]:
        """Guards 1-3; the masked user text, or ``None`` if blocked."""
        try:
            self.budget.reserve(self.reservation_id, self.spec.name, self.spec.tier)
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
                "reservation_id": self.reservation_id,
            }
        )
        chain = GuardChain([InputScanGuard(), self.permit, BudgetGuard()])
        results = await chain.run_pre(ctx)
        self.result.pre_guards = results
        if results and not results[-1].passed:
            self.budget.release(self.reservation_id)
            self._block(results[-1].guard, results[-1].summary)
            return None
        masked = results[0].transformed if results else {}
        return "\n\n".join(
            f"## {name}\n{masked.get(name, text)}"
            for name, text in self.pack.sections.items()
        )

    def first_request_estimate(self, user_text: str, tools: Sequence[Any]) -> int:
        """The estimated input of an attempt's first request: instructions,
        user text and the schemas of the output tool and the offered tools
        (the output schema uncompacted, so the estimate errs high)."""
        if self._output_schema is None:
            self._output_schema = TypeAdapter(self.spec.output_type()).json_schema()
        schemas = [
            self._output_schema,
            *(tool.function_schema.json_schema for tool in tools),
        ]
        return estimate_prompt_tokens(self.instructions, user_text, schemas=schemas)

    async def call_model(
        self, attempt: int, user_text: str
    ) -> tuple[Optional[list[BaseModel]], AttemptRecord]:
        """One live model call for ``attempt``.

        Each request's ``max_tokens`` is what the attempt's budget leaves
        (:class:`~fcrm_agent_worker.allowance.OutputAllowanceModel`). An
        answer that fails the output tool's validation gets one repair inside
        the attempt (Pydantic AI shows the model its validation errors) when
        the budget can hold a second request of the same size; otherwise the
        attempt fails as before and attempt 2 runs on the fallback chain.
        Pydantic AI's own token limit is the attempt budget plus one request's
        output, so a valid answer that tips the attempt over its budget is
        kept (and annotated) rather than discarded; the whole call is bounded
        by the tier's wall clock.
        """
        plan = attempt_plan(self.spec.tier, attempt)
        record = AttemptRecord(
            attempt=attempt, slot=plan.slot.value, guard_results=[], part=self.part
        )
        inner = self.model_factory(self.models, plan)
        self.model_used = _model_name(inner)
        settings = model_settings_for(
            self.models, max_tokens_factor=plan.max_tokens_factor
        )
        tier_max = int(settings.get("max_tokens") or self.models.max_tokens)
        log_extra = {
            "agent": self.spec.name.value,
            "attempt": attempt,
            "part": self.part,
        }
        model = OutputAllowanceModel(
            inner,
            attempt_limit=plan.token_limit,
            tier_max=tier_max,
            floor=self.output_floor,
            log_extra=log_extra,
            cut_off_note=CUT_OFF_FEEDBACK,
        )
        tools = tool_functions(self.spec, self.registry) if self.tool_call_cap else []
        estimated_first = self.first_request_estimate(user_text, tools)
        repair_allowed = 2 * estimated_first + self.output_floor <= plan.token_limit
        agent: Agent[AgentDeps, Any] = Agent(
            model,
            output_type=ToolOutput(
                self.spec.output_type(), name=f"{self.spec.name.value}_output"
            ),
            instructions=self.instructions,
            model_settings=settings,
            retries=AgentRetries(tools=0, output=1 if repair_allowed else 0),
            tools=tools,
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
        messages: list[ModelMessage] = []
        try:
            async with asyncio.timeout(plan.wall_s):
                with capture_run_messages() as messages:
                    run = await agent.run(
                        user_text,
                        deps=deps,
                        usage=usage,
                        usage_limits=UsageLimits(
                            total_tokens_limit=plan.token_limit + tier_max,
                            request_limit=request_limit(self.tool_call_cap)
                            + (1 if repair_allowed else 0),
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
            # the chain failed; a TimeoutError is the tier's wall clock). A
            # non-final attempt is a retry candidate, same as a Guard 4
            # schema rejection; the last attempt blocks.
            status = (
                GuardStatus.RETRY
                if attempt < self.max_attempts
                else GuardStatus.BLOCKED
            )
            stop = stop_reason(messages)
            detail = describe_failure(exc, self.members)
            if isinstance(exc, TimeoutError) and not str(exc):
                detail = f"no answer within the {plan.wall_s}-second wall clock"
            if stop == "length":
                cap = model.allowances[-1].allowance if model.allowances else tier_max
                detail = f"output cut off at the {cap}-token limit; {detail}"
            logger.warning(
                "model call failed (%s, attempt %d, stop %s): %s",
                self.spec.name.value,
                attempt,
                stop,
                detail,
                extra={**log_extra, "wall_s": plan.wall_s},
            )
            if isinstance(exc, UnexpectedModelBehavior):
                self.feedback = [
                    CUT_OFF_FEEDBACK
                    if stop == "length"
                    else "your answer was not a valid call of the output tool "
                    f"({detail[:FEEDBACK_CHARS]}); call it once with the "
                    "complete result, its list as a JSON array (not a string)"
                ]
            record.guard_results.append(
                GuardResult(
                    guard=GuardName.OUTPUT_SHAPE,
                    status=status,
                    summary=f"attempt {attempt}: model call failed: {detail}",
                    details={"error": type(exc).__name__, "stop_reason": stop},
                    audit_type=AUDIT_TYPES[GuardName.OUTPUT_SHAPE],
                )
            )
            return None, record
        finally:
            record.allowances = [a.as_record() for a in model.allowances]
            self._bill(record, usage)
        self._note_overrun(attempt, plan.token_limit, record, log_extra)
        return list(run.output), record

    def _note_overrun(
        self,
        attempt: int,
        limit: int,
        record: AttemptRecord,
        log_extra: Mapping[str, Any],
    ) -> None:
        """Annotate an attempt whose valid answer took it over its budget.

        The answer is kept (its tokens are spent either way, and discarding it
        meant a budget block with no retry); the overrun is recorded as a
        Guard 3 annotation, audited with the attempt, and logged.
        """
        overrun = record.tokens_used - limit
        if overrun <= 0:
            return
        record.guard_results.append(
            GuardResult(
                guard=GuardName.BUDGET,
                status=GuardStatus.ANNOTATED,
                summary=(
                    f"attempt {attempt}: overran its {limit}-token attempt budget "
                    f"by {overrun}; answer kept"
                ),
                details={
                    "limit": limit,
                    "spent": record.tokens_used,
                    "overrun": overrun,
                },
                audit_type=AUDIT_TYPES[GuardName.BUDGET],
            )
        )
        logger.warning(
            "attempt overran its budget (%s, attempt %d): %d over %d; answer kept",
            self.spec.name.value,
            attempt,
            overrun,
            limit,
            extra={**log_extra, "limit": limit, "overrun": overrun},
        )

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
                # The audit record has the detail; the log said nothing of why
                # attempt 1 failed (FCRM-2026-0154's first refusal was lost).
                logger.warning(
                    "output refused (%s, attempt %d), retrying: %s",
                    self.spec.name.value,
                    attempt,
                    shape.summary,
                    extra={
                        "agent": self.spec.name.value,
                        "attempt": attempt,
                        "part": self.part,
                    },
                )
                return None
            self._block(GuardName.OUTPUT_SHAPE, f"{shape.summary}; no attempt left")
            return False
        if (
            shape.status is GuardStatus.BLOCKED
            and shape.details.get("empty") is True
            and not last
            and not self._split_part
        ):
            # An empty answer from an agent whose contract needs items is most
            # often a model that answered in text or called the output tool with
            # nothing (Sonnet 5.5 is not forced to call it); the next attempt is
            # told so. A part of a split job may legitimately hold nothing to
            # report, so there the empty answer stands and the merge tolerates it.
            # The reduce call that merges the parts (part 0) always has
            # something to merge, so its empty answer is retried too.
            self.feedback = [EMPTY_ANSWER_FEEDBACK]
            logger.warning(
                "empty answer (%s, attempt %d), retrying",
                self.spec.name.value,
                attempt,
                extra={
                    "agent": self.spec.name.value,
                    "attempt": attempt,
                    "part": self.part,
                },
            )
            return None
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

    async def run_all(self) -> AgentRunResult:
        """The whole run: guards 1-3 once, then attempts up to the tier's
        ``max_attempts`` (each followed by guards 4-5) until one finishes or
        blocks, then the reservation settled with every attempt's tokens.
        Returns :attr:`result` (also readable afterwards with
        :attr:`model_used` and :attr:`usage_total`)."""
        user_text = await self.pre_guards()
        if user_text is not None:
            for attempt in range(self.starting_attempt, self.max_attempts + 1):
                done = await self.attempt(
                    attempt, user_text, last=attempt == self.max_attempts
                )
                if done is not None:
                    break
            self.budget.settle(self.reservation_id, self.result.tokens_used)
        logger.info(
            "live agent run finished",
            extra={
                "agent": self.spec.name.value,
                "part": self.part,
                "verdict": self.result.verdict.value,
                "attempts": len(self.result.attempts),
                # Not named "tokens": the log scrubber drops any key with "token" in it.
                "spent": self.result.tokens_used,
                "usage": {
                    key.removesuffix("_tokens"): value
                    for key, value in self.usage_total.items()
                },
            },
        )
        return self.result


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
    reservation_id: Optional[str] = None,
    output_floor: int = OUTPUT_FLOOR_TOKENS,
) -> tuple[AgentRunResult, str, UsageBreakdown]:
    """Run one job live through the real guard chain (module doc).

    Returns ``(result, model_used, usage)``: ``result`` is exactly what
    ``run_agent_offline`` would hand H1 (same type, same verdict
    semantics); ``model_used`` is the last attempt's and ``usage`` is the whole
    job's, every attempt included, for ``AgentDone.model``/``AgentDone.usage``
    (H1 sums it into the case's token total). ``reservation_id`` and
    ``output_floor`` pass through to :class:`LiveRun`.
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
        reservation_id=reservation_id,
        output_floor=output_floor,
    )
    result = await run.run_all()
    return result, run.model_used, run.usage_total
