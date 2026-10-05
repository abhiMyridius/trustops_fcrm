"""The case document S1 actually stores.

Not a wire contract: ``fcrm_case.shaping`` maps a :class:`CaseRecord` onto
whichever of ``CaseDetailPO`` / ``CaseDetailAnalyst`` / ``CaseDetailInternal``
(``fcrm_contracts.api.case_*``) the caller's role is entitled to, the same
way ``fcrm_scoring.seedmodel`` adapts between the seed's raw shape and
``fcrm_contracts.api.scoring`` types. Keeping the storage shape separate
from the three wire shapes is what makes "the PO response model structurally
cannot carry a score" true: ``CaseDetailPO`` has no field a bug could even
assign a score into.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fcrm_contracts.enums import CaseState, ChangeType
from fcrm_contracts.versions import PinnedVersions
from pydantic import BaseModel, ConfigDict, Field

from fcrm_case.domain.fieldset import FieldDef

#: States in which a case still accepts field writes and (re)submission.
#: Everything else -- committee voting (DECIDE) and CLOSED -- rejects
#: further writes with 409 (CLAUDE.md: "closed cases reject writes").
OPEN_FOR_WRITES_STATES: frozenset[CaseState] = frozenset(
    {
        CaseState.INTAKE,
        CaseState.REVIEW_AND_SUBMIT,
        CaseState.TRIAGE,
        CaseState.SCREENING,
        CaseState.ASSESS,
        CaseState.CRITIC,
        CaseState.COMPLETENESS,
        CaseState.CLARIFICATION,
        CaseState.REVIEW,
    }
)


class StageFlags(BaseModel):
    """The run-snapshot flags H1 pushes with ``PATCH /internal/cases/{id}/
    state``: sub-states of a stage, not stages of their own (design A §3.1).
    """

    model_config = ConfigDict(extra="forbid")

    fast_lane_proposed: bool = False
    round_open: bool = False
    locked: bool = False
    pack_version: Optional[int] = None
    vote_round: Optional[int] = None


class FieldValueRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_id: str
    label: str
    value: Optional[str] = None
    source: Optional[str] = None
    how: str = "owner_corrected"
    filled_at: datetime


class DocumentRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doc_id: str
    name: str
    pages: int
    stored_at: datetime


class SubmissionVersionRecord(BaseModel):
    """One submission snapshot: every field's value at that moment, so any
    two versions can be diffed later without replaying intermediate writes.
    """

    model_config = ConfigDict(extra="forbid")

    version: int
    created_at: datetime
    created_by: str
    field_values: dict[str, Optional[str]] = {}


class CaseRecord(BaseModel):
    """The Mongo document for ``fcrm_case.cases`` (keyed by ``case_id``)."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    title: str
    unit: str
    owner: str
    created_by: str
    analyst_id: Optional[str] = None
    state: CaseState = CaseState.INTAKE
    change_types: list[ChangeType]
    # The display names behind ``owner``/``analyst_id`` (user ids), stored when
    # the case is created or assigned so a read never calls X1.
    owner_name: Optional[str] = None
    analyst_name: Optional[str] = None
    run_id: Optional[str] = None
    domains: list[str] = Field(default_factory=list)
    geographies: list[str] = Field(default_factory=list)
    stage_flags: StageFlags = Field(default_factory=StageFlags)
    fieldset_v: int
    completion_threshold: float
    pinned_versions: PinnedVersions
    required_fields: list[FieldDef]
    fields: dict[str, FieldValueRecord] = {}
    documents: list[DocumentRecord] = []
    submission_versions: list[SubmissionVersionRecord] = []
    outcome_summary: Optional[str] = None
    # The ``outcomes`` row that closed the case (its id is the case id).
    outcome_ref: Optional[str] = None
    created_at: datetime
    submitted_at: Optional[datetime] = None
    decided_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    current_submission_v: Optional[int] = None
    # Highest ``activity.seq`` allocated for this case; bumped in the same
    # write that inserts the activity row, so seqs are gap-free per case.
    activity_seq: int = 0

    @property
    def filled_field_ids(self) -> set[str]:
        r"""Field ids currently holding a non-empty value, from any source.

        A value that is empty once stripped (``""``, ``"   "``, ``"\n"``) is
        not filled: it would otherwise count towards completion and let a
        case clear the submit threshold on whitespace alone.
        """
        return {
            field_id
            for field_id, value in self.fields.items()
            if value.value is not None and value.value.strip() != ""
        }

    @property
    def open_for_writes(self) -> bool:
        """Whether a field write, title edit or (re)submission may proceed.

        False once the case is closed for voting (``DECIDE``/``CLOSED``) or
        has already been submitted at least once -- the task's "a case
        closed for voting or already submitted rejects further writes"
        rule, checked in one place rather than at each route.
        """
        return self.state in OPEN_FOR_WRITES_STATES and self.submitted_at is None
