"""Examiner metrics.

- ``examiner_grounding`` (design B ``sentence_record_grounding``, = 1.00,
  examiner gate): sentences that cite at least one record and whose every
  ``record_refs`` entry resolves in the case's verified audit chain
  (``fcrm_audit.chain`` replay of the seed, ``verify_chain`` first) /
  sentences. The contract has no per-sentence quote, so the design's
  "and a verbatim quote" half is not measurable on this schema.
- ``must_cite_coverage`` (>= 0.90, proposed): record *types* the question
  requires (e.g. ``score.computed``, ``human.override``) that some cited
  record has / required types.
- ``refusal_rate`` (design B, >= 0.95): unanswerable questions answered with
  no sentences at all / unanswerable questions.
"""

from __future__ import annotations

from typing import Optional

from fcrm_contracts.proposals.examiner import ExaminerAnswer

from fcrm_evals.context import EvalContext
from fcrm_evals.metrics.base import Observation, ratio
from fcrm_evals.metrics.critic import refs_resolve
from fcrm_evals.outputs import AgentOutput
from fcrm_evals.rows import EvalRow


def _sentences(output: AgentOutput) -> list[tuple[str, list[int]]]:
    return [
        (s.text, list(s.record_refs))
        for answer in output.of_type(ExaminerAnswer)
        for s in answer.sentences
    ]


def examiner_grounding(
    row: EvalRow, output: AgentOutput, context: EvalContext
) -> Optional[Observation]:
    """Grounded sentences / sentences."""
    seqs = context.record_seqs(row.case_id)
    sentences = _sentences(output)
    bad = [(t, refs) for t, refs in sentences if not refs_resolve(refs, seqs)]
    flags = [f"ungrounded {refs}: {t[:60]}" for t, refs in bad]
    return ratio(len(sentences) - len(bad), len(sentences), flags)


def must_cite_coverage(
    row: EvalRow, output: AgentOutput, context: EvalContext
) -> Optional[Observation]:
    """Required record types cited / required record types."""
    required = list(row.label("must_cite_types", []))
    types = context.record_types(row.case_id)
    cited = {
        types[seq] for _, refs in _sentences(output) for seq in refs if seq in types
    }
    missing = [t for t in required if t not in cited]
    return ratio(
        len(required) - len(missing), len(required), [f"no {m}" for m in missing]
    )


def refusal_rate(
    row: EvalRow, output: AgentOutput, context: EvalContext
) -> Optional[Observation]:
    """1 per unanswerable row answered with no sentences."""
    del context
    if row.label("answerable", True):
        return None
    refused = not _sentences(output)
    return ratio(
        1.0 if refused else 0.0, 1.0, [] if refused else ["answered the unanswerable"]
    )
