"""S1 case-service: the product owner's shapes, an exact mirror of the web's PO
types (``src/frontend/src/api/types/po.ts`` and the shared ``common.ts`` types
it imports; ADR-30: the web's route and type names are the contract).

This is the enforcement point for "the PO never sees a score, band, rating or
harness data" (rule 3, D-62, BB-02): no model here has a field that could carry
one, rather than a view that strips them. Three fences hold it
(docs/governance/visibility-matrix.md):

1. ``tests/unit/packages/contracts/test_po_models_forbidden_fields.py`` fails
   when any field of any model below is on the D-62 list;
2. ``tests/unit/services/case/test_po_router_imports.py`` fails when the PO
   router imports anything that holds analyst, score or harness data;
3. ``fcrm_case.po_guard`` re-scans every PO response with
   ``fcrm_guardrails.rules.r3_po_no_restricted`` and answers 500
   ``rule_violation:R3`` on a hit.

``tests/unit/packages/contracts/test_po_contract_matches_web_types.py`` parses
``po.ts`` and fails when a field name, its optionality or a literal union here
drifts from it. A field the TypeScript marks optional (``name?:``) is optional
here and left out of the JSON when unset; every other field is always sent,
``null`` when it has no value.

The request bodies at the end are what the web's mutations post
(``src/frontend/src/api/queries/caseQueries.ts``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from fcrm_contracts.enums import ChangeType

POStage = Literal[
    "draft",
    "submitted",
    "under_assessment",
    "awaiting_your_answer",
    "decision",
    "closed",
]

#: How a value got into the case (``ProvenanceTag`` in ``common.ts``).
Origin = Literal[
    "extracted",
    "reused",
    "answered",
    "owner_declared",
    "analyst_override",
    "jurisdiction_list",
    "open",
]

DocumentStatus = Literal["uploading", "stored", "scanned", "read"]

#: How the owner started the case (ADR-72): ``ai_assisted`` (upload documents,
#: the assistant proposes the change types) or ``manual`` (the owner chose the
#: change types and fills in the details by hand).
IntakeMode = Literal["ai_assisted", "manual"]

#: Who put a change type on the case: the assistant proposed it, or the owner
#: added it herself (ADR-72).
TypeOrigin = Literal["assistant", "owner"]


def _is_none(value: Any) -> bool:
    """``exclude_if`` for a field the web types as ``name?:`` (absent, not null)."""
    return value is None


class _Dto(BaseModel):
    """Every PO DTO forbids unknown fields: a typo in a builder is an error,
    not a silently dropped key.
    """

    model_config = ConfigDict(extra="forbid")


# --- shared shapes (common.ts) -------------------------------------------------


class SourceRef(_Dto):
    """A page:line pointer into a stored document; ``label`` is the display
    form, e.g. ``business_case.pdf p.4 l.12``.
    """

    label: str
    document: Optional[str] = Field(default=None, exclude_if=_is_none)
    page: Optional[int] = Field(default=None, exclude_if=_is_none)
    line: Optional[int] = Field(default=None, exclude_if=_is_none)


class StageStep(_Dto):
    key: str
    label: str
    state: Literal["done", "current", "upcoming"]


class Completion(_Dto):
    """Filled over required fields, rounded half up, as the server computes it
    (the client never does arithmetic on these).
    """

    filled: int
    required: int
    pct: int
    threshold_pct: int
    always_required_filled: int
    always_required_total: int


# --- cases, dashboard ------------------------------------------------------------


class CasePOListItem(_Dto):
    id: str
    title: str
    change_types: list[ChangeType]
    stage: POStage
    stage_label: str
    next_action: str
    analyst_name: Optional[str]
    submitted: Optional[str]
    completion_pct: Optional[int]
    due: Optional[str]
    needs_response: bool


class DashboardPO(_Dto):
    active: int
    awaiting_my_answer: int
    avg_days_intake_to_decision: float
    open_conditions: int
    my_cases: list[CasePOListItem]


class CasePO(_Dto):
    id: str
    title: str
    unit: str
    owner_name: str
    change_types: list[ChangeType]
    stage: POStage
    stage_label: str
    lifecycle: list[StageStep]
    created: str
    submitted: Optional[str]
    analyst_name: Optional[str]
    next_action: str
    completion: Completion
    closed: bool
    inbox_open: int
    intake_mode: IntakeMode


# --- documents -------------------------------------------------------------------


class DocumentOut(_Dto):
    id: str
    name: str
    pages: int
    size_mb: float
    status: DocumentStatus
    guard_line: Optional[str]
    added_in: Optional[str]
    uploaded_at: datetime


class AssistantStatus(_Dto):
    state: Literal["waiting", "reading", "done"]
    line: str


class DocumentsViewPO(_Dto):
    documents: list[DocumentOut]
    assistant: AssistantStatus
    sharepoint_connected: bool
    completion: Completion


# --- intake ----------------------------------------------------------------------


class CategoryProposalPO(_Dto):
    type: ChangeType
    label: str
    status: Literal["proposed", "confirmed", "removed"]
    source: Optional[SourceRef]
    reason: Optional[str]
    origin: TypeOrigin


class ValuePO(_Dto):
    field_id: str
    label: str
    value: Optional[str]
    origin: Origin
    source: Optional[SourceRef]
    candidates: Optional[int]
    always_required: bool


class ReuseFieldPO(_Dto):
    field_id: str
    label: str
    value: str
    reusable: bool
    reason: Optional[str]


class PrecedentPO(_Dto):
    """The closest closed case: its title, unit and closed month, and which of
    its values may be reused. Never its outcome, rating or similarity.
    """

    case_id: str
    title: str
    unit: str
    closed_month: str
    fields: list[ReuseFieldPO]
    reused: bool


class GapQuestionPO(_Dto):
    id: str
    field_id: str
    label: str
    question: str
    blocking: bool
    answered: bool
    value: Optional[str]


class IntakeViewPO(_Dto):
    ready: bool
    change_types: list[CategoryProposalPO]
    values: list[ValuePO]
    precedent: Optional[PrecedentPO]
    gaps: list[GapQuestionPO]
    completion: Completion
    required_fields: Optional[int]
    field_set_version: str
    candidates_found: int


# --- review and submit -----------------------------------------------------------


class OriginalValue(_Dto):
    """What a value held before the owner corrected it (the original is kept)."""

    value: str
    source: Optional[SourceRef]


class ReviewRowPO(_Dto):
    field_id: str
    label: str
    value: Optional[str]
    origin: Origin
    source: Optional[SourceRef]
    original: Optional[OriginalValue]
    always_required: bool


class SubmitCheck(_Dto):
    id: Literal["completion", "always_required", "types_confirmed", "no_conflicts"]
    label: str
    ok: bool
    blocking: bool
    detail: str


class RequiredCard(_Dto):
    field_set_version: str
    required: int
    always_required: int
    threshold_pct: int
    labels: list[str]


class ReviewViewPO(_Dto):
    rows: list[ReviewRowPO]
    required_card: RequiredCard
    completion: Completion
    submitted: bool
    submit_blocked_reason: Optional[str]


class SubmitResult(_Dto):
    ok: bool
    checks: list[SubmitCheck]
    submission_version: Optional[int]
    message: Optional[str]


# --- clarification inbox, outcome, activity (served by later lanes) --------------


class ThreadAnswer(_Dto):
    text: str
    documents: list[str]
    at: str


class ThreadPO(_Dto):
    id: str
    # ``round``: a question of FCRM's one clarification round; ``adhoc``: a
    # question the analyst asked at another time (ADR-73; its ``round`` is 0).
    kind: Literal["round", "adhoc"]
    round: int
    # The field the question is about, so the owner can set its new value with
    # the answer; ``None`` when it is about nothing the owner can name.
    field_id: Optional[str]
    field_label: str
    question: str
    status: Literal["open", "answered"]
    answer: Optional[ThreadAnswer]


class InboxRound(_Dto):
    """One round of questions. ``from`` is a Python keyword, so the attribute
    is ``from_`` and the wire name stays ``from``.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    round: int
    from_: str = Field(alias="from")
    sent_at: str
    threads: list[ThreadPO]


class InboxPO(_Dto):
    rounds: list[InboxRound]
    # The analyst's ad-hoc questions (ADR-73), oldest first.
    questions: list[ThreadPO]
    banner: Optional[str]


class ConditionPO(_Dto):
    id: str
    text: str
    owner: str
    due: str
    status: Literal["open", "scheduled", "evidence_submitted", "closed"]
    mine: bool


class OutcomePO(_Dto):
    decided: bool
    outcome_label: Optional[str]
    plain_summary: Optional[str]
    decided_at: Optional[str]
    conditions: list[ConditionPO]
    reminder_days: int


class ActivityEventPO(_Dto):
    id: str
    at: datetime
    actor: Literal["You", "FCRM", "System", "AI"]
    text: str


class ChainStatusPO(_Dto):
    """What the PO learns about the audit chain: that it verifies, and how long
    it is (design A §2.4).
    """

    ok: bool
    count: int
    verified_at: str


class Integration(_Dto):
    id: str
    name: str
    kind: Literal["document_source", "notification_channel", "data_catalogue"]
    status: Literal["connected", "connected_demo", "not_connected"]
    description: str
    scope: Optional[str]
    disconnect_consequence: str


# --- request bodies (what caseQueries.ts posts) ----------------------------------


class CreateCaseBody(_Dto):
    """``POST /cases``: every field is optional, so ``{}`` still raises an
    AI-assisted draft (ADR-72 keeps that). The web's New case form posts the
    title, the unit and the path the owner chose; the manual path names the
    change types she chose (at least one), the AI-assisted path names none.
    """

    title: Optional[str] = Field(default=None, max_length=200)
    unit: Optional[str] = Field(default=None, max_length=80)
    mode: IntakeMode = "ai_assisted"
    change_types: list[ChangeType] = Field(default_factory=list, max_length=6)


class DemoFile(_Dto):
    """A demo fixture named by file name; ``size`` is the client's guess and is
    ignored (the stored bytes decide).
    """

    name: str = Field(min_length=1, max_length=200)
    size: Optional[int] = Field(default=None, ge=0)


class DemoFilesBody(_Dto):
    """``POST /cases/{id}/documents`` with JSON: the committed demo documents to
    use. An empty list means the demo case's two intake documents.
    """

    demo_files: list[DemoFile] = Field(default_factory=list, max_length=10)


class CategoryDecisionBody(_Dto):
    """``confirm`` or ``remove`` a type the assistant proposed, or ``add`` any
    change type the case does not hold yet (ADR-72; adding one the assistant
    proposed confirms that proposal). ``remove`` also takes off a type the
    owner added. A removal needs a reason; an addition's reason is optional.
    """

    decision: Literal["confirm", "remove", "add"]
    reason: Optional[str] = Field(default=None, max_length=1000)


class ValueDecisionBody(_Dto):
    decision: Literal["confirm", "correct"]
    value: Optional[str] = Field(default=None, max_length=2000)
    reason: Optional[str] = Field(default=None, max_length=1000)


class ReuseBody(_Dto):
    from_case_id: str = Field(min_length=1, max_length=40)
    field_ids: list[str] = Field(min_length=1, max_length=40)


class AnswerItem(_Dto):
    question_id: str = Field(min_length=1, max_length=120)
    field_id: Optional[str] = Field(default=None, max_length=40)
    value: str = Field(max_length=2000)


class AnswersBody(_Dto):
    answers: list[AnswerItem] = Field(min_length=1, max_length=50)


class DiscardBody(_Dto):
    reason: str = Field(max_length=1000)


class IntegrationActionBody(_Dto):
    scope: Optional[str] = Field(default=None, max_length=200)


class EmptyBody(_Dto):
    """``POST validate`` and ``POST submit`` carry no input: the server reads the
    case. The web posts ``{}``.
    """
