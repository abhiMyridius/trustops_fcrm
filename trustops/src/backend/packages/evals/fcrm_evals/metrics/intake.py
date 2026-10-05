"""Intake metrics (design B §7.3).

- ``field_extraction_accuracy`` (>= 0.90, intake gate): per expected field,
  the proposed value equals the label after normalisation (Guard 4's
  ``quote_match.normalise``: NFKC, quote/dash folding, whitespace) with
  currency symbols removed; a missing field counts as wrong.
- ``source_span_accuracy`` (>= 0.90): per expected field the agent found,
  same document and page, and the line ranges overlap.
- ``category_f1`` (>= 0.90): micro-F1 over change types, aggregated as
  sum(2TP) / sum(2TP + FP + FN) so the dataset-level value is a true micro-F1.
- ``gap_question_precision`` (>= 0.90): gap questions whose field really is
  absent from the extraction / gap questions asked.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Optional

from fcrm_contracts.proposals.intake import (
    CategoryProposal,
    ExtractedField,
    ExtractionProposal,
    GapQuestion,
)
from fcrm_contracts.proposals.provenance import DocSourceRef
from fcrm_guardrails.quote_match import normalise

from fcrm_evals.context import EvalContext
from fcrm_evals.metrics.base import Observation, ratio
from fcrm_evals.outputs import AgentOutput
from fcrm_evals.rows import EvalRow

_CURRENCY = re.compile(r"[$€£¥]")


def normalise_value(value: str) -> str:
    """The comparison form of a field value."""
    return normalise(_CURRENCY.sub("", value))


def _fields(output: AgentOutput) -> dict[str, ExtractedField]:
    found: dict[str, ExtractedField] = {}
    for proposal in output.of_type(ExtractionProposal):
        for field in proposal.fields:
            found.setdefault(field.field_id, field)
    return found


def field_extraction_accuracy(
    row: EvalRow, output: AgentOutput, context: EvalContext
) -> Optional[Observation]:
    """Correct values / expected fields."""
    del context
    expected: list[Mapping[str, Any]] = row.label("fields", [])
    found = _fields(output)
    flags = []
    correct = 0
    for item in expected:
        got = found.get(item["field_id"])
        if got is not None and normalise_value(got.value) == normalise_value(
            item["value"]
        ):
            correct += 1
        else:
            flags.append(f"{item['field_id']}: {got.value if got else 'missing'!r}")
    return ratio(correct, len(expected), flags)


def _span_ok(ref: Any, item: Mapping[str, Any]) -> bool:
    return (
        isinstance(ref, DocSourceRef)
        and ref.doc_id == item["doc_id"]
        and ref.page == item["page"]
        and ref.line_start <= item["line_end"]
        and item["line_start"] <= ref.line_end
    )


def source_span_accuracy(
    row: EvalRow, output: AgentOutput, context: EvalContext
) -> Optional[Observation]:
    """Fields cited at the right span / expected fields the agent found."""
    del context
    expected: list[Mapping[str, Any]] = row.label("fields", [])
    found = _fields(output)
    present = [item for item in expected if item["field_id"] in found]
    ok = [i for i in present if _span_ok(found[i["field_id"]].source_ref, i)]
    flags = [f"{i['field_id']}: wrong span" for i in present if i not in ok]
    return ratio(len(ok), len(present), flags)


def category_f1(
    row: EvalRow, output: AgentOutput, context: EvalContext
) -> Optional[Observation]:
    """Micro-F1 contribution: ``2TP / (2TP + FP + FN)`` for this row."""
    del context
    expected = set(row.label("categories", []))
    proposed = {p.change_type.value for p in output.of_type(CategoryProposal)}
    tp = len(expected & proposed)
    fp = len(proposed - expected)
    fn = len(expected - proposed)
    flags = [f"fp {sorted(proposed - expected)}"] if fp else []
    flags += [f"fn {sorted(expected - proposed)}"] if fn else []
    return ratio(2 * tp, 2 * tp + fp + fn, flags)


def gap_question_precision(
    row: EvalRow, output: AgentOutput, context: EvalContext
) -> Optional[Observation]:
    """Gap questions about a really-absent field / gap questions."""
    del context
    absent = row.label("absent_fields")
    if absent is None:
        return None
    questions = output.of_type(GapQuestion)
    grounded = [q for q in questions if q.field_id in set(absent)]
    flags = [
        f"{q.question_id}: field {q.field_id} not absent"
        for q in questions
        if q not in grounded
    ]
    return ratio(len(grounded), len(questions), flags)
