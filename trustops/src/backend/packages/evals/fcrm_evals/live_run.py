"""One live eval run over the nine agents.

For each agent: draw the sampled rows, run the agent live on each (one
Bedrock call per attempt, under the runtime's own budgets), score the outputs
with the 27 deterministic metrics plus the LLM-judge metrics where faithfulness
matters, and keep the usage. Agents run together under one semaphore
(``--concurrency``), so a run's wall time is bounded by the slowest rows and
its in-flight Bedrock calls by one number.

Failures are isolated per row and counted: a row whose guard blocks the run, or
whose run raises, is scored on an empty output and shows in
``RunUsage.blocked_rows`` / ``failed_rows``; a judge call that fails marks that
judge metric *not measured* for the row. Nothing here retries beyond the
runtime's own single retry.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Literal, Optional

from fcrm_agent_worker.live_runtime import ModelFactory, live_model_for_slot
from fcrm_contracts.enums import AgentName

from fcrm_evals.datasets import dataset_path, load
from fcrm_evals.instructions import DEFAULT_AI_ROOT, InstructionsMode
from fcrm_evals.live import (
    DEFAULT_ROW_TIMEOUT_S,
    LiveProducer,
    RowPackBuilder,
    lifted_token_budgets,
)
from fcrm_evals.metrics import suite_for
from fcrm_evals.metrics.base import MetricSpec
from fcrm_evals.metrics.judge import JudgeFn
from fcrm_evals.outputs import AgentOutput, dump_outputs
from fcrm_evals.producers import produce_all
from fcrm_evals.results import OUTPUTS_SUFFIX
from fcrm_evals.rows import EvalRow
from fcrm_evals.runner import EvalResult, arun
from fcrm_evals.sampling import SampleInfo, sample_rows
from fcrm_evals.usage import PriceTable, RunUsage, UsageMeter, usage_of_rows

logger = logging.getLogger(__name__)

JudgeMode = Literal["bedrock", "fake"]
DEFAULT_SAMPLE: Final[int] = 5
DEFAULT_SEED: Final[int] = 7
DEFAULT_CONCURRENCY: Final[int] = 3
DEFAULT_JUDGE_VOTES: Final[int] = 3


@dataclass(frozen=True)
class LiveRunConfig:
    """Everything that shapes a live run."""

    agents: tuple[AgentName, ...] = tuple(AgentName)
    sample: Optional[int] = DEFAULT_SAMPLE
    seed: int = DEFAULT_SEED
    row_ids: tuple[str, ...] = ()
    concurrency: int = DEFAULT_CONCURRENCY
    row_timeout_s: float = DEFAULT_ROW_TIMEOUT_S
    judge: JudgeMode = "bedrock"
    judge_votes: int = DEFAULT_JUDGE_VOTES
    instructions: InstructionsMode = "rendered"
    token_budget_factor: float = 1.0
    """Diagnostic: scale the per-attempt token limits (see
    :func:`fcrm_evals.live.lifted_token_budgets`). 1.0 is the shipped budget."""
    ai_root: Path = DEFAULT_AI_ROOT
    spool: Optional[Path] = None
    """Where each agent's outputs are written the moment they exist, before any
    scoring, so a failure while grading never costs the model calls again."""


@dataclass(frozen=True)
class JudgeKit:
    """What an agent's scoring gets from the judge: extra metrics, the judge
    behind ``narrative_faithfulness`` (``None``: the offline stand-in), and
    a callable that reports how many rows the judge could not grade."""

    specs: tuple[MetricSpec, ...] = ()
    judge_fn: Optional[JudgeFn] = None
    failures: Callable[[], int] = lambda: 0


JudgeFactory = Callable[
    [AgentName, UsageMeter, RowPackBuilder, "LiveRunConfig"], JudgeKit
]


def fake_judge_kit(
    agent: AgentName, meter: UsageMeter, packs: RowPackBuilder, config: LiveRunConfig
) -> JudgeKit:
    """No LLM judge: ``narrative_faithfulness`` uses the heuristic stand-in
    and the DeepEval metrics are absent. For unit tests and offline CI; the
    result says ``fake``."""
    del agent, meter, packs, config
    return JudgeKit()


def bedrock_judge_kit(
    agent: AgentName, meter: UsageMeter, packs: RowPackBuilder, config: LiveRunConfig
) -> JudgeKit:
    """The Bedrock judge: DeepEval's metrics for the judged agents, and the
    rubric judge behind ``narrative_faithfulness``. Both run on the ``judge``
    tier and are metered on ``meter``."""
    from fcrm_agents.models import load_tiers_config

    from fcrm_evals.judge.cases import JUDGED_AGENTS
    from fcrm_evals.judge.deepeval_metrics import DeepEvalJudge, deepeval_factory
    from fcrm_evals.judge.llm import BedrockJudgeLLM, JudgeSettings
    from fcrm_evals.judge.rubric import BedrockRubricJudge

    if agent not in JUDGED_AGENTS:
        return JudgeKit()
    settings = JudgeSettings.from_tiers(load_tiers_config(config.ai_root))
    llm = BedrockJudgeLLM(settings, meter)
    judge = DeepEvalJudge(packs, deepeval_factory(llm))
    rubric = (
        BedrockRubricJudge(llm, ai_root=config.ai_root)
        if agent is AgentName.NARRATIVE
        else None
    )
    return JudgeKit(
        specs=judge.specs(agent), judge_fn=rubric, failures=lambda: judge.failures
    )


@dataclass
class LiveRunOutcome:
    """What a live run produced."""

    config: LiveRunConfig
    timestamp: datetime
    results: list[EvalResult] = field(default_factory=list)
    outputs: dict[AgentName, list[AgentOutput]] = field(default_factory=dict)
    tiers: Mapping[str, Any] = field(default_factory=dict)


def _row_ids_for(agent: AgentName, config: LiveRunConfig) -> tuple[str, ...]:
    """The ``--row-ids`` this agent's dataset has (an id belongs to one)."""
    if not config.row_ids:
        return ()
    have = {row.row_id for row in load(agent)}
    return tuple(rid for rid in config.row_ids if rid in have)


async def _run_agent(
    agent: AgentName,
    config: LiveRunConfig,
    *,
    model_factory: ModelFactory,
    judge_factory: JudgeFactory,
    produce_gate: asyncio.Semaphore,
    judge_gate: asyncio.Semaphore,
    timestamp: datetime,
) -> Optional[tuple[EvalResult, list[AgentOutput]]]:
    wanted = _row_ids_for(agent, config)
    if config.row_ids and not wanted:
        logger.info("%s: none of --row-ids is in its dataset; skipped", agent.value)
        return None
    rows, info = sample_rows(
        agent,
        load(agent),
        n=config.sample,
        seed=config.seed,
        row_ids=wanted or None,
    )
    producer = LiveProducer.for_agent(
        agent,
        ai_root=config.ai_root,
        model_factory=model_factory,
        instructions=config.instructions,
    )
    outputs = await produce_all(
        producer,
        rows,
        row_timeout_s=config.row_timeout_s,
        isolate=True,
        semaphore=produce_gate,
    )
    if config.spool is not None:
        config.spool.mkdir(parents=True, exist_ok=True)
        dump_outputs(outputs, config.spool / f"{agent.value}.{OUTPUTS_SUFFIX}")
    result = await score_agent(
        agent,
        config,
        rows,
        info,
        outputs,
        usage_of_rows(o.run for o in outputs if o.run is not None),
        packs=producer.packs,
        prices=producer.prices,
        judge_factory=judge_factory,
        semaphore=judge_gate,
        timestamp=timestamp,
    )
    return result, outputs


async def score_agent(
    agent: AgentName,
    config: LiveRunConfig,
    rows: Sequence[EvalRow],
    info: SampleInfo,
    outputs: Sequence[AgentOutput],
    usage: Optional[RunUsage],
    *,
    packs: RowPackBuilder,
    prices: PriceTable,
    judge_factory: JudgeFactory,
    semaphore: asyncio.Semaphore,
    timestamp: datetime,
) -> EvalResult:
    """Score one agent's ``outputs`` on ``rows``: its deterministic metrics, and
    the judge's where the judge applies. Used by a live run once the agent has
    produced, and by a re-grade of a saved run (no agent call is made here)."""
    meter = UsageMeter(prices)
    kit = judge_factory(agent, meter, packs, config)
    suite = suite_for(agent, kit.judge_fn, votes=config.judge_votes, extra=kit.specs)
    result = await arun(
        agent,
        dataset_path(agent),
        suite.metrics,
        outputs,
        primary_metric=suite.primary,
        rows=rows,
        semaphore=semaphore,
        usage=usage,
        sampled=info,
        judge=config.judge,
        timestamp=timestamp,
    )
    if meter.calls or kit.failures():
        judged = meter.as_usage().model_copy(update={"failed_rows": kit.failures()})
        result = result.model_copy(update={"judge_usage": judged})
    return result


async def run_live(
    config: LiveRunConfig,
    *,
    model_factory: ModelFactory = live_model_for_slot,
    judge_factory: Optional[JudgeFactory] = None,
    timestamp: Optional[datetime] = None,
) -> LiveRunOutcome:
    """Run ``config.agents`` live and score them (module docstring).

    Args:
        config: What to run.
        model_factory: Builds a model per attempt (Bedrock by default; a fake
            in unit tests).
        judge_factory: Builds an agent's judge (default: by ``config.judge``).
        timestamp: Pins the result timestamp.
    """
    from fcrm_agents.models import load_tiers_config

    stamp = timestamp or datetime.now(UTC)
    chosen = judge_factory or (
        bedrock_judge_kit if config.judge == "bedrock" else fake_judge_kit
    )
    produce_gate = asyncio.Semaphore(max(1, config.concurrency))
    judge_gate = asyncio.Semaphore(max(1, config.concurrency))
    with lifted_token_budgets(config.token_budget_factor):
        ran = await asyncio.gather(
            *(
                _run_agent(
                    agent,
                    config,
                    model_factory=model_factory,
                    judge_factory=chosen,
                    produce_gate=produce_gate,
                    judge_gate=judge_gate,
                    timestamp=stamp,
                )
                for agent in config.agents
            )
        )
    outcome = LiveRunOutcome(
        config=config, timestamp=stamp, tiers=load_tiers_config(config.ai_root)
    )
    for agent, item in zip(config.agents, ran, strict=True):
        if item is None:
            continue
        outcome.results.append(item[0])
        outcome.outputs[agent] = item[1]
    return outcome


__all__ = [
    "JudgeKit",
    "LiveRunConfig",
    "LiveRunOutcome",
    "bedrock_judge_kit",
    "fake_judge_kit",
    "run_live",
    "score_agent",
]
