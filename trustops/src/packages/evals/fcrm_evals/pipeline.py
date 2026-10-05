"""Dataset + producer + suite -> result, and the offline self-check.

:func:`evaluate_agent` is what ``make evals`` calls per agent.
:func:`selfcheck` is the proof the machinery measures something: for every
agent it scores the reference (echo) outputs, which must be perfect, and
the degraded outputs, which must be worse; then it gates echo as a
candidate over a degraded baseline (an improvement: must PASS) and
degraded as a candidate over the echo baseline (a regression: must FAIL).
It returns the results, the gate verdicts and whether every expectation
held.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from fcrm_contracts.enums import AgentName

from fcrm_evals.context import EvalContext
from fcrm_evals.datasets import dataset_path, load
from fcrm_evals.metrics import suite_for
from fcrm_evals.metrics.judge import JudgeFn
from fcrm_evals.producers import (
    DegradedProducer,
    EchoProducer,
    OutputProducer,
    produce_all,
)
from fcrm_evals.runner import EvalResult, GateResult, GateStatus, arun, gate_detail

logger = logging.getLogger(__name__)

JUDGE_TOLERANCE = 0.01
"""Design B §6.4: judge metrics tolerate 0.01; deterministic metrics 0."""


async def evaluate_agent(
    agent: AgentName,
    producer: OutputProducer,
    *,
    judge: Optional[JudgeFn] = None,
    context: Optional[EvalContext] = None,
    timestamp: Optional[datetime] = None,
) -> EvalResult:
    """Run ``agent``'s full metric suite on its bundled dataset with outputs
    from ``producer``."""
    suite = suite_for(agent, judge)
    outputs = await produce_all(producer, load(agent))
    return await arun(
        agent,
        dataset_path(agent),
        suite.metrics,
        outputs,
        primary_metric=suite.primary,
        context=context,
        timestamp=timestamp,
    )


def tolerance_for(result: EvalResult) -> float:
    """0.01 when the primary metric is judge-graded, else 0."""
    summary = result.summary(result.primary_metric or "")
    return JUDGE_TOLERANCE if summary and summary.kind == "llm_judge" else 0.0


@dataclass
class SelfCheck:
    """Everything a self-check run produced."""

    results: list[EvalResult] = field(default_factory=list)
    gates: dict[str, GateResult] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """All self-check expectations held."""
        return not self.problems


def _echo_problems(result: EvalResult) -> list[str]:
    return [
        f"{result.agent.value} echo {s.name} = {s.score}"
        for s in result.summaries
        if s.score is not None and s.score < 1.0
    ]


async def selfcheck(
    agents: Sequence[AgentName] = tuple(AgentName),
    *,
    timestamp: Optional[datetime] = None,
) -> SelfCheck:
    """Echo must be perfect and pass the gate over degraded; degraded must
    fail the gate over echo."""
    check = SelfCheck()
    context = EvalContext()
    for agent in agents:
        echo = await evaluate_agent(
            agent, EchoProducer(), context=context, timestamp=timestamp
        )
        worse = await evaluate_agent(
            agent, DegradedProducer(), context=context, timestamp=timestamp
        )
        check.results += [echo, worse]
        check.problems += _echo_problems(echo)
        primary = echo.primary_metric or ""
        improved = gate_detail(echo, worse, primary, tolerance_for(echo))
        regressed = gate_detail(worse, echo, primary, tolerance_for(worse))
        check.gates[f"{agent.value}: echo vs degraded baseline"] = improved
        check.gates[f"{agent.value}: degraded vs echo baseline"] = regressed
        if improved.status is not GateStatus.PASS:
            check.problems.append(f"{agent.value}: an improvement failed the gate")
        if regressed.status is not GateStatus.FAIL:
            check.problems.append(f"{agent.value}: degraded output passed the gate")
    for problem in check.problems:
        logger.error("self-check: %s", problem)
    return check
