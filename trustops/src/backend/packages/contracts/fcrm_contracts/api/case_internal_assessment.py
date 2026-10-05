"""S1 internal routes of the assessment half (lane I3-A): what H1's assessment
core pushes into the analyst's views and reads back to score a case.

Called only by the service account (``fcrm_service``), idempotently: every
``PUT`` upserts one view document keyed by the case (the score also appends a
history row) and honours an ``Idempotency-Key`` header, because a harness step
may run again after a failure (ADR-28). Nothing here reaches the PO: the views
are analyst-only and S1's PO routes have no field to carry them.

Two groups live here:

* the **typed item shapes** (``ClauseSubject``, ``ControlProposed``...): what
  H1 puts in a review item's ``subject`` and ``proposed`` and what S1's pure
  score-input builder reads back, so the two sides share one definition of
  "the control's rating" instead of two copies of the key names;
* the **push bodies and results** of ``PUT .../screening|score|critic|
  completeness|recommendation-draft|handoff`` and the ``GET .../score-request``
  and ``GET .../evidence`` answers.

An agent never writes a score: ``ScorePush.score`` is S2's own response, and
the item shapes carry the *inputs* (likelihood, impact, a control's rating)
that S2 consumes.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from fcrm_contracts.api.scoring import ScoreRequest, ScoreResponse
from fcrm_contracts.enums import Band, Domain
from fcrm_contracts.proposals.narrative import (
    BusinessSummary,
    CompletenessReport,
    Condition,
    Question,
)
from fcrm_contracts.proposals.provenance import SourceRef
from fcrm_contracts.proposals.screening import FastLaneReason

# ── typed item shapes (review_items.subject / .proposed / .decided.value) ───────


class ClauseSubject(BaseModel):
    """``subject`` of a ``clause`` item: which policy clause, in which domain."""

    model_config = ConfigDict(extra="forbid")

    clause_id: str
    domain: Domain
    corpus_v: int


class ClauseProposed(BaseModel):
    """``proposed`` of a ``clause`` item. ``quote`` is verbatim from the clause
    (rule 8); ``applicability`` is the agent's own short note.
    """

    model_config = ConfigDict(extra="forbid")

    quote: str
    applicability: str
    confidence: float = Field(ge=0.0, le=1.0)


class FactorSubject(BaseModel):
    """``subject`` of a ``factor`` item (the analyst's "rating"): one per
    in-scope domain.
    """

    model_config = ConfigDict(extra="forbid")

    domain: Domain


class FactorProposed(BaseModel):
    """``proposed`` of a ``factor`` item: likelihood and impact are *inputs* to
    S2, never a score.
    """

    model_config = ConfigDict(extra="forbid")

    likelihood: int = Field(ge=1, le=5)
    impact: int = Field(ge=1, le=5)
    justification: str
    quote: str
    confidence: float = Field(ge=0.0, le=1.0)


class FactorValue(BaseModel):
    """``decided.value`` of an overridden or adjusted ``factor`` item."""

    model_config = ConfigDict(extra="forbid")

    likelihood: int = Field(ge=1, le=5)
    impact: int = Field(ge=1, le=5)


class ControlSubject(BaseModel):
    """``subject`` of a ``control`` item. ``planned`` marks a control that is
    recorded and shown but never scored (rule 7); ``catalogue_weight`` is
    stamped by H1 from S3's inventory at ``controls_v`` (an agent never supplies
    one), so S1 can answer a complete score request without calling S3.
    """

    model_config = ConfigDict(extra="forbid")

    control_id: str
    domain: Domain
    planned: bool = False
    name: Optional[str] = None
    catalogue_weight: float = Field(gt=0.0, le=1.0)
    controls_v: int


class ControlProposed(BaseModel):
    """``proposed`` of a ``control`` item. ``rating`` is ``None`` exactly for a
    planned control.
    """

    model_config = ConfigDict(extra="forbid")

    rating: Optional[float] = Field(default=None, ge=0.0, le=10.0)
    rationale: str
    evidence_artifact: str
    evidence_date: str
    quote: str
    confidence: float = Field(ge=0.0, le=1.0)


class ControlValue(BaseModel):
    """``decided.value`` of an overridden or adjusted ``control`` item."""

    model_config = ConfigDict(extra="forbid")

    rating: float = Field(ge=0.0, le=10.0)


class GapSubject(BaseModel):
    """``subject`` of a research ``gap`` item (a domain with no applicable
    clause). Intake ``gap`` items carry ``question_id`` instead and are not
    this shape; ``origin`` tells them apart.
    """

    model_config = ConfigDict(extra="forbid")

    origin: Literal["research"] = "research"
    domain: Domain
    description: str
    searched_query: str
    searched_indexes: list[str] = Field(default_factory=list)
    searched_hits: int = Field(default=0, ge=0)


class CriticFindingSubject(BaseModel):
    """``subject`` of a ``critic_finding`` item."""

    model_config = ConfigDict(extra="forbid")

    finding_type: Literal[
        "precedent_deviation", "evidence_recency", "rule_violation", "other"
    ]
    severity: Literal["low", "medium", "high"]
    record_refs: list[str] = Field(default_factory=list)
    text: str
    confidence: float = Field(ge=0.0, le=1.0)
    quote: str


# ── views H1 pushes (PUT) ───────────────────────────────────────────────────────


class ViewWriteResult(BaseModel):
    """What every ``PUT`` answers: which view was written, its id when it has
    one (the score snapshot's), and whether this call changed nothing because
    the same ``Idempotency-Key`` already did the write.
    """

    model_config = ConfigDict(extra="forbid")

    case_id: str
    view: Literal[
        "screening", "score", "critic", "completeness", "recommendation", "handoff"
    ]
    id: Optional[str] = None
    replayed: bool = False


class ScreeningPush(BaseModel):
    """``PUT /internal/cases/{id}/screening``: the screening agent's R4
    proposal. ``gate_id`` is the ``fast_lane_confirm`` gate the analyst decides
    (``None`` when no fast lane was proposed).
    """

    model_config = ConfigDict(extra="forbid")

    run_id: str
    fast_lane_proposed: bool
    reasons: list[FastLaneReason] = Field(default_factory=list)
    proposal_id: Optional[str] = None
    gate_id: Optional[str] = None


class PreviousScore(BaseModel):
    """The score of record before a re-score, for the "was 9.8, now 10.4" line."""

    model_config = ConfigDict(extra="forbid")

    composite: float
    band: Band


class ScorePush(BaseModel):
    """``PUT /internal/cases/{id}/score``: one S2 result of record.

    Attributes:
        run_id: The run that asked for it.
        purpose: ``proposed`` (scored from the agents' proposals) or
            ``confirmed`` (re-scored after an analyst decision).
        request: The exact request H1 posted to S2: every likelihood, impact
            and rating that went in (a planned control is never among them).
        score: S2's response, stored verbatim.
        previous: The composite and band before this score, on a re-score.
        boundary: Whether the boundary flag is raised (R3: needs an
            acknowledgement).
        band_changed: Whether the band differs from ``previous``.
        gate_id: The ``ack`` gate this score opened, if any.
    """

    model_config = ConfigDict(extra="forbid")

    run_id: str
    purpose: Literal["proposed", "confirmed"]
    request: ScoreRequest
    score: ScoreResponse
    previous: Optional[PreviousScore] = None
    boundary: bool = False
    band_changed: bool = False
    gate_id: Optional[str] = None


class CriticFindingItem(BaseModel):
    """One critic finding as the analyst sees it; ``finding_id`` is the id of
    its ``critic_finding`` review item and ``gate_id`` the gate that decides it.
    """

    model_config = ConfigDict(extra="forbid")

    finding_id: str
    gate_id: Optional[str] = None
    finding_type: Literal[
        "precedent_deviation", "evidence_recency", "rule_violation", "other"
    ]
    severity: Literal["low", "medium", "high"]
    record_refs: list[str] = Field(default_factory=list)
    text: str
    source_ref: SourceRef = Field(discriminator="kind")
    quote: str
    confidence: float = Field(ge=0.0, le=1.0)


class CriticPush(BaseModel):
    """``PUT /internal/cases/{id}/critic``: the critic's findings."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    findings: list[CriticFindingItem] = Field(default_factory=list)


class CompletenessPush(BaseModel):
    """``PUT /internal/cases/{id}/completeness``: the narrative agent's
    completeness report and the questions it drafted. ``gate_id`` is the
    ``completeness`` gate (ask or proceed) the analyst decides.
    """

    model_config = ConfigDict(extra="forbid")

    run_id: str
    report: CompletenessReport
    questions: list[Question] = Field(default_factory=list)
    gate_id: str
    proposal_id: Optional[str] = None


class NarrativeSectionItem(BaseModel):
    """One section of the recommendation text with the claim it rests on."""

    model_config = ConfigDict(extra="forbid")

    key: str
    text: str
    source_ref: SourceRef = Field(discriminator="kind")
    quote: str
    confidence: float = Field(ge=0.0, le=1.0)


class RecommendationDraftPush(BaseModel):
    """``PUT /internal/cases/{id}/recommendation-draft``: the narrative agent's
    recommendation draft. It replaces the *draft* only: edits the analyst made
    to an earlier draft are kept by S1 beside it, never overwritten.
    ``business_summary.text`` is empty when H1 withheld it because the
    restricted-term scan found a hit.
    """

    model_config = ConfigDict(extra="forbid")

    run_id: str
    sections: list[NarrativeSectionItem] = Field(default_factory=list)
    conditions: list[Condition] = Field(default_factory=list)
    business_summary: BusinessSummary
    score_record_seq: Optional[int] = None
    proposal_ids: list[str] = Field(default_factory=list)


class HandoffPush(BaseModel):
    """``PUT /internal/cases/{id}/handoff``: the pack H1 assembled from the
    locked numbers and the four release checks it computed. ``checks`` keys are
    S5's release-check names; ``audit_head`` is the chain record the pack was
    built against (the ``recommendation.locked`` record).
    """

    model_config = ConfigDict(extra="forbid")

    run_id: str
    pack_id: str
    pack_version: int
    content_hash: str
    checks: dict[str, bool]
    audit_head_seq: int
    audit_head_hash: str
    gate_id: Optional[str] = None


# ── reads (GET) ─────────────────────────────────────────────────────────────────


class OverrideNote(BaseModel):
    """An analyst decision that changed a value S2 was given, shown next to the
    score request so a reader can see why it differs from the proposal.
    """

    model_config = ConfigDict(extra="forbid")

    item_id: str
    kind: Literal["factor", "control"]
    subject_id: str
    proposed: dict[str, Any]
    decided: dict[str, Any]
    reason: Optional[str] = None
    by: str


class ScoreRequestView(BaseModel):
    """``GET /internal/cases/{id}/score-request``: everything S2 needs to score
    the case as it stands.

    ``request`` is a complete, validated S2 request: one domain per current
    ``factor`` item, in it every control whose item is not removed and not
    planned, each at its decided value (override or adjustment) or, while
    undecided, at its proposed value. ``planned_control_ids`` lists the planned
    controls that were left out: they are recorded and shown, never scored
    (rule 7), and are not in ``request`` by construction.
    """

    model_config = ConfigDict(extra="forbid")

    case_id: str
    request: ScoreRequest
    planned_control_ids: list[str] = Field(default_factory=list)
    unscored_control_ids: list[str] = Field(default_factory=list)
    overrides: list[OverrideNote] = Field(default_factory=list)
    undecided_items: int = 0
