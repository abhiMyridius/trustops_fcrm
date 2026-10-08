"""H1's record of a clause the analyst added from a policy-corpus search, or
removed (ADR-74).

* ``clause.record`` (``case.clause_added`` / ``case.clause_removed``): S1 stored
  the clause item (accepted, its text read word for word from S3 at the case's
  pinned corpus version) or its removal, with the analyst's reason, before it
  announced it. All that is left is to put it on the chain: ``clause.added`` or
  ``clause.removed``, the analyst as the actor and the reason in the record.

**No state moves and no score is asked for.** The trigger is step-only (the run
stays in review or recommend), a clause is never a score input, and the item has
no gate, so it neither opens nor closes one.

**Safe to run twice**: the record's ``Idempotency-Key`` is derived from the step
id, so a retried step gets back the record S4 already chained.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Final

from fcrm_contracts.enums import StepKind
from fcrm_contracts.events.payloads import (
    CaseClauseAdded,
    CaseClauseRemoved,
    HarnessStep,
)

from fcrm_caseflow.cores.intake_records import short
from fcrm_caseflow.cores.shared import step_payload
from fcrm_caseflow.steps import StepDeps, StepHandler, StepResult
from fcrm_caseflow.triggers import Trigger

logger = logging.getLogger(__name__)

#: The audit record types this step writes.
CLAUSE_ADDED: Final = "clause.added"
CLAUSE_REMOVED: Final = "clause.removed"


async def clause_record(step: HarnessStep, deps: StepDeps) -> StepResult:
    """Write ``clause.added`` or ``clause.removed`` for the clause S1 stored,
    whichever the trigger says.
    """
    if step.args.get("trigger") == Trigger.CASE_CLAUSE_REMOVED.value:
        removed = step_payload(step, CaseClauseRemoved)
        await deps.append_record(
            step,
            CLAUSE_REMOVED,
            f"Clause {removed.clause_id} removed by the analyst · "
            f"{short(removed.reason)}",
            {
                "item_id": removed.item_id,
                "clause_id": removed.clause_id,
                "reason": removed.reason,
            },
            actor=removed.actor,
        )
        return StepResult()
    added = step_payload(step, CaseClauseAdded)
    title = f" ({short(added.title)})" if added.title else ""
    await deps.append_record(
        step,
        CLAUSE_ADDED,
        f"Clause {added.clause_id}{title} added from the corpus v{added.corpus_v} "
        f"by the analyst · quoted verbatim · not scored · {short(added.reason)}",
        {
            "item_id": added.item_id,
            "clause_id": added.clause_id,
            "corpus_v": added.corpus_v,
            "ref": added.ref,
            "reason": added.reason,
        },
        actor=added.actor,
    )
    logger.info(
        "analyst clause recorded",
        extra={"case_id": step.case_id, "clause_id": added.clause_id},
    )
    return StepResult()


#: This core's handlers by step kind.
HANDLERS: Final[Mapping[StepKind, StepHandler]] = {
    StepKind.CLAUSE_RECORD: clause_record,
}

__all__ = ["CLAUSE_ADDED", "CLAUSE_REMOVED", "HANDLERS", "clause_record"]
