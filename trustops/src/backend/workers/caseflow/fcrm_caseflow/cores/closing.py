"""H1's closing step: scene 19, after an outcome is recorded and S1 has closed
the case (``closed.finalize``; ADR-28).

It runs when the run is CLOSED and no other step is pending, so the decision
steps' results (``outcome``, ``outcome_mode``, ``conditions_activated``, left in
the run's refs) are in. It writes ``outcome.delivered`` and ``case.closed``, asks
S4 to recompute the whole chain and **requires it to verify**: a chain that does
not verify fails the step (``ChainBrokenError``), which the broker retries and
then dead-letters, so a tampered or broken trail is never reported as sound. Only
a verified chain gets its ``chain.verified`` record.

The PO is told, once, in plain words: ``outcome.delivered`` is announced as a
catalogued event in the same transaction that completes the step
(:class:`~fcrm_caseflow.steps.UiEvent`), so it goes out exactly once, and S1's
event consumer shows it to the PO. It carries the outcome's label and how many
conditions apply: no score, no band, no vote.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from fcrm_common.errors import ChainBrokenError, ConflictError
from fcrm_contracts.api.case_internal_outcome import OutcomeDelivered, outcome_label
from fcrm_contracts.enums import Outcome, StepKind
from fcrm_contracts.events.payloads import HarnessStep

from fcrm_caseflow.steps import StepDeps, StepHandler, StepResult, UiEvent

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ClosedCase:
    """What the closing step knows about the decision it is finalising.

    Attributes:
        case_id: The closed case.
        outcome: What was decided.
        mode: ``committee`` or ``delegated``.
        conditions_activated: How many conditions S1 activated.
    """

    case_id: str
    outcome: Outcome
    mode: str
    conditions_activated: int


async def record_precedent(
    step: HarnessStep, deps: StepDeps, closed: ClosedCase
) -> bool:
    """Add the closed case to S3's precedent index so a later case can be
    matched to it (scene 5's reuse).

    **A seam, not yet wired:** S3's ``POST /internal/precedents`` route does not
    exist in this tree (the policy lane adds it). The call that belongs here is
    ``PolicyClient.add_precedent(closed)``; until then this reports ``False`` and
    the case is not added, so nothing pretends the index grew.

    Returns:
        Whether the case was added to the index.
    """
    logger.info(
        "precedent index route not available; the closed case is not added",
        extra={"case_id": closed.case_id, "step_id": step.step_id},
    )
    return False


async def _decided(step: HarnessStep, deps: StepDeps) -> ClosedCase:
    """The decision this step finalises: from the run's refs, which the decision
    step left, else from S5's own record of it.

    Raises:
        fcrm_common.errors.ConflictError: The case closed with no recorded
            outcome anywhere, which nothing here may paper over.
    """
    refs: Mapping[str, object] = deps.run.refs
    raw = refs.get("outcome")
    if isinstance(raw, str):
        return ClosedCase(
            case_id=step.case_id,
            outcome=Outcome(raw),
            mode=str(refs.get("outcome_mode") or "committee"),
            conditions_activated=_count(refs.get("conditions_activated")),
        )
    recorded = await deps.require_decision_votes().case_outcome(step.case_id)
    if recorded is None:
        raise ConflictError(
            "the case closed with no recorded outcome to finalise",
            case_id=step.case_id,
        )
    return ClosedCase(
        case_id=step.case_id,
        outcome=recorded.outcome,
        mode=recorded.mode,
        conditions_activated=len(recorded.conditions),
    )


def _count(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


async def closed_finalize(step: HarnessStep, deps: StepDeps) -> StepResult:
    """``internal.closed_dispatch``: write ``outcome.delivered`` and
    ``case.closed``, verify the audit chain (failing the step if it does not
    verify), write ``chain.verified``, and announce the outcome to the PO.

    Raises:
        fcrm_common.errors.ChainBrokenError: S4's recomputation found a break.
        fcrm_common.errors.ConflictError: No recorded outcome to finalise.
    """
    closed = await _decided(step, deps)
    label = outcome_label(closed.outcome)
    await deps.append_record(
        step,
        "outcome.delivered",
        f"Outcome delivered to the product owner · {label}",
        {
            "outcome": closed.outcome.value,
            "mode": closed.mode,
            "conditions_activated": closed.conditions_activated,
        },
        actor="system",
    )
    await deps.append_record(
        step,
        "case.closed",
        f"Case closed · {label}",
        {"outcome": closed.outcome.value, "mode": closed.mode},
        actor="system",
    )
    verified = await deps.audit.verify(step.case_id)
    if not verified.ok:
        logger.error(
            "the audit chain does not verify",
            extra={
                "case_id": step.case_id,
                "step_id": step.step_id,
                "first_break_seq": verified.first_break_seq,
            },
        )
        raise ChainBrokenError(
            f"the audit chain of {step.case_id} does not verify",
            case_id=step.case_id,
            first_broken_seq=verified.first_break_seq,
        )
    await deps.append_record(
        step,
        "chain.verified",
        f"Audit chain verified · {verified.record_count} records, every hash intact",
        {"record_count": verified.record_count, "ok": True},
        actor="system",
    )
    in_index = await record_precedent(step, deps, closed)
    return StepResult(
        ui_events=(
            UiEvent(
                "outcome.delivered",
                OutcomeDelivered(
                    outcome=closed.outcome,
                    outcome_label=label,
                    conditions=closed.conditions_activated,
                ).model_dump(mode="json"),
            ),
        ),
        refs_patch={
            "chain_verified": True,
            "chain_records": verified.record_count,
            "precedent_indexed": in_index,
        },
    )


#: The closing step's handler by step kind (``fcrm_caseflow.cores.default_handlers``).
HANDLERS: Final[Mapping[StepKind, StepHandler]] = {
    StepKind.CLOSED_FINALIZE: closed_finalize
}

__all__ = ["HANDLERS", "ClosedCase", "closed_finalize", "record_precedent"]
