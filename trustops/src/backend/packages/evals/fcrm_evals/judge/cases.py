"""What a judge reads for one row of a judged agent.

DeepEval grades an ``actual_output`` against an ``input`` (is it relevant?)
and a body of ``retrieval_context`` (is it supported? does it contradict?).
For the three agents whose output is prose a deterministic metric cannot
grade, those three parts are:

- narrative: ``input`` is the facts the agent was shown; ``actual_output`` the
  recommendation, the conditions and the business summary.
- examiner: ``input`` is the question; ``actual_output`` the answer's
  sentences.
- critic: ``input`` is the proposals, rules and evidence dates shown;
  ``actual_output`` the findings.

``retrieval_context`` is every line of the pack (for the examiner, every audit
record).

The context is the *pack the agent was shown* (rebuilt from the row by
:class:`~fcrm_evals.live.RowPackBuilder`), not the dataset's full row: a fact
the agent was never given cannot be held against it. Packs are a few hundred
tokens (the examiner's 67 records excepted), so nothing is truncated here
beyond :data:`MAX_CONTEXT_LINES`.

Nothing here talks to a model.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Final, Optional

from fcrm_agents.context_pack import ContextPack
from fcrm_contracts.enums import AgentName
from fcrm_contracts.proposals.critic import CriticFinding
from fcrm_contracts.proposals.examiner import ExaminerAnswer
from fcrm_contracts.proposals.narrative import BusinessSummary, Condition, Narrative

from fcrm_evals.outputs import AgentOutput
from fcrm_evals.rows import EvalRow

logger = logging.getLogger(__name__)

JUDGED_AGENTS: Final[tuple[AgentName, ...]] = (
    AgentName.NARRATIVE,
    AgentName.EXAMINER,
    AgentName.CRITIC,
)
"""Agents whose prose the DeepEval metrics grade."""

MAX_CONTEXT_LINES: Final[int] = 80
"""Context lines passed to a judge. DeepEval extracts and checks claims line
by line, so an unbounded context is an unbounded bill; the largest pack (the
examiner's, 67 records plus six routing rules) fits."""

NON_FACT_SECTIONS: Final[frozenset[str]] = frozenset({"identity", "versions"})
"""Pack sections that describe the run, not the case."""


@dataclass(frozen=True)
class JudgeCase:
    """The three texts a judge grades (see the module docstring)."""

    input: str
    actual_output: str
    retrieval_context: tuple[str, ...]


def context_lines(pack: ContextPack) -> tuple[str, ...]:
    """Every fact line of ``pack``, in section order, without the run's own
    identity and versions lines, ``(none)`` placeholders or truncation notes."""
    lines: list[str] = []
    for name, text in pack.sections.items():
        if name in NON_FACT_SECTIONS:
            continue
        lines += [
            line.strip()
            for line in text.split("\n")
            if line.strip() and line.strip() != "(none)" and not line.startswith("[")
        ]
    return tuple(lines[:MAX_CONTEXT_LINES])


def _narrative_output(output: AgentOutput) -> str:
    parts = [f"Recommendation: {n.text}" for n in output.of_type(Narrative)]
    parts += [
        f"Condition {c.condition_id}: {c.text} (owner {c.owner}, due {c.due}, "
        f"source {c.source})"
        for c in output.of_type(Condition)
    ]
    parts += [f"Business summary: {b.text}" for b in output.of_type(BusinessSummary)]
    return "\n".join(parts)


def _examiner_output(output: AgentOutput) -> str:
    return "\n".join(
        sentence.text
        for answer in output.of_type(ExaminerAnswer)
        for sentence in answer.sentences
    )


def _critic_output(output: AgentOutput) -> str:
    return "\n".join(
        f"[{f.finding_type}, {f.severity}] {f.text}"
        for f in output.of_type(CriticFinding)
    )


def _input_for(agent: AgentName, row: EvalRow, pack: ContextPack) -> str:
    if agent is AgentName.EXAMINER:
        return str(row.input.get("question", ""))
    if agent is AgentName.CRITIC:
        wanted = ("proposals", "rules", "evidence_dates")
        return "\n".join(pack.sections[n] for n in wanted if n in pack.sections)
    return "\n".join(
        text for name, text in pack.sections.items() if name not in NON_FACT_SECTIONS
    )


def judge_case(
    agent: AgentName, row: EvalRow, output: AgentOutput, pack: ContextPack
) -> Optional[JudgeCase]:
    """The case for one judged row, or ``None`` when there is nothing to
    judge (the run produced no text: a blocked or failed row)."""
    writers = {
        AgentName.NARRATIVE: _narrative_output,
        AgentName.EXAMINER: _examiner_output,
        AgentName.CRITIC: _critic_output,
    }
    writer = writers.get(agent)
    if writer is None:
        return None
    text = writer(output)
    if not text.strip():
        logger.info("%s row %s has no text to judge", agent.value, row.row_id)
        return None
    return JudgeCase(
        input=_input_for(agent, row, pack),
        actual_output=text,
        retrieval_context=context_lines(pack),
    )
