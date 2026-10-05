"""H1's cores: the step handlers of each vertical, as modules.

``intake`` (scenes 2-7), the assessment core (scenes 8-16: ``assessment``,
``assessment_review``, ``assessment_pack``) and the decision core (scenes 17-19:
``decision``, ``closing``) are here; the oversight core adds its ``HANDLERS``
table to :func:`default_handlers` with its handlers and its
``audit_map.STEP_FOR`` entries.
"""

from __future__ import annotations

from fcrm_contracts.enums import StepKind

from fcrm_caseflow.cores import (
    assessment,
    assessment_pack,
    assessment_review,
    closing,
    decision,
    intake,
)
from fcrm_caseflow.steps import StepHandler


def default_handlers() -> dict[StepKind, StepHandler]:
    """Every core's handlers merged into one table (a fresh dict each call).

    Raises:
        ValueError: two cores claim the same step kind.
    """
    merged: dict[StepKind, StepHandler] = {}
    for table in (
        intake.HANDLERS,
        assessment.HANDLERS,
        assessment_review.HANDLERS,
        assessment_pack.HANDLERS,
        decision.HANDLERS,
        closing.HANDLERS,
    ):
        clash = merged.keys() & table.keys()
        if clash:
            names = ", ".join(sorted(kind.value for kind in clash))
            raise ValueError(f"step kinds claimed by two cores: {names}")
        merged.update(table)
    return merged
