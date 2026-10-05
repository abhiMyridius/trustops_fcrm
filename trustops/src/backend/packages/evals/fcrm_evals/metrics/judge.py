"""The judge-metric seam (design B §7.3 judge rows, §7.4 judge).

:class:`JudgeMetric` is the interface every LLM-judged metric implements:
``async def score(output, context) -> float`` in ``[0, 1]``.
:class:`RubricJudgeMetric` is the one concrete implementation. It never
talks to a model itself: it builds a :class:`JudgeRequest` (the rubric's
weighted criteria, the row's input facts, the output text) and awaits an
**injected** judge callable ``k`` times (design B: k = 3 votes, criteria
averaged, pass decided on the mean).

- In production the callable is the Bedrock judge (a Pydantic AI agent on
  the judge tier returning per-criterion scores). That wiring belongs to a
  later lane: the judge prompt must live under ``ai/prompts`` and model ids
  only in ``ai/models/tiers.yaml``, neither of which this package owns.
- Offline (``make evals`` without AWS, and every unit test) the callable is
  :class:`FakeJudge`: **a deterministic heuristic stand-in, not a model and
  not a measurement of judge quality**. It exists so the runner, the
  aggregation and the gate can be exercised end to end today.

Only the rubric *criteria and weights* are defined here (design B §7.3's
``narrative_faithfulness`` row), not prompt text.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Final, Optional, Protocol, runtime_checkable

from fcrm_contracts.proposals.narrative import Narrative
from pydantic import BaseModel, ConfigDict, Field

from fcrm_evals.context import EvalContext
from fcrm_evals.metrics.base import MetricSpec, Observation, ratio
from fcrm_evals.outputs import AgentOutput
from fcrm_evals.rows import EvalRow
from fcrm_evals.text import content_words, numbers_in, sentences, strings_in

logger = logging.getLogger(__name__)


class JudgeUnavailableError(RuntimeError):
    """A judge could not grade an item (a transport error, an unparseable
    verdict). The metric records the item as *not measured* rather than as a
    pass or a fail, and says so in its flags."""


class Rubric(BaseModel):
    """Weighted criteria and the per-item pass mark."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rubric_id: str
    criteria: dict[str, float]
    pass_mark: float = Field(ge=0.0, le=1.0)


NARRATIVE_FAITHFULNESS: Final[Rubric] = Rubric(
    rubric_id="narrative_faithfulness",
    criteria={"traceable": 0.5, "no_invented_numbers": 0.3, "emphasis": 0.2},
    pass_mark=0.8,
)
"""Design B §7.3: every claim traceable to the inputs (0.5), no invented or
altered number (0.3), emphasis on the biggest real driver (0.2); pass >= 0.8
per case; threshold >= 0.90 of cases."""


class JudgeContext(BaseModel):
    """What the judge may see besides the output: the row's input facts."""

    model_config = ConfigDict(extra="forbid")

    row_id: str
    facts: dict[str, Any]
    allowed_numbers: list[str] = Field(default_factory=list)
    top_driver: Optional[str] = None

    @classmethod
    def from_row(cls, row: EvalRow) -> JudgeContext:
        """The row's inputs minus its source list, plus its grounding labels."""
        return cls(
            row_id=row.row_id,
            facts={k: v for k, v in row.input.items() if k != "sources"},
            allowed_numbers=list(row.label("allowed_numbers", [])),
            top_driver=row.label("top_driver"),
        )


class JudgeRequest(BaseModel):
    """One judge call: rubric, context and the text to grade."""

    model_config = ConfigDict(extra="forbid")

    rubric: Rubric
    context: JudgeContext
    output_text: str
    vote: int = Field(ge=0)


JudgeFn = Callable[[JudgeRequest], Awaitable[Mapping[str, float]]]
"""Returns a score in [0, 1] per rubric criterion."""


@runtime_checkable
class JudgeMetric(Protocol):
    """An LLM-judged metric (design B §7.3 ``kind = judge``)."""

    name: str
    threshold: float

    async def score(self, output: BaseModel, context: JudgeContext) -> float:
        """Grade one output item; returns a value in [0, 1]."""
        ...


def text_of(output: BaseModel) -> str:
    """The gradable text of a proposal item (its ``text`` field)."""
    value = getattr(output, "text", None)
    if not isinstance(value, str):
        msg = f"{type(output).__name__} has no text to judge"
        raise TypeError(msg)
    return value


class RubricJudgeMetric:
    """k-vote rubric judging over an injected judge callable."""

    def __init__(
        self,
        name: str,
        rubric: Rubric,
        judge: JudgeFn,
        *,
        threshold: float,
        k: int = 3,
    ) -> None:
        """Bind a rubric to a judge callable; ``k`` votes per item."""
        if k < 1:
            msg = "k must be at least 1"
            raise ValueError(msg)
        self.name = name
        self.rubric = rubric
        self.judge = judge
        self.threshold = threshold
        self.k = k

    def _weighted(self, verdict: Mapping[str, float]) -> float:
        total = sum(self.rubric.criteria.values())
        return (
            sum(
                weight * min(1.0, max(0.0, float(verdict.get(name, 0.0))))
                for name, weight in self.rubric.criteria.items()
            )
            / total
        )

    async def score(self, output: BaseModel, context: JudgeContext) -> float:
        """Mean weighted rubric score over ``k`` judge votes."""
        text = text_of(output)
        votes = []
        for vote in range(self.k):
            request = JudgeRequest(
                rubric=self.rubric, context=context, output_text=text, vote=vote
            )
            votes.append(self._weighted(await self.judge(request)))
        return sum(votes) / len(votes)


class FakeJudge:
    """Offline stand-in for the Bedrock judge (heuristics, not a model).

    Deterministic: the same score for every vote. Criteria:

    - ``traceable``: share of sentences that share a content word (4+
      letters) with the input facts;
    - ``no_invented_numbers``: 1 when every number in the text is in
      ``allowed_numbers`` (or appears in the facts), else 0;
    - ``emphasis``: 1 when the labelled top driver is named, else 0.
    """

    async def __call__(self, request: JudgeRequest) -> Mapping[str, float]:
        """Score ``request.output_text`` against its context."""
        context = request.context
        facts_words = content_words(" ".join(strings_in(context.facts)))
        parts = sentences(request.output_text)
        traced = [p for p in parts if content_words(p) & facts_words]
        allowed = set(context.allowed_numbers) | set(
            numbers_in(strings_in(context.facts))
        )
        numbers = set(numbers_in([request.output_text]))
        driver = context.top_driver
        return {
            "traceable": len(traced) / len(parts) if parts else 0.0,
            "no_invented_numbers": 1.0 if numbers <= allowed else 0.0,
            "emphasis": (
                1.0
                if driver is None or driver.casefold() in request.output_text.casefold()
                else 0.0
            ),
        }


def judge_metric_spec(
    metric: RubricJudgeMetric, *, item_type: type[BaseModel] = Narrative
) -> MetricSpec:
    """Adapt a judge metric to the runner: per row, items of ``item_type``
    scoring >= the rubric's pass mark / items judged."""

    async def _fn(
        row: EvalRow, output: AgentOutput, context: EvalContext
    ) -> Optional[Observation]:
        del context
        items = output.of_type(item_type)
        judge_ctx = JudgeContext.from_row(row)
        scores: list[float] = []
        unavailable: list[str] = []
        for item in items:
            try:
                scores.append(await metric.score(item, judge_ctx))
            except JudgeUnavailableError as exc:
                logger.warning("row %s: judge unavailable: %s", row.row_id, exc)
                unavailable.append(f"judge unavailable: {exc}")
        passed = [s for s in scores if s >= metric.rubric.pass_mark]
        flags = [
            f"judge {s:.2f} < {metric.rubric.pass_mark}"
            for s in scores
            if s not in passed
        ]
        return ratio(len(passed), len(scores), [*flags, *unavailable])

    return MetricSpec(
        name=metric.name,
        fn=_fn,
        threshold=metric.threshold,
        threshold_source="design_b",
        description=f"{metric.rubric.rubric_id} via injected judge (k={metric.k})",
        kind="llm_judge",
    )
