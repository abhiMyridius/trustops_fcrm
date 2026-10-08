"""What one agent job cost, as metrics (ADR-72).

The worker records, once per job and only here (never in ``fcrm_agents`` or
``live_runtime``, which evals and the demo scripts also run, so those runs do
not count as production use):

- ``fcrm_agent_tokens_total{agent, model, tier, direction}``: ``input`` is the
  uncached part, ``cache_read``/``cache_write`` are separate, ``output`` is
  output, so a plain sum is the billed total;
- ``fcrm_agent_cost_usd_total{agent, model, tier}`` at the list prices in
  ``ai/models/tiers.yaml`` (:class:`fcrm_agents.pricing.PriceTable`);
- ``fcrm_agent_latency_ms{agent, tier, outcome}``: the job's wall time;
- ``fcrm_guard_results_total{guard, result}``: every guard verdict;
- ``fcrm_budget_used_ratio{agent}``: tokens / the tier's per-attempt budget.

``model`` is the label ``tiers.yaml`` gives the model that actually answered,
read from each response by :class:`AnsweringModelMeter` (a ``FallbackModel``'s
own name joins every model in the chain, which is neither true nor countable).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final, Optional

from fcrm_agents.models import TierModels
from fcrm_agents.pricing import PriceTable
from fcrm_agents.runtime import AgentRunResult, RunVerdict
from fcrm_common.metrics import FcrmMetrics
from fcrm_guardrails.budget import TIER_BUDGETS, AttemptPlan
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings

from fcrm_agent_worker.live_runtime import ModelFactory

__all__ = [
    "OUTCOME_CONTEXT_PACK_ERROR",
    "AnsweringModelMeter",
    "JobUsage",
    "job_outcome",
    "metered_factory",
    "record_job_metrics",
]

logger = logging.getLogger(__name__)

OUTCOME_CONTEXT_PACK_ERROR: Final = "context_pack_error"
"""The outcome of a job whose context pack could not be built (no model call)."""


@dataclass
class _ModelUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass
class JobUsage:
    """Tokens per answering model id, summed over every request of one job."""

    by_model: dict[str, _ModelUsage] = field(default_factory=dict)

    def add(
        self,
        model_id: str,
        *,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int,
        cache_write_tokens: int,
    ) -> None:
        """Add one response's usage to ``model_id``'s running total."""
        usage = self.by_model.setdefault(model_id, _ModelUsage())
        usage.input_tokens += max(0, input_tokens)
        usage.output_tokens += max(0, output_tokens)
        usage.cache_read_tokens += max(0, cache_read_tokens)
        usage.cache_write_tokens += max(0, cache_write_tokens)


class AnsweringModelMeter(WrapperModel):
    """Forwards every request and adds the answer's usage to a :class:`JobUsage`
    under the id of the model that answered (for a ``FallbackModel``, the child
    that did). Changes nothing about the request or the response."""

    def __init__(self, wrapped: Model, usage: JobUsage) -> None:
        """Wrap ``wrapped``; ``usage`` receives one entry per response."""
        super().__init__(wrapped)
        self._usage = usage

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: Optional[ModelSettings],
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        """Forward the request; record the usage of the response."""
        response = await self.wrapped.request(
            messages, model_settings, model_request_parameters
        )
        usage = response.usage
        self._usage.add(
            response.model_name or self.wrapped.model_name,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            cache_write_tokens=usage.cache_write_tokens,
        )
        return response


def metered_factory(base: ModelFactory, usage: JobUsage) -> ModelFactory:
    """``base`` with every model it builds wrapped in :class:`AnsweringModelMeter`."""

    def factory(models: TierModels, plan: AttemptPlan) -> Model:
        return AnsweringModelMeter(base(models, plan), usage)

    return factory


def job_outcome(result: AgentRunResult) -> str:
    """A small, closed set: ``done``, ``done_with_drops``, or the guard that
    stopped the run (``permit``, ``output_shape`` ...), else ``blocked``."""
    if result.verdict is RunVerdict.PASS:
        return "done"
    if result.verdict is RunVerdict.PASS_WITH_DROPS:
        return "done_with_drops"
    return result.stopped_at.value if result.stopped_at is not None else "blocked"


def record_job_metrics(
    metrics: FcrmMetrics,
    prices: PriceTable,
    *,
    agent: str,
    tier: str,
    elapsed_ms: float,
    outcome: str,
    usage: Optional[JobUsage] = None,
    result: Optional[AgentRunResult] = None,
) -> None:
    """Record one finished job. Never raises into the job: a metric that
    cannot be recorded is logged and dropped (the job's outcome stands)."""
    try:
        _record(metrics, prices, agent, tier, elapsed_ms, outcome, usage, result)
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        logger.warning(
            "agent job metrics not recorded",
            extra={"metric_error": type(exc).__name__, "agent": agent},
        )


def _record(
    metrics: FcrmMetrics,
    prices: PriceTable,
    agent: str,
    tier: str,
    elapsed_ms: float,
    outcome: str,
    usage: Optional[JobUsage],
    result: Optional[AgentRunResult],
) -> None:
    metrics.agent_latency_ms.record(
        max(0.0, elapsed_ms), {"agent": agent, "tier": tier, "outcome": outcome}
    )
    for model_id, used in (usage.by_model if usage else {}).items():
        labels = {"agent": agent, "model": prices.label(model_id), "tier": tier}
        uncached = max(
            0, used.input_tokens - used.cache_read_tokens - used.cache_write_tokens
        )
        for direction, tokens in _directions(uncached, used).items():
            if tokens:
                metrics.agent_tokens_total.add(
                    tokens, {**labels, "direction": direction}
                )
        cost = prices.cost(
            model_id,
            input_tokens=used.input_tokens,
            output_tokens=used.output_tokens,
            cache_read_tokens=used.cache_read_tokens,
            cache_write_tokens=used.cache_write_tokens,
        )
        if cost > 0:
            metrics.agent_cost_usd_total.add(cost, labels)
    if result is None:
        return
    for guard in result.guard_results:
        metrics.guard_results_total.add(
            1,
            {"guard": guard.guard.value, "result": "pass" if guard.passed else "block"},
        )
    budget = _tier_tokens().get(tier)
    if budget:
        metrics.budget_used_ratio.set(result.tokens_used / budget, {"agent": agent})


def _directions(uncached: int, used: _ModelUsage) -> Mapping[str, int]:
    return {
        "input": uncached,
        "output": used.output_tokens,
        "cache_read": used.cache_read_tokens,
        "cache_write": used.cache_write_tokens,
    }


def _tier_tokens() -> Mapping[str, int]:
    return {str(tier.value): budget.tokens for tier, budget in TIER_BUDGETS.items()}
