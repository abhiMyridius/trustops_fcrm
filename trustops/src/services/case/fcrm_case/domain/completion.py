"""The one canonical completion calculation.

Design A §2.1 and the engineering-judgement register are explicit that
this is deterministic set arithmetic and must exist exactly once. Every
caller — the submit-checks evaluator below, the analyst workbench's
completion ring, the PO dashboard — reads this function's result; none of
them recomputes a percentage from raw field lists themselves.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from fcrm_case.domain.fieldset import FieldDef


class CompletionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    required_count: int
    filled_count: int
    pct: float  # 0.0-1.0; round only for display, never before comparing to a threshold
    missing_always_required: list[str]  # field ids
    all_always_required_filled: bool


def compute_completion(
    required_fields: list[FieldDef], filled_field_ids: set[str]
) -> CompletionResult:
    """``filled_field_ids`` is whatever the case currently has a non-empty
    value for, from any source (extracted, reused, answered, or
    owner-corrected) — this function does not care how a value arrived,
    only whether one exists.
    """
    required_count = len(required_fields)
    filled_count = sum(1 for f in required_fields if f.field_id in filled_field_ids)
    pct = (filled_count / required_count) if required_count else 1.0

    missing_always_required = [
        f.field_id
        for f in required_fields
        if f.always_required and f.field_id not in filled_field_ids
    ]

    return CompletionResult(
        required_count=required_count,
        filled_count=filled_count,
        pct=pct,
        missing_always_required=missing_always_required,
        all_always_required_filled=not missing_always_required,
    )
