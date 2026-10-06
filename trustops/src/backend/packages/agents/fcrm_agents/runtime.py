"""The offline-runnable agent runtime (design B §5.1-§5.2).

:func:`run_agent_offline` runs one agent job end to end with **no network**:
the model is a Pydantic AI ``FunctionModel`` that returns a fixed output
through the agent's real output tool (so the output round-trips through
Pydantic AI's own JSON validation), and everything around it is the real
thing:

1. **Pre-guards** (``fcrm_guardrails`` Guards 1-3, in their fixed order):
   input scan + PII masking over every pack section (the masked text is
   what the model sees); OPA tool permit for the agent's allow-list (an
   injectable :class:`~fcrm_guardrails.OPAClient`; offline:
   :class:`StaticPermitClient` over ``ai/policies/tool_permits.json``);
   budget against a :class:`~fcrm_guardrails.CaseBudget` reservation.
2. **The model call** under ``UsageLimits`` (tier tokens, ``1 +
   tool_call_cap`` requests) with the agent's tools registered as Pydantic
   AI tools (each call permit-checked inside the tool function; a denial or
   tool error is returned to the model as an error, design B §4.3).
3. **Post-guards**: Guard 4 (output shape: schema, sources resolve, quotes
   verbatim) and then Guard 5 (the eight rules) on what survives it.

Attempts follow Guard 3's plan (``attempt_plan``): attempt 2 exists only
for a Guard 4 ``retry``, runs on the fallback slot with a halved
``max_tokens`` hint, and on it claims failing *only* the verbatim check are
dropped (flow node I6D). An offline run given no ``retry_output`` is a
single-attempt run: a fixed function model has nothing new to say, so a
Guard 4 retry then has no attempt left and the job is **blocked**
(``agent.done{failed_output}``; H1 opens the handoff gate), which is exactly
what H2 does when a tier's retries are used up.

Guards 6 (critic scheduling) and 7 (human gate) are H1's, not run here.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, Optional, Union

from fcrm_contracts.enums import Tier
from fcrm_contracts.versions import PinnedVersions
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
    OPAClient,
    OutputShapeGuard,
    PermitGuard,
    RulesGuard,
    ScanText,
    attempt_plan,
)
from fcrm_guardrails.base import AUDIT_TYPES
from fcrm_guardrails.context import AvailableSource
from fcrm_tools import ToolError, ToolRegistry, build_seed_registry
from pydantic import BaseModel, ConfigDict, Field, SerializeAsAny, ValidationError
from pydantic_ai import (
    Agent,
    ModelSettings,
    RunContext,
    Tool,
    ToolOutput,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
    UsageLimits,
)
from pydantic_ai.capabilities import AbstractCapability, PrepareOutputTools
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.tools import ToolDefinition

from fcrm_agents.builders import POLICY_INDEX, PRECEDENT_INDEX
from fcrm_agents.context_pack import ContextPack
from fcrm_agents.spec import REGISTRY, AgentSpec
from fcrm_agents.tool_sources import sources_from_result

logger = logging.getLogger(__name__)

HARNESS_ROLE: Final = "fcrm_service"
"""The role H2 calls tools as. ``ai/policies/tools.rego`` permits only
``caller_roles: [fcrm_service]``; ``GuardContext.role`` defaults to
``"agent"``, which that policy would deny (reconciliation item)."""

DEFAULT_PERMITS_FILE: Final = Path("ai/policies/tool_permits.json")
_OUTPUT_INDEX: Final = re.compile(r"^outputs\[(\d+)\]")
OFFLINE_MAX_TOKENS: Final[Mapping[Tier, int]] = {
    Tier.CHEAP: 4000,
    Tier.MID: 4000,
    Tier.STRONG: 8000,
    Tier.JUDGE: 8000,
}
"""Output caps per tier (``ai/models/tiers.yaml max_tokens``) for the
offline ``ModelSettings``; the live path reads them from the file."""


class RunVerdict(StrEnum):
    """How an agent run ended."""

    PASS = "pass"
    PASS_WITH_DROPS = "pass_with_drops"
    BLOCKED = "blocked"


class StaticPermitClient:
    """An in-process :class:`~fcrm_guardrails.OPAClient` that evaluates the
    same data OPA does (``tool_permits.json``: agent -> model tools, caller
    roles). Offline stand-in for ``fcrm_common.authz``; never allows by
    default."""

    def __init__(
        self,
        permits: Mapping[str, Collection[str]],
        *,
        caller_roles: Collection[str] = (HARNESS_ROLE,),
    ) -> None:
        """Allow ``permits[agent]`` to callers in ``caller_roles``."""
        self._permits = {agent: frozenset(tools) for agent, tools in permits.items()}
        self._roles = frozenset(caller_roles)

    @classmethod
    def from_registry(cls) -> StaticPermitClient:
        """Permits from :data:`fcrm_agents.spec.REGISTRY` (model tools)."""
        return cls({name.value: spec.tools for name, spec in REGISTRY.items()})

    @classmethod
    def from_file(cls, path: Path = DEFAULT_PERMITS_FILE) -> StaticPermitClient:
        """Permits from ``ai/policies/tool_permits.json``.

        Raises:
            FileNotFoundError: the file is absent.
            ValueError: the file is not the expected shape.
        """
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
        try:
            block = data["tool_permits"]
            permits = {p["agent"]: p["tools"] for p in block["permits"]}
            roles = block["caller_roles"]
        except (KeyError, TypeError) as exc:
            msg = f"{path}: not a tool_permits document"
            raise ValueError(msg) from exc
        return cls(permits, caller_roles=roles)

    async def tool_permit(
        self, *, agent: str, tool: str, case_id: str, role: str, run_id: str
    ) -> bool:
        """``True`` only for a listed agent/tool pair and caller role."""
        return role in self._roles and tool in self._permits.get(agent, frozenset())


@dataclass
class AgentDeps:
    """What a tool function needs at call time (Pydantic AI ``deps``)."""

    case_id: str
    run_id: str
    agent: str
    registry: ToolRegistry
    permit: PermitGuard
    versions: PinnedVersions
    tools_called: list[str] = field(default_factory=list)
    """The tools the model called, in order (refused calls too)."""
    tool_sources: list[AvailableSource] = field(default_factory=list)
    """What the tool results returned to *this model call* let the agent cite
    (:func:`fcrm_agents.tool_sources.sources_from_result`). One ``AgentDeps``
    serves one call, so the list is scoped to it: Guard 4 resolves a citation
    against the pack and these, never against a tool the agent did not call."""


SCOPE_ARGUMENTS: Final = ("case_id", "run_id", "controls_v", "fieldset_v")
"""Tool arguments H2 fills from the job, never the model: the case and run it
belongs to and the pinned versions. A model asked for them gets them wrong
(or names another case); a tool call is scoped by construction instead of
refused afterwards, and the model's schema is shorter."""

FIXED_ARGUMENTS: Final[Mapping[str, Mapping[str, str]]] = {
    "policy_search": {"index": POLICY_INDEX},
    "precedent_search": {"index": PRECEDENT_INDEX},
}
"""Tool arguments the agent spec, not the model, pins (two tools share one
search request shape and differ only by the index they read)."""


def compact_schema(node: Any) -> Any:
    """``node`` (a JSON Schema) without what costs tokens and tells a model
    nothing: ``title`` keywords (they repeat the field name) and ``default``
    ``null``. A *property* called ``title`` is kept: its value is a schema (a
    mapping), where the keyword's is a string."""
    if isinstance(node, dict):
        return {
            k: compact_schema(v)
            for k, v in node.items()
            if not (k == "title" and isinstance(v, str))
            and not (k == "default" and v is None)
        }
    if isinstance(node, list):
        return [compact_schema(v) for v in node]
    return node


async def compact_output_tools(
    ctx: RunContext[Any], tool_defs: list[ToolDefinition]
) -> list[ToolDefinition]:
    """The output tool's definition with its JSON schema compacted
    (:func:`compact_schema`): 200 to 300 tokens off every request of every agent.
    Only what the model is shown changes; the output is still validated against
    the contract's own types."""
    return [
        replace(td, parameters_json_schema=compact_schema(td.parameters_json_schema))
        for td in tool_defs
    ]


def agent_capabilities() -> list[AbstractCapability[Any]]:
    """The Pydantic AI capabilities every agent runs with (offline, live and
    :func:`fcrm_agents.models.build_agent` alike)."""
    return [PrepareOutputTools(compact_output_tools)]


def scope_values(name: str, deps: AgentDeps) -> dict[str, Any]:
    """The arguments of tool ``name`` that the job fixes (see
    :data:`SCOPE_ARGUMENTS`, :data:`FIXED_ARGUMENTS`)."""
    values: dict[str, Any] = {
        "case_id": deps.case_id,
        "run_id": deps.run_id,
        "controls_v": deps.versions.controls_v,
        "fieldset_v": deps.versions.fieldset_v,
    }
    values.update(FIXED_ARGUMENTS.get(name, {}))
    return values


def model_schema(name: str, input_model: type[BaseModel]) -> dict[str, Any]:
    """The input schema the model sees for tool ``name``: the input model's,
    compacted, without the arguments the job fills in."""
    schema = compact_schema(input_model.model_json_schema())
    hidden = {*SCOPE_ARGUMENTS, *FIXED_ARGUMENTS.get(name, {})}
    properties = schema.get("properties", {})
    for key in hidden & set(properties):
        del properties[key]
    if "required" in schema:
        schema["required"] = [k for k in schema["required"] if k not in hidden]
        if not schema["required"]:
            del schema["required"]
    return dict(schema)


def _tool_function(name: str, registry: ToolRegistry) -> Tool[AgentDeps]:
    tool = registry.get(name)
    hidden = {*SCOPE_ARGUMENTS, *FIXED_ARGUMENTS.get(name, {})} & set(
        tool.input_model.model_fields
    )

    async def call_tool(ctx: RunContext[AgentDeps], **arguments: Any) -> dict[str, Any]:
        deps = ctx.deps
        deps.tools_called.append(name)
        decision = await deps.permit.decide(
            agent=deps.agent,
            tool=name,
            case_id=deps.case_id,
            role=HARNESS_ROLE,
            run_id=deps.run_id,
        )
        if not decision.allowed:
            logger.warning(
                "tool call denied", extra={"tool": name, "reason": decision.reason}
            )
            return {"error": f"not permitted: {decision.reason}"}
        scoped = scope_values(name, deps)
        try:
            request = tool.input_model.model_validate(
                {**arguments, **{k: scoped[k] for k in hidden}}
            )
            result = await tool.call(request)
        except (ValidationError, ToolError) as exc:
            logger.info(
                "tool call refused: %s: %s",
                name,
                str(exc)[:300],
                extra={"tool": name, "error": str(exc)},
            )
            return {"error": f"{type(exc).__name__}: {exc}"}
        deps.tool_sources.extend(
            sources_from_result(
                name, request, result, case_id=deps.case_id, versions=deps.versions
            )
        )
        return result.model_dump(mode="json")

    return Tool.from_schema(
        call_tool,
        name=name,
        description=(tool.input_model.__doc__ or f"The read-only {name} tool.").strip(),
        json_schema=model_schema(name, tool.input_model),
        takes_ctx=True,
    )


def tool_functions(spec: AgentSpec, registry: ToolRegistry) -> list[Tool[AgentDeps]]:
    """The agent's model-callable tools, and only those (``spec.tools``), or none
    when its ``tool_call_cap`` is 0: the pack is complete by construction (design
    B §3.2), and a model that is offered a tool it does not need calls it anyway
    (the first live runs: Haiku on every intake row), each call re-sending the
    whole request.
    """
    if spec.tool_call_cap == 0:
        return []
    return [_tool_function(name, registry) for name in spec.tools]


ToolCallPlan = Sequence[tuple[str, dict[str, Any]]]


def fixed_output_model(
    outputs: Sequence[BaseModel], *, tool_calls: ToolCallPlan = ()
) -> FunctionModel:
    """A ``FunctionModel`` that first makes ``tool_calls`` (one per request)
    and then returns ``outputs`` through the agent's output tool."""
    payload = [o.model_dump(mode="json") for o in outputs]

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        responses = sum(1 for m in messages if isinstance(m, ModelResponse))
        if responses < len(tool_calls):
            name, args = tool_calls[responses]
            return ModelResponse(parts=[ToolCallPart(tool_name=name, args=args)])
        output_tool = info.output_tools[0]
        properties = output_tool.parameters_json_schema.get("properties", {})
        key = next(iter(properties)) if len(properties) == 1 else "response"
        return ModelResponse(
            parts=[ToolCallPart(tool_name=output_tool.name, args={key: payload})]
        )

    return FunctionModel(respond, model_name="offline-fixed-output")


class AttemptRecord(BaseModel):
    """One attempt: its slot, guard results and usage."""

    model_config = ConfigDict(extra="forbid")

    attempt: int
    slot: str
    guard_results: list[GuardResult]
    tokens_used: int = 0
    tool_calls: int = 0
    tools_called: list[str] = Field(default_factory=list)
    """The tools the model called in this attempt, in order (a refused call
    is listed too: it cost a request)."""


class AgentRunResult(BaseModel):
    """The outcome of one agent job (what H2 publishes on ``agent.done``)."""

    model_config = ConfigDict(extra="forbid")

    agent: str
    case_id: str
    run_id: str
    verdict: RunVerdict
    stopped_at: Optional[GuardName] = None
    reason: Optional[str] = None
    pre_guards: list[GuardResult] = Field(default_factory=list)
    attempts: list[AttemptRecord] = Field(default_factory=list)
    accepted: list[SerializeAsAny[BaseModel]] = Field(default_factory=list)
    dropped: list[str] = Field(default_factory=list)
    notes_for_analyst: list[str] = Field(default_factory=list)
    pack_hash: str
    pack_token_estimate: int
    tokens_used: int = 0

    @property
    def guard_results(self) -> list[GuardResult]:
        """Every guard result in order (pre-guards, then each attempt)."""
        return [*self.pre_guards, *(r for a in self.attempts for r in a.guard_results)]


def _schema_failure(attempt: int, error: Exception) -> GuardResult:
    status = GuardStatus.RETRY if attempt == 1 else GuardStatus.BLOCKED
    return GuardResult(
        guard=GuardName.OUTPUT_SHAPE,
        status=status,
        summary=f"attempt {attempt}: output failed schema validation",
        details={"error": str(error)[:500], "attempt": attempt},
        audit_type=AUDIT_TYPES[GuardName.OUTPUT_SHAPE],
    )


def _drop(
    outputs: Sequence[BaseModel], item_ids: Sequence[str]
) -> tuple[list[BaseModel], list[str]]:
    indexes = {int(m.group(1)) for item in item_ids if (m := _OUTPUT_INDEX.match(item))}
    kept = [o for i, o in enumerate(outputs) if i not in indexes]
    return kept, [f"outputs[{i}]" for i in sorted(indexes)]


class OfflineRun:
    """One offline job: the context and the attempt loop."""

    def __init__(
        self,
        spec: AgentSpec,
        pack: ContextPack,
        *,
        registry: ToolRegistry,
        permit: PermitGuard,
        budget: CaseBudget,
        instructions: Optional[str],
        tool_calls: ToolCallPlan,
    ) -> None:
        """Hold the job's inputs."""
        self.spec = spec
        self.pack = pack
        self.registry = registry
        self.permit = permit
        self.budget = budget
        self.instructions = instructions
        self.tool_calls = tool_calls
        self.reservation_id = f"{pack.run_id}:{spec.name.value}"
        self.tool_sources: list[AvailableSource] = []
        self.base = GuardContext(
            case_id=pack.case_id,
            run_id=pack.run_id,
            job_id=self.reservation_id,
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
        """Guards 1-3; returns the masked user text, or ``None`` if blocked."""
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

    async def call_model(
        self, outputs: Sequence[BaseModel], attempt: int, user_text: str
    ) -> tuple[Optional[list[BaseModel]], AttemptRecord]:
        """One model call through the agent's real output tool."""
        plan = attempt_plan(self.spec.tier, attempt)
        record = AttemptRecord(attempt=attempt, slot=plan.slot.value, guard_results=[])
        agent: Agent[AgentDeps, Any] = Agent(
            fixed_output_model(
                outputs, tool_calls=self.tool_calls if attempt == 1 else ()
            ),
            output_type=ToolOutput(
                self.spec.output_type(), name=f"{self.spec.name.value}_output"
            ),
            instructions=self.instructions,
            deps_type=AgentDeps,
            retries=0,
            tools=tool_functions(self.spec, self.registry),
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
        )
        self.tool_sources = deps.tool_sources
        record.tools_called = deps.tools_called
        max_tokens = int(
            OFFLINE_MAX_TOKENS[Tier(self.spec.tier)] * plan.max_tokens_factor
        )
        try:
            run = await agent.run(
                user_text,
                deps=deps,
                usage_limits=UsageLimits(
                    total_tokens_limit=plan.token_limit,
                    request_limit=1 + self.spec.tool_call_cap,
                ),
                model_settings=ModelSettings(
                    max_tokens=max_tokens, timeout=float(plan.wall_s)
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
        except UnexpectedModelBehavior as exc:
            record.guard_results.append(_schema_failure(attempt, exc))
            return None, record
        record.tokens_used = run.usage.input_tokens + run.usage.output_tokens
        record.tool_calls = run.usage.tool_calls
        return list(run.output), record

    async def post_guards(
        self,
        outputs: list[BaseModel],
        attempt: int,
        record: AttemptRecord,
        *,
        last: bool,
    ) -> Optional[bool]:
        """Guard 4 then Guard 5. ``True`` finished, ``False`` blocked,
        ``None`` retry on the next attempt."""
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
        self, outputs: Sequence[BaseModel], attempt: int, user_text: str, *, last: bool
    ) -> Optional[bool]:
        """Run one attempt end to end (see :meth:`post_guards`)."""
        produced, record = await self.call_model(outputs, attempt, user_text)
        self.result.attempts.append(record)
        self.result.tokens_used += record.tokens_used
        if produced is None:
            failure = record.guard_results[-1]
            if last or failure.guard is GuardName.BUDGET:
                self._block(failure.guard, failure.summary)
                return False
            return None
        return await self.post_guards(produced, attempt, record, last=last)


async def run_agent_offline(
    spec: AgentSpec,
    context_pack: ContextPack,
    fixed_output: Union[BaseModel, Sequence[BaseModel]],
    *,
    retry_output: Optional[Union[BaseModel, Sequence[BaseModel]]] = None,
    registry: Optional[ToolRegistry] = None,
    permit_client: Optional[OPAClient] = None,
    budget: Optional[CaseBudget] = None,
    instructions: Optional[str] = None,
    tool_calls: ToolCallPlan = (),
) -> AgentRunResult:
    """Run one agent job offline through the real guard chain (module doc).

    ``fixed_output`` is what the fake model returns on attempt 1;
    ``retry_output`` (if given) on attempt 2. ``tool_calls`` makes the fake
    model call tools first (each goes through the permit check and the real
    tool). ``registry`` (the tools the model may call, as Pydantic AI
    tools) defaults to the seed-backed registry.
    """
    first = (
        [fixed_output] if isinstance(fixed_output, BaseModel) else list(fixed_output)
    )
    second: Optional[list[BaseModel]] = None
    if retry_output is not None:
        second = (
            [retry_output]
            if isinstance(retry_output, BaseModel)
            else list(retry_output)
        )
    run = OfflineRun(
        spec,
        context_pack,
        registry=registry if registry is not None else build_seed_registry(),
        permit=PermitGuard(
            permit_client or StaticPermitClient.from_registry(),
            allow_list={spec.name.value: spec.tools},
        ),
        budget=budget or CaseBudget(context_pack.case_id),
        instructions=instructions,
        tool_calls=tool_calls,
    )
    user_text = await run.pre_guards()
    if user_text is None:
        return run.result
    plans = [(1, first)] + ([(2, second)] if second is not None else [])
    for index, (attempt, outputs) in enumerate(plans):
        done = await run.attempt(
            outputs, attempt, user_text, last=index == len(plans) - 1
        )
        if done is not None:
            break
    run.budget.settle(run.reservation_id, run.result.tokens_used)
    logger.info(
        "offline agent run finished",
        extra={"agent": spec.name.value, "verdict": run.result.verdict.value},
    )
    return run.result
