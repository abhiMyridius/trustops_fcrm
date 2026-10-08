"""How a stored case reads to its product owner: the PO's six stages, the
five-step lifecycle strip, the sentence that says what the owner does next and
the Mongo filter behind each stage. Pure, no I/O.

The PO stage is derived from the case's state (H1 decides it, S1 stores it) and
from whether it was ever submitted; S1 never stores a PO stage of its own.
:func:`po_stage` is the one place that mapping lives, and :func:`stage_filter`
spells the same mapping as a Mongo query so a list can filter by stage in the
database instead of in memory. ``tests/unit/services/case/test_po_stage.py``
runs every state through both and fails if they disagree.

Nothing here knows a score, band or route.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final, get_args

from fcrm_contracts.api.case_po import POStage, StageStep
from fcrm_contracts.enums import CaseState

from fcrm_case.domain.record import CaseRecord

PO_STAGES: Final[tuple[str, ...]] = get_args(POStage)

STAGE_LABELS: Final[dict[str, str]] = {
    "draft": "Draft",
    "submitted": "Submitted",
    "under_assessment": "Under assessment",
    "awaiting_your_answer": "Awaiting your answer",
    "decision": "Decision pending",
    "closed": "Closed",
}

#: The lifecycle strip: ``awaiting_your_answer`` is a sub-state of assessment.
LIFECYCLE: Final[tuple[tuple[str, str], ...]] = (
    ("draft", "Draft"),
    ("submitted", "Submitted"),
    ("under_assessment", "Under assessment"),
    ("decision", "Decision"),
    ("closed", "Closed"),
)
_STEP_OF: Final[dict[str, int]] = {
    "draft": 0,
    "submitted": 1,
    "under_assessment": 2,
    "awaiting_your_answer": 2,
    "decision": 3,
    "closed": 4,
}

#: States in which a case that was never submitted is still a draft.
_DRAFT_STATES: Final[frozenset[CaseState]] = frozenset(
    {CaseState.INTAKE, CaseState.REVIEW_AND_SUBMIT}
)
#: Triage is where a submitted case waits to be picked up (R5); a case still
#: in a draft state is "submitted" the moment ``submitted_at`` is stamped, before
#: H1 has moved it on.
_TRIAGE_STATE: Final = CaseState.TRIAGE
#: Everything between triage and the committee that is not a PO question.
_ASSESSMENT_STATES: Final[frozenset[CaseState]] = frozenset(
    {
        CaseState.SCREENING,
        CaseState.ASSESS,
        CaseState.CRITIC,
        CaseState.COMPLETENESS,
        CaseState.REVIEW,
        CaseState.RECOMMEND,
        CaseState.HANDOFF,
    }
)

#: A condition in one of these statuses is still open for its owner.
OPEN_CONDITION_STATUSES: Final[tuple[str, ...]] = (
    "open",
    "scheduled",
    "evidence_submitted",
    "overdue",
)

#: Stages in which the product owner has something to do.
NEEDS_RESPONSE_STAGES: Final[tuple[str, ...]] = ("draft", "awaiting_your_answer")


def po_stage(record: CaseRecord) -> POStage:
    """The PO stage of ``record``."""
    state = record.state
    submitted = record.submitted_at is not None
    stage: POStage
    if state == CaseState.CLOSED:
        stage = "closed"
    elif state == CaseState.DECIDE:
        stage = "decision"
    elif state == CaseState.CLARIFICATION:
        stage = "awaiting_your_answer"
    elif state in _DRAFT_STATES:
        stage = "submitted" if submitted else "draft"
    elif state == _TRIAGE_STATE:
        stage = "submitted"
    else:
        stage = "under_assessment"
    return stage


def _states(*states: CaseState) -> dict[str, Any]:
    """A ``state`` filter naming ``states`` (sorted, so the query is stable)."""
    return {"$in": sorted(state.value for state in states)}


def stage_filter(stage: str) -> dict[str, Any]:
    """The Mongo filter fragment that selects exactly the cases :func:`po_stage`
    calls ``stage``.

    Raises:
        ValueError: ``stage`` is not one of the six PO stages.
    """
    if stage == "draft":
        return {"state": _states(*_DRAFT_STATES), "submitted_at": None}
    if stage == "submitted":
        return {
            "$or": [
                {"state": _TRIAGE_STATE.value},
                {"state": _states(*_DRAFT_STATES), "submitted_at": {"$ne": None}},
            ]
        }
    if stage == "under_assessment":
        return {"state": _states(*_ASSESSMENT_STATES)}
    if stage == "awaiting_your_answer":
        return {"state": CaseState.CLARIFICATION.value}
    if stage == "decision":
        return {"state": CaseState.DECIDE.value}
    if stage == "closed":
        return {"state": CaseState.CLOSED.value}
    raise ValueError(f"not a PO stage: {stage!r}")


def needs_response_filter() -> dict[str, Any]:
    """The filter for ``needs_response=true``: a draft or a question to answer."""
    return {
        "$or": [stage_filter(stage) for stage in NEEDS_RESPONSE_STAGES],
    }


def lifecycle_steps(stage: str) -> list[StageStep]:
    """The five-step strip with everything before ``stage`` done."""
    current = _STEP_OF[stage]
    return [
        StageStep(
            key=key,
            label=label,
            state="done" if i < current else "current" if i == current else "upcoming",
        )
        for i, (key, label) in enumerate(LIFECYCLE)
    ]


@dataclass(frozen=True)
class CaseSummary:
    """What a list or a header needs to know about a case beyond its document:
    gathered for a whole page of cases in a handful of bulk queries (never one
    per case), then read by :func:`next_action` and the completion figures.
    """

    open_conditions: int = 0
    open_threads: int = 0
    #: Required fields that hold a non-blank value.
    filled_field_ids: frozenset[str] = field(default_factory=frozenset)
    documents: int = 0
    documents_unread: int = 0
    documents_unreadable: int = 0
    intake_ready: bool = False
    #: Change types the intake agent proposed that the owner has not decided.
    pending_types: tuple[str, ...] = ()
    #: Blocking questions the owner has not answered.
    blocking_gaps_open: int = 0

    @property
    def has_values(self) -> bool:
        """Whether any field value is stored for the case."""
        return bool(self.filled_field_ids)


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _draft_action(summary: CaseSummary, *, manual: bool) -> str:
    """What the owner does next on a draft, in the order the screens go. A
    manual case (ADR-72) needs no document: its owner fills in the details.
    """
    if summary.documents == 0:
        if manual:
            return "Fill in the details, then review & submit"
        return "Upload what you already have"
    if not summary.intake_ready:
        if summary.documents_unread == 0 and summary.documents_unreadable > 0:
            return "Upload another version of the file that could not be read"
        return "The assistant is reading your documents"
    if summary.pending_types:
        return "Confirm the proposed change types"
    if summary.blocking_gaps_open:
        return f"Answer {_plural(summary.blocking_gaps_open, 'blocking question')}"
    return "Review & submit"


def next_action(stage: str, summary: CaseSummary, *, manual: bool = False) -> str:
    """The one sentence that says what the product owner does next.
    ``manual`` is a case its owner started on the manual path (ADR-72).
    """
    if stage == "draft":
        return _draft_action(summary, manual=manual)
    if stage == "awaiting_your_answer":
        if summary.open_threads > 1:
            return f"Answer {summary.open_threads} questions from FCRM"
        return "Answer a question from FCRM"
    if stage == "closed":
        if summary.open_conditions:
            return _plural(summary.open_conditions, "open condition")
        return "Closed"
    if stage == "decision":
        return "Waiting for the decision"
    return "Nothing needed from you"


def stage_label(stage: str) -> str:
    """The PO-facing name of ``stage``."""
    return STAGE_LABELS[stage]


def needs_response(stage: str) -> bool:
    """Whether the owner has something to do at ``stage``."""
    return stage in NEEDS_RESPONSE_STAGES


def has_due_date(stage: str) -> bool:
    """Whether a target date applies at ``stage``: a draft has none yet and a
    closed case no longer has one.
    """
    return stage not in ("draft", "closed")
