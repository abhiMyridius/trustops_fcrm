"""The audit records the intake core writes (scenes 3-7), one function each.

Every record carries ids, counts and source positions: never a score, never a
document's text beyond what an agent quoted. Each goes through
:meth:`fcrm_caseflow.steps.StepDeps.append_record`, whose ``Idempotency-Key``
is derived from the step id, the record type and an ordinal, so a step that
runs twice chains nothing twice. The summaries are the walkthrough's wording
(``demo.audit[].what``), with the figures taken from what actually happened.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, Final, Optional

from fcrm_contracts.api.case_internal import PrecedentMatch
from fcrm_contracts.events.payloads import GateDecided, HarnessStep

from fcrm_caseflow.cores.intake_merge import CHANGE_TYPE_LABELS, MergedIntake
from fcrm_caseflow.steps import StepDeps

logger = logging.getLogger(__name__)

_MAX_TEXT: Final = 200

#: Decision strings that mean the PO changed an extracted value.
_CORRECTING_DECISIONS: Final = frozenset(
    {"correct", "corrected", "edit", "edited", "override", "overridden"}
)
#: Decision strings that mean the PO accepted the values offered from a precedent.
_REUSING_DECISIONS: Final = frozenset(
    {"reuse", "reused", "accept", "accepted", "confirm", "confirmed"}
)
_DECISION_VERBS: Final[Mapping[str, str]] = {
    "confirm": "confirmed",
    "remove": "removed",
}


def short(value: Any) -> str:
    """``value`` as one short line for a summary."""
    text = " ".join(str(value).split())
    return text if len(text) <= _MAX_TEXT else text[: _MAX_TEXT - 1] + "…"


def plural(count: int, noun: str) -> str:
    """``"1 page"``, ``"3 pages"``."""
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def position(source: Any) -> dict[str, Any]:
    """A document span as an audit record shows it: ids and numbers only."""
    return {
        "doc_id": source.doc_id,
        "page": source.page,
        "line_start": source.line_start,
        "line_end": source.line_end,
    }


# --- what the intake agents proposed -------------------------------------------


async def _extraction(step: HarnessStep, deps: StepDeps, fresh: MergedIntake) -> None:
    await deps.append_record(
        step,
        "proposal.extraction",
        f"Intake agent · {plural(fresh.candidates_found, 'candidate value')} · "
        f"{plural(len(fresh.values), 'field')} · every value sourced",
        {
            "candidates_found": fresh.candidates_found,
            "dropped_non_document_sources": fresh.dropped_non_document_sources,
            "values": [
                {
                    "field_id": v.field_id,
                    "confidence": v.confidence,
                    "candidates": v.candidates,
                    "source": position(v.source),
                }
                for v in fresh.values
            ],
        },
        actor="ai",
    )


async def _categories(step: HarnessStep, deps: StepDeps, fresh: MergedIntake) -> None:
    await deps.append_record(
        step,
        "proposal.category",
        f"Intake agent · {plural(len(fresh.categories), 'change type')} proposed",
        {
            "change_types": [
                {
                    "change_type": c.change_type.value,
                    "confidence": c.confidence,
                    "source": position(c.source),
                }
                for c in fresh.categories
            ]
        },
        actor="ai",
    )


async def _gaps(step: HarnessStep, deps: StepDeps, fresh: MergedIntake) -> None:
    blocking = sum(1 for g in fresh.gaps if g.blocking)
    await deps.append_record(
        step,
        "proposal.gaps",
        f"Intake agent · {plural(len(fresh.gaps), 'question')} · {blocking} blocking",
        {
            "questions": [
                {
                    "question_id": g.question_id,
                    "field_id": g.field_id,
                    "blocking": g.blocking,
                }
                for g in fresh.gaps
            ]
        },
        actor="ai",
    )


async def _precedent(
    step: HarnessStep, deps: StepDeps, precedent: PrecedentMatch
) -> None:
    await deps.append_record(
        step,
        "precedent.matched",
        f"precedent_search · {precedent.case_id} similarity {precedent.similarity:.2f}",
        {
            "precedent_case_id": precedent.case_id,
            "similarity": precedent.similarity,
            "reusable_field_ids": [f.field_id for f in precedent.fields if f.reusable],
        },
        actor="system",
    )


async def _flags(step: HarnessStep, deps: StepDeps, fresh: MergedIntake) -> None:
    for index, flag in enumerate(fresh.flags):
        await deps.append_record(
            step,
            "fact.flagged",
            f"New fact at intake: {short(flag.text)} · flagged, not blocked",
            {"field_id": flag.field_id, "kind": flag.kind},
            actor="system",
            ordinal=index,
        )


async def record_proposals(
    step: HarnessStep,
    deps: StepDeps,
    fresh: MergedIntake,
    *,
    first_publish: bool,
    precedent: Optional[PrecedentMatch],
) -> None:
    """The records of what ``fresh`` (the agent jobs this publish has not
    recorded before) proposed. The first publish always records the extraction
    and the change types, as the walkthrough shows them, and the precedent if
    one was found; a later one (the questions-only round) records only what it
    found.
    """
    if first_publish or fresh.values:
        await _extraction(step, deps, fresh)
    if first_publish or fresh.categories:
        await _categories(step, deps, fresh)
    if fresh.gaps:
        await _gaps(step, deps, fresh)
    if first_publish and precedent is not None:
        await _precedent(step, deps, precedent)
    await _flags(step, deps, fresh)


# --- what the PO decided -------------------------------------------------------


def change_type_label(subject_id: Optional[str]) -> str:
    """The PO-facing label of a change type id, lower-cased for a sentence."""
    for change_type, label in CHANGE_TYPE_LABELS.items():
        if change_type.value == subject_id:
            return label.lower()
    return subject_id or "change type"


async def record_category_decision(
    step: HarnessStep, deps: StepDeps, decided: GateDecided
) -> None:
    """``gate.decided`` for one change type: who confirmed or removed it, and
    the reason when it was removed.
    """
    verb = _DECISION_VERBS.get(decided.decision, decided.decision)
    label = change_type_label(decided.subject.get("id"))
    reason = f" · {short(decided.reason)}" if decided.reason else ""
    await deps.append_record(
        step,
        "gate.decided",
        f"PO {verb} change type: {label}{reason}",
        {
            "gate_id": decided.gate_id,
            "gate_type": decided.gate_type.value,
            "subject": decided.subject,
            "decision": decided.decision,
            "reason": decided.reason,
        },
        actor=decided.decided_by,
    )


def _before_after(decided: GateDecided) -> tuple[Any, Any]:
    """The value before and after a correction, from ``before``/``after``
    (``{"value": ...}``), falling back to the decision's ``value``.
    """
    before = (decided.before or {}).get("value")
    after = (decided.after or {}).get("value", decided.value)
    return before, after


async def record_value_decision(
    step: HarnessStep, deps: StepDeps, decided: GateDecided
) -> None:
    """``owner.corrected`` when the PO changed an extracted value (the original
    stays on the field); a plain confirmation records nothing.
    """
    before, after = _before_after(decided)
    corrected = decided.decision in _CORRECTING_DECISIONS or (
        before is not None and after is not None and before != after
    )
    if not corrected:
        logger.info("a value was confirmed unchanged; nothing to record")
        return
    field_id = decided.subject.get("id", "")
    await deps.append_record(
        step,
        "owner.corrected",
        f"PO corrected {field_id}: {short(before)} → {short(after)} · "
        "source = owner declared · original kept",
        {
            "field_id": field_id,
            "before": before,
            "after": after,
            "reason": decided.reason,
        },
        actor=decided.decided_by,
    )


async def record_reuse_decision(
    step: HarnessStep, deps: StepDeps, decided: GateDecided
) -> None:
    """``values.reused`` when the PO accepted values from the precedent."""
    if decided.decision not in _REUSING_DECISIONS:
        logger.info("reuse was declined; nothing to record")
        return
    precedent_id = decided.subject.get("id", "")
    reused = decided.after if decided.after is not None else decided.value
    await deps.append_record(
        step,
        "values.reused",
        f"Values reused with provenance = {precedent_id}",
        {"precedent_case_id": precedent_id, "reused": reused},
        actor=decided.decided_by,
    )


async def record_answer(
    step: HarnessStep, deps: StepDeps, decided: GateDecided
) -> None:
    """``owner.answered``: the PO answered one gap question."""
    question_id = decided.subject.get("id", "")
    await deps.append_record(
        step,
        "owner.answered",
        f"PO answered question {question_id}",
        {"question_id": question_id, "decision": decided.decision},
        actor=decided.decided_by,
    )
