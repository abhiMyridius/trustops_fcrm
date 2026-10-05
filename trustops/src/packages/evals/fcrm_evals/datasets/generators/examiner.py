"""``examiner/questions``: five questions over the demo case's real
67-record chain.

Only the demo case has an audit trail in the seed, so this dataset is
small and says so:

1. The seed's own examiner question and seven-sentence answer
   (``demo.examiner``), each sentence citing real record numbers;
   ``must_cite_types`` is design B §7.2's example row for this question.
2-3. Two templated questions (what was overridden and why; how the
   committee decided) whose answers are **synthesized** from the summaries
   of the records they cite (#49-#50; #60-#64).
4-5. Two unanswerable questions (facts the chain does not hold); the
   correct answer is a refusal: no sentences at all.

Design B §7.2 sizes this at 120 rows (40 cases x 3 templates + 20
unanswerable) once H1 can replay every seed case into a chain.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Final

from fcrm_contracts.enums import AgentName
from fcrm_contracts.proposals.examiner import ExaminerAnswer, ExaminerSentence
from fcrm_seed.loader import demo_case

from fcrm_evals.chains import demo_chain
from fcrm_evals.datasets.generators.common import (
    chain_view,
    dump_sources,
    record_sources,
    ref,
)
from fcrm_evals.rows import EvalRow, Expected, RowMeta, RowSource

REAL_MUST_CITE: Final[tuple[str, ...]] = (
    "score.computed",
    "human.override",
    "score.recomputed",
    "vote.revealed",
)
"""Design B §7.2's ``must_cite_types`` for the routed-to-committee question."""

TEMPLATED: Final[tuple[tuple[str, tuple[tuple[str, tuple[int, ...]], ...]], ...]] = (
    (
        "What was overridden on FCRM-2026-0151, and why?",
        (
            (
                "Analyst Rahul Iyer overrode the vendor due-diligence rating "
                "CTL-T01 from 7.6 to 4.5 and recorded a reason.",
                (49,),
            ),
            ("The engine then recomputed the composite to 10.4, Band 3.", (50,)),
        ),
    ),
    (
        "How did the committee reach its outcome on FCRM-2026-0151?",
        (
            ("Three votes were cast and kept hidden until all were in.", (60, 61, 62)),
            (
                "The tally by code was approve with conditions 2, defer 1.",
                (63,),
            ),
            ("The case was approved with three conditions activated.", (64,)),
        ),
    ),
)

UNANSWERABLE: Final[tuple[str, ...]] = (
    "What did the board of Remesas del Norte decide about its own AML budget?",
    "Which analyst reviewed case FCRM-2026-0199?",
)


def _row(
    row_id: str,
    question: str,
    sentences: Sequence[tuple[str, Sequence[int]]],
    *,
    must_cite: Sequence[str],
    source: RowSource,
    notes: str,
    view: list[dict[str, Any]],
    sources: list[dict[str, Any]],
) -> EvalRow:
    demo_id = demo_case()["id"]
    answer = ExaminerAnswer(
        question=question,
        sentences=[
            ExaminerSentence(text=text, record_refs=list(refs))
            for text, refs in sentences
        ],
    )
    return EvalRow(
        row_id=row_id,
        case_id=demo_id,
        agent=AgentName.EXAMINER,
        input={"question": question, "chain": view, "sources": sources},
        expected=Expected(
            labels={"answerable": bool(sentences), "must_cite_types": list(must_cite)},
            reference_outputs=[ref(answer)],
        ),
        rubric_notes=notes,
        metadata=RowMeta(
            source=source, tags=["answerable" if sentences else "unanswerable"]
        ),
    )


def build() -> list[EvalRow]:
    """The seed question, two templated questions, two refusals."""
    demo = demo_case()
    chain = demo_chain()
    types = {r.seq: r.type for r in chain}
    view = chain_view(chain)
    sources = dump_sources(record_sources(chain))
    seed_answer = demo["examiner"]
    rows = [
        _row(
            f"{demo['id']}~q1",
            seed_answer["question"],
            [(s["t"], s["refs"]) for s in seed_answer["sentences"]],
            must_cite=REAL_MUST_CITE,
            source=RowSource.DEMO,
            notes="Seed-real: demo.examiner question and answer with refs.",
            view=view,
            sources=sources,
        )
    ]
    for n, (question, sentences) in enumerate(TEMPLATED, start=2):
        cited = sorted({types[seq] for _, refs in sentences for seq in refs})
        rows.append(
            _row(
                f"{demo['id']}~q{n}",
                question,
                sentences,
                must_cite=cited,
                source=RowSource.SYNTHETIC,
                notes=(
                    "Synthesized answer over the real demo chain; sentences "
                    "restate the summaries of the records they cite."
                ),
                view=view,
                sources=sources,
            )
        )
    for n, question in enumerate(UNANSWERABLE, start=len(rows) + 1):
        rows.append(
            _row(
                f"{demo['id']}~q{n}",
                question,
                [],
                must_cite=[],
                source=RowSource.SYNTHETIC,
                notes="Unanswerable from the chain: the correct answer is a refusal.",
                view=view,
                sources=sources,
            )
        )
    return rows
