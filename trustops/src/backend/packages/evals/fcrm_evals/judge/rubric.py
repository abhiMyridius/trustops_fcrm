"""The real judge behind ``narrative_faithfulness``.

:class:`~fcrm_evals.metrics.judge.RubricJudgeMetric` (design B §7.3: every
claim traceable 0.5, no invented number 0.3, emphasis on the real driver 0.2;
pass at 0.8; k votes) takes any async callable that scores one criterion set.
Offline that callable is the heuristic ``FakeJudge``. :class:`BedrockRubricJudge`
is the live one: one judge-tier call per vote, with the rubric text coming from
the prompt ``narrative.judge`` under ``ai/prompts`` (never from code), asked for
one JSON verdict.

Each vote is an independent call, so ``--judge-votes K`` spends K calls per
narrative row and averages away the judge's own noise.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final, Optional, Union

from fcrm_prompts import PromptLibrary
from pydantic import BaseModel

from fcrm_evals.instructions import DEFAULT_AI_ROOT
from fcrm_evals.judge.llm import JUDGE_ERRORS, BedrockJudgeLLM
from fcrm_evals.metrics.judge import JudgeRequest, JudgeUnavailableError

logger = logging.getLogger(__name__)

PROMPT_ID: Final[str] = "narrative.judge"


class RubricVerdict(BaseModel):
    """The judge's reply: a 0 to 1 score per criterion, and why. Out-of-range
    values are clamped by the metric that weights them."""

    traceable: float
    no_invented_numbers: float
    emphasis: float
    reasoning: str = ""


def facts_text(facts: Mapping[str, Any]) -> str:
    """The facts a narrative was written from, one ``key: value`` per line."""
    return "\n".join(
        f"{key}: {json.dumps(value, ensure_ascii=False)}"
        for key, value in sorted(facts.items())
    )


class BedrockRubricJudge:
    """A :data:`~fcrm_evals.metrics.judge.JudgeFn` over the judge model."""

    def __init__(
        self,
        llm: BedrockJudgeLLM,
        *,
        ai_root: Union[str, Path, None] = None,
        library: Optional[PromptLibrary] = None,
    ) -> None:
        """Judge with ``llm``, reading the rubric prompt from ``ai_root``."""
        self._llm = llm
        self._library = library or PromptLibrary(
            ai_root if ai_root is not None else DEFAULT_AI_ROOT
        )

    def prompt_for(self, request: JudgeRequest) -> str:
        """The rendered judge prompt for one vote."""
        context = request.context
        return self._library.render(
            PROMPT_ID,
            {
                "facts": facts_text(context.facts),
                "allowed_numbers": context.allowed_numbers,
                "top_driver": context.top_driver or "",
                "text": request.output_text,
            },
        ).text

    async def __call__(self, request: JudgeRequest) -> Mapping[str, float]:
        """One vote.

        Raises:
            JudgeUnavailableError: the call failed or the verdict did not parse.
        """
        prompt = self.prompt_for(request)
        try:
            verdict, _cost = await self._llm.a_generate(prompt, schema=RubricVerdict)
        except JUDGE_ERRORS as exc:
            logger.warning("rubric judge call failed: %s", type(exc).__name__)
            raise JudgeUnavailableError(f"{type(exc).__name__}: {exc}") from exc
        scores = verdict.model_dump()
        return {name: float(scores[name]) for name in request.rubric.criteria}
