"""DeepEval's judge metrics as runner metrics.

For the agents whose output is prose (narrative, examiner, critic) three
DeepEval metrics grade each row with the judge model:

=================  =====================================================  =======
metric             what it asks                                           passes
=================  =====================================================  =======
faithfulness       is every claim in the output supported by the context   >= 0.90
answer_relevancy   is the output relevant to what was asked                >= 0.80
hallucination_free does the output contradict the context?                 >= 0.90
=================  =====================================================  =======

**``hallucination_free`` and the direction of DeepEval's hallucination
score.** The brief asked for the hallucination metric to be reported as
``1 - score >= 0.90``, which is right when ``score`` is the fraction of
contradicted contexts (lower is better), as it was before DeepEval 4. In the
pinned DeepEval (4.2.8) ``HallucinationMetric`` already scores like every
other metric (1 is a pass; its own notice says so, and a probe with a
deliberately contradicting output scored 0.5), so ``1 - old_score`` *is* the
new ``score``. It is reported as that ``score`` under the name
``hallucination_free`` and checked ``>= 0.90``, i.e. at most 10% of the
contexts contradicted: the same bar as the brief.

Each row is one observation, ``score / 1``, so a metric's aggregate is the
mean score over the rows that were judged; its flags carry DeepEval's own
reason for a row that fell below the threshold. A row the judge could not
grade (a transport error, a verdict it could not parse) is *not measured*,
counted in :attr:`DeepEvalJudge.failures` and logged, never scored.
"""

from __future__ import annotations

import asyncio
import logging
import warnings
from collections.abc import Awaitable, Callable
from typing import Any, Final, Optional, Protocol

from deepeval.metrics import (
    AnswerRelevancyMetric,
    FaithfulnessMetric,
    HallucinationMetric,
)
from deepeval.test_case import LLMTestCase
from fcrm_contracts.enums import AgentName

from fcrm_evals.context import EvalContext
from fcrm_evals.judge.cases import JUDGED_AGENTS, JudgeCase, judge_case
from fcrm_evals.judge.llm import JUDGE_ERRORS
from fcrm_evals.live import RowPackBuilder
from fcrm_evals.metrics.base import MetricSpec, Observation
from fcrm_evals.outputs import AgentOutput
from fcrm_evals.rows import EvalRow

logger = logging.getLogger(__name__)

FAITHFULNESS: Final[str] = "faithfulness"
ANSWER_RELEVANCY: Final[str] = "answer_relevancy"
HALLUCINATION_FREE: Final[str] = "hallucination_free"
THRESHOLDS: Final[dict[str, float]] = {
    FAITHFULNESS: 0.90,
    ANSWER_RELEVANCY: 0.80,
    HALLUCINATION_FREE: 0.90,
}
DESCRIPTIONS: Final[dict[str, str]] = {
    FAITHFULNESS: "claims in the output supported by what the agent was shown "
    "(DeepEval FaithfulnessMetric)",
    ANSWER_RELEVANCY: "share of the output that is relevant to the task "
    "(DeepEval AnswerRelevancyMetric)",
    HALLUCINATION_FREE: "1 - the share of contexts the output contradicts "
    "(DeepEval HallucinationMetric, already scored 1 = pass)",
}


class Measurable(Protocol):
    """The slice of a DeepEval metric this module uses."""

    score: Optional[float]
    reason: Optional[str]

    def a_measure(self, test_case: Any, *args: Any, **kwargs: Any) -> Awaitable[float]:
        """Score ``test_case``."""
        ...


MetricFactory = Callable[[str], Measurable]


def deepeval_factory(llm: Any) -> MetricFactory:
    """A factory of fresh DeepEval metrics on ``llm``. A metric keeps its
    score and reason on itself, so one is built per measurement, never
    shared between rows scored concurrently."""

    def build(name: str) -> Measurable:
        common: dict[str, Any] = {
            "model": llm,
            "include_reason": True,
            "async_mode": True,
            "verbose_mode": False,
        }
        if name == FAITHFULNESS:
            return FaithfulnessMetric(threshold=THRESHOLDS[name], **common)
        if name == ANSWER_RELEVANCY:
            return AnswerRelevancyMetric(threshold=THRESHOLDS[name], **common)
        with warnings.catch_warnings():
            # The notice says the score now reads 1 = pass and the threshold is
            # a minimum; both are how this module already uses it (see above).
            warnings.simplefilter("ignore", DeprecationWarning)
            return HallucinationMetric(
                threshold=THRESHOLDS[HALLUCINATION_FREE], **common
            )

    return build


class DeepEvalJudge:
    """Builds the three judge metrics of each judged agent and counts the rows
    it could not grade."""

    def __init__(self, packs: RowPackBuilder, factory: MetricFactory) -> None:
        """Grade against the pack ``packs`` rebuilds for each row, with the
        metrics ``factory`` makes."""
        self._packs = packs
        self._factory = factory
        self.failures = 0
        self._cases: dict[tuple[str, str], Optional[JudgeCase]] = {}
        self._lock = asyncio.Lock()

    async def _case(self, row: EvalRow, output: AgentOutput) -> Optional[JudgeCase]:
        key = (row.agent.value, row.row_id)
        async with self._lock:
            if key not in self._cases:
                pack = await self._packs.build(row)
                self._cases[key] = judge_case(row.agent, row, output, pack)
        return self._cases[key]

    async def measure(self, name: str, case: JudgeCase) -> Optional[Observation]:
        """One metric on one case (``None``: the judge could not grade it)."""
        test_case = LLMTestCase(
            input=case.input,
            actual_output=case.actual_output,
            retrieval_context=list(case.retrieval_context),
            context=list(case.retrieval_context),
        )
        metric = self._factory(name)
        try:
            await metric.a_measure(test_case, _show_indicator=False)
        except JUDGE_ERRORS:
            logger.exception("judge could not grade a row on %s", name)
            self.failures += 1
            return None
        score = float(metric.score if metric.score is not None else 0.0)
        flags = (
            [f"{name} {score:.2f} < {THRESHOLDS[name]:.2f}: {metric.reason}"]
            if score < THRESHOLDS[name]
            else []
        )
        return Observation(numerator=score, denominator=1.0, flags=flags)

    def _spec(self, name: str) -> MetricSpec:
        async def fn(
            row: EvalRow, output: AgentOutput, context: EvalContext
        ) -> Optional[Observation]:
            del context
            case = await self._case(row, output)
            return None if case is None else await self.measure(name, case)

        return MetricSpec(
            name=name,
            fn=fn,
            threshold=THRESHOLDS[name],
            threshold_source="proposed",
            description=DESCRIPTIONS[name],
            kind="llm_judge",
        )

    def specs(self, agent: AgentName) -> tuple[MetricSpec, ...]:
        """The judge metrics of ``agent`` (none for an agent whose output is
        not prose)."""
        if agent not in JUDGED_AGENTS:
            return ()
        return tuple(
            self._spec(name)
            for name in (FAITHFULNESS, ANSWER_RELEVANCY, HALLUCINATION_FREE)
        )
