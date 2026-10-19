"""Running a job whose input was split into parts (ADR-77, ADR-78).

:meth:`fcrm_agents.context_pack.ContextPackBuilder.build_parts` returns one pack
when the job's input fits its tier, and several when it does not. This module
runs them:

- **One pack**: :func:`~fcrm_agent_worker.live_runtime.run_agent_live`, exactly
  as before parts existed.
- **Several packs**: each part is its own :class:`~fcrm_agent_worker.
  live_runtime.LiveRun` (pre-guards, attempts, Guards 4-5 against that part's
  sources) under its own budget reservation (``<job_id>#p<i>``), one after the
  other. A part that is blocked for any reason other than an empty answer stops
  the job: the remaining parts would be spent on a job that fails anyway. The
  answers are then merged by code (:func:`fcrm_agents.consolidate.merge_parts`,
  :func:`~fcrm_agents.consolidate.merge_singletons`), or, for the narrative
  recommendation, by one call on the reduce tier (:mod:`fcrm_agent_worker.
  reduce`).

The job's usage is the sum over every part and the reduce call, so H1 settles
what the job really spent; ``model_used`` names each model that answered, once.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Optional

from fcrm_agents.consolidate import (
    SINGLETON_AGENTS,
    PartResult,
    is_empty,
    merge_parts,
    merge_singletons,
    needs_reduce,
)
from fcrm_agents.context_pack import ContextPack
from fcrm_agents.runtime import AgentRunResult, RunVerdict
from fcrm_agents.spec import AgentSpec
from fcrm_contracts.enums import Tier
from fcrm_guardrails import CaseBudget, PermitGuard
from fcrm_guardrails.context import AvailableSource
from fcrm_tools import ToolRegistry

from fcrm_agent_worker.allowance import OUTPUT_FLOOR_TOKENS
from fcrm_agent_worker.live_runtime import (
    USAGE_KEYS,
    LiveRun,
    ModelFactory,
    UsageBreakdown,
    live_model_for_slot,
)
from fcrm_agent_worker.reduce import run_reduce

logger = logging.getLogger(__name__)


@dataclass
class PartsOutcome:
    """What a job's run produced, whether or not its input was split."""

    result: AgentRunResult
    model_used: str
    usage: UsageBreakdown
    parts: list[PartResult] = field(default_factory=list)
    """Every part that ran (empty when the input was not split)."""
    failed_pack: Optional[ContextPack] = None
    """The input to keep for the analyst when the job failed: the part that
    failed, or the reduce call's partial answers."""


def add_usage(total: UsageBreakdown, more: Mapping[str, int]) -> None:
    """Add ``more`` into ``total``, key by key."""
    for key in USAGE_KEYS:
        total[key] = total.get(key, 0) + int(more.get(key, 0))


def models_named(names: Sequence[str]) -> str:
    """Every model that answered, once each, in order (``a + b``)."""
    return " + ".join(dict.fromkeys(n for n in names if n))


def parts_summary(parts: Sequence[PartResult]) -> list[dict[str, Any]]:
    """``AgentDone.output["parts"]``: what each part did and cost."""
    return [
        {
            "part": p.index,
            "count": p.count,
            "attempts": len(p.result.attempts),
            "verdict": p.result.verdict.value,
            "accepted": len(p.result.accepted),
            "dropped": len(p.result.dropped),
            "usage": dict(p.usage),
        }
        for p in parts
    ]


async def run_agent_parts(
    spec: AgentSpec,
    packs: Sequence[ContextPack],
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
    output_floor: int = OUTPUT_FLOOR_TOKENS,
    reduce_tier: Tier = Tier.CHEAP,
    reduce_instructions: Optional[str] = None,
    on_part_done: Optional[Callable[[], Awaitable[None]]] = None,
) -> PartsOutcome:
    """Run ``packs`` (one job's input, split or not) and return one outcome.

    Args:
        spec: The agent.
        packs: What :meth:`~fcrm_agents.context_pack.ContextPackBuilder.
            build_parts` returned; never empty.
        job_id: H1's job id.
        registry: The tools.
        permit: Guard 2.
        budget: The job's ledger; each part and the reduce call reserve under
            their own id in it.
        instructions: The agent's rendered system prompt.
        tiers_config: ``ai/models/tiers.yaml``.
        model_factory: Builds the model for an attempt.
        tool_call_cap: H2's cap on model tool calls (each part has its own).
        starting_attempt: ``JobPayload.attempt``.
        output_floor: ``AGENT_OUTPUT_FLOOR_TOKENS``.
        reduce_tier: ``AGENT_REDUCE_TIER``, the narrative reduce call's tier.
        reduce_instructions: The reduce call's rendered system prompt.
        on_part_done: Awaited after each part (H2 renews the job's lease).

    Raises:
        ValueError: ``packs`` is empty.
    """
    if not packs:
        msg = "a job has at least one pack"
        raise ValueError(msg)
    common: dict[str, Any] = {
        "registry": registry,
        "permit": permit,
        "budget": budget,
        "tiers_config": tiers_config,
        "model_factory": model_factory,
        "tool_call_cap": tool_call_cap,
        "starting_attempt": starting_attempt,
        "output_floor": output_floor,
    }
    if len(packs) == 1:
        run = LiveRun(spec, packs[0], job_id, instructions=instructions, **common)
        result = await run.run_all()
        failed = None if _passed(result) else packs[0]
        return PartsOutcome(result, run.model_used, run.usage_total, failed_pack=failed)

    parts: list[PartResult] = []
    tool_sources: list[AvailableSource] = []
    usage: UsageBreakdown = {}
    models: list[str] = []
    for pack in packs:
        run = LiveRun(
            spec,
            pack,
            job_id,
            instructions=instructions,
            reservation_id=f"{job_id}#p{pack.part_index}",
            part=pack.part_index,
            **common,
        )
        result = await run.run_all()
        parts.append(
            PartResult(
                index=pack.part_index,
                count=pack.part_count,
                pack=pack,
                result=result,
                model_used=run.model_used,
                usage=dict(run.usage_total),
            )
        )
        tool_sources.extend(run.tool_sources)
        add_usage(usage, run.usage_total)
        models.append(run.model_used)
        if on_part_done is not None:
            await on_part_done()
        if result.verdict is RunVerdict.BLOCKED and not is_empty(result):
            logger.warning(
                "part blocked; the job's remaining parts are not run",
                extra={
                    "agent": spec.name.value,
                    "part": pack.part_index,
                    "parts": pack.part_count,
                },
            )
            merged = merge_parts(spec, parts)
            return PartsOutcome(
                merged, models_named(models), usage, parts, failed_pack=pack
            )

    if needs_reduce(spec, packs[0].mode):
        reduced = await run_reduce(
            spec,
            parts,
            job_id,
            tool_sources=tool_sources,
            instructions=reduce_instructions,
            tier=reduce_tier,
            **common,
        )
        add_usage(usage, reduced.usage)
        models.append(reduced.model_used)
        return PartsOutcome(
            reduced.result,
            models_named(models),
            usage,
            parts,
            failed_pack=None if _passed(reduced.result) else reduced.pack,
        )

    merge = merge_singletons if spec.name in SINGLETON_AGENTS else merge_parts
    merged = merge(spec, parts)
    return PartsOutcome(
        merged,
        models_named(models),
        usage,
        parts,
        failed_pack=None if _passed(merged) else packs[0],
    )


def _passed(result: AgentRunResult) -> bool:
    return result.verdict in (RunVerdict.PASS, RunVerdict.PASS_WITH_DROPS)
