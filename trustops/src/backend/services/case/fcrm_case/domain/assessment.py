"""The pure logic of S1's assessment views: no I/O, no clock.

Two things live here. The **stored shapes** of the analyst-only views H1's
assessment core pushes (design A §2.1.1's ``critic_views``, ``completeness_
views``, ``recommendations``; plus ``screening_views`` and ``handoff_views``),
documented field by field in ``docs/architecture/data-model.md``. And the two
**derivations over review items** that must not be written twice:

* :func:`build_score_view`: the complete S2 request for the case as it stands
  (``GET /internal/cases/{id}/score-request``), used by H1 to score and re-score
  and by S2's simulate. It reads ``factor`` and ``control`` items only, never
  the planned controls, and never invents a number: a value is the decided one
  (override or adjustment) or the proposed one.
* :func:`control_evidence`: what the case already holds as evidence for one
  control (``GET .../evidence?control_id=``), shaped as the ``evidence_store``
  tool's output.

Superseded items (an item some later item names in ``supersedes``) are history:
they are never read as current. That is how a re-run replaces a proposal
without anyone deleting a row.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from fcrm_common.errors import ConflictError
from fcrm_contracts.api.case_internal_assessment import (
    ControlProposed,
    ControlSubject,
    ControlValue,
    FactorProposed,
    FactorSubject,
    FactorValue,
    OverrideNote,
    ScoreRequestView,
)
from fcrm_contracts.api.scoring import ControlInput, DomainInput, ScoreRequest
from fcrm_contracts.enums import Domain, ItemKind, ItemStatus
from fcrm_contracts.tools.evidence_store import EvidenceItem, EvidenceStoreOutput
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from fcrm_case.domain.views import ReviewItemRow

#: Item kinds an analyst decides through a ``review_item`` gate.
GATED_KINDS = frozenset(
    {ItemKind.CLAUSE, ItemKind.FACTOR, ItemKind.CONTROL, ItemKind.CRITIC_FINDING}
)

#: Statuses in which an analyst replaced the proposed value.
_VALUE_CHANGED = frozenset({ItemStatus.OVERRIDDEN, ItemStatus.ADJUSTED})


# ── stored shapes of the views ──────────────────────────────────────────────────


class ScreeningViewRow(BaseModel):
    """``screening_views`` (``_id = case_id``): the screening agent's R4
    proposal and the gate that decides it.
    """

    model_config = ConfigDict(extra="forbid")

    case_id: str
    run_id: str
    fast_lane_proposed: bool
    reasons: list[dict[str, Any]] = Field(default_factory=list)
    proposal_id: Optional[str] = None
    gate_id: Optional[str] = None
    updated_at: datetime


class CriticViewRow(BaseModel):
    """``critic_views`` (``_id = case_id``): the critic's findings, one per
    ``critic_finding`` item.
    """

    model_config = ConfigDict(extra="forbid")

    case_id: str
    run_id: str
    findings: list[dict[str, Any]] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    updated_at: datetime


class CompletenessViewRow(BaseModel):
    """``completeness_views`` (``_id = case_id``): the narrative agent's
    completeness report, its drafted questions and the ask/proceed gate.
    """

    model_config = ConfigDict(extra="forbid")

    case_id: str
    run_id: str
    report: dict[str, Any]
    questions: list[dict[str, Any]] = Field(default_factory=list)
    gate_id: str
    proposal_id: Optional[str] = None
    updated_at: datetime


class RecommendationRow(BaseModel):
    """``recommendations`` (``_id = case_id``): the narrative agent's draft and,
    beside it, what the analyst did to it.

    H1 writes ``draft`` and ``draft_seq`` only. ``edits``, ``locked`` and the
    lock fields belong to the analyst surface (the next lane) and are never
    touched by H1's push, so a re-drafted narrative cannot erase an edit: it
    arrives as a new ``draft_seq`` the analyst sees beside their edit.
    """

    model_config = ConfigDict(extra="forbid")

    case_id: str
    run_id: str
    draft: dict[str, Any]
    draft_seq: int = 1
    draft_at: datetime
    edits: Optional[dict[str, Any]] = None
    locked: bool = False
    locked_at: Optional[datetime] = None
    locked_by: Optional[str] = None


class HandoffViewRow(BaseModel):
    """``handoff_views`` (``_id = case_id``): the pack H1 assembled from the
    locked numbers and the four release checks it computed.
    """

    model_config = ConfigDict(extra="forbid")

    case_id: str
    run_id: str
    pack_id: str
    pack_version: int
    content_hash: str
    checks: dict[str, bool]
    audit_head_seq: int
    audit_head_hash: str
    gate_id: Optional[str] = None
    updated_at: datetime


# ── which items are current ─────────────────────────────────────────────────────


def current_items(rows: Sequence[ReviewItemRow]) -> list[ReviewItemRow]:
    """``rows`` without the items a later item supersedes, in the given order."""
    superseded = {row.supersedes for row in rows if row.supersedes is not None}
    return [row for row in rows if row.item_id not in superseded]


def _newest(rows: Sequence[ReviewItemRow]) -> ReviewItemRow:
    """The most recently created of ``rows`` (ties by id, so it is stable)."""
    return max(rows, key=lambda row: (row.created_at, row.item_id))


# ── the score request ───────────────────────────────────────────────────────────


def _parse[M: BaseModel](model: type[M], data: dict[str, Any], row: ReviewItemRow) -> M:
    """``data`` as ``model``.

    Raises:
        fcrm_common.errors.ConflictError: the stored item does not carry the
            shape its kind promises (a writer bug); naming the item, never its
            content.
    """
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise ConflictError(
            f"review item {row.item_id} is not a valid {model.__name__}",
            item_id=row.item_id,
            errors_count=exc.error_count(),
        ) from exc


def _decided_value(row: ReviewItemRow) -> dict[str, Any]:
    """``decided.value`` of an item whose value an analyst replaced.

    Raises:
        fcrm_common.errors.ConflictError: the item says it was overridden or
            adjusted but holds no replacement value.
    """
    value = row.decided.value if row.decided is not None else None
    if not value:
        raise ConflictError(
            f"review item {row.item_id} was {row.status.value} without a value",
            item_id=row.item_id,
        )
    return value


def _factor_values(row: ReviewItemRow) -> tuple[int, int, dict[str, Any]]:
    """``(likelihood, impact, proposed)`` of a factor item at its effective
    values: the replacement when an analyst replaced it, else the proposal.
    """
    proposed = _parse(FactorProposed, row.proposed, row)
    if row.status in _VALUE_CHANGED:
        decided = _parse(FactorValue, _decided_value(row), row)
        return (
            decided.likelihood,
            decided.impact,
            {
                "likelihood": proposed.likelihood,
                "impact": proposed.impact,
            },
        )
    return proposed.likelihood, proposed.impact, {}


def _control_rating(row: ReviewItemRow, proposed: ControlProposed) -> float:
    """A scored control's effective rating.

    Raises:
        fcrm_common.errors.ConflictError: a control that is not planned has no
            rating (the agent contract forbids it; a stored item that breaks it
            is refused here rather than scored as zero).
    """
    if row.status in _VALUE_CHANGED:
        return _parse(ControlValue, _decided_value(row), row).rating
    if proposed.rating is None:
        raise ConflictError(
            f"control item {row.item_id} is not planned but has no rating",
            item_id=row.item_id,
        )
    return proposed.rating


def _override_note(
    row: ReviewItemRow, kind: str, subject_id: str, proposed: dict[str, Any]
) -> OverrideNote:
    """The note for an item an analyst replaced the value of.

    Raises:
        fcrm_common.errors.ConflictError: the item holds no decision.
    """
    decided = row.decided
    if decided is None:
        raise ConflictError(
            f"review item {row.item_id} was {row.status.value} without a decision",
            item_id=row.item_id,
        )
    return OverrideNote(
        item_id=row.item_id,
        kind=kind,  # type: ignore[arg-type]
        subject_id=subject_id,
        proposed=proposed,
        decided=dict(decided.value or {}),
        reason=decided.reason,
        by=decided.by,
    )


@dataclass(frozen=True)
class _ScoredControl:
    """A scored control item with its parsed subject and proposal."""

    row: ReviewItemRow
    subject: ControlSubject
    proposed: ControlProposed


def _factor_inputs(
    items: Sequence[ReviewItemRow],
) -> tuple[dict[Domain, tuple[int, int]], list[OverrideNote]]:
    """Likelihood and impact per domain (the newest factor item of each, at its
    effective values) and the notes for the ones an analyst replaced.
    """
    candidates: dict[Domain, list[ReviewItemRow]] = {}
    for row in items:
        if row.kind is ItemKind.FACTOR and row.status is not ItemStatus.REMOVED:
            domain = _parse(FactorSubject, row.subject, row).domain
            candidates.setdefault(domain, []).append(row)
    domains: dict[Domain, tuple[int, int]] = {}
    notes: list[OverrideNote] = []
    for domain, rows in candidates.items():
        row = _newest(rows)
        likelihood, impact, proposed = _factor_values(row)
        domains[domain] = (likelihood, impact)
        if row.status in _VALUE_CHANGED:
            notes.append(_override_note(row, "factor", domain.value, proposed))
    return domains, notes


def _split_controls(
    items: Sequence[ReviewItemRow],
) -> tuple[dict[str, _ScoredControl], list[str]]:
    """The scored controls by id (the newest when two items name one control) and
    the ids of the planned ones, which are skipped by construction.
    """
    scored: dict[str, _ScoredControl] = {}
    planned: list[str] = []
    for row in items:
        if row.kind is not ItemKind.CONTROL or row.status is ItemStatus.REMOVED:
            continue
        subject = _parse(ControlSubject, row.subject, row)
        proposed = _parse(ControlProposed, row.proposed, row)
        if subject.planned:
            planned.append(subject.control_id)
            continue
        known = scored.get(subject.control_id)
        if known is None or _newest([known.row, row]) is row:
            scored[subject.control_id] = _ScoredControl(row, subject, proposed)
    return scored, planned


def _control_inputs(
    domains: Sequence[Domain], scored: dict[str, _ScoredControl]
) -> tuple[dict[Domain, list[ControlInput]], list[str], list[OverrideNote]]:
    """The scored controls grouped under the domains that have a factor item;
    the ids of those whose domain has none; and the notes for replaced ratings.
    """
    by_domain: dict[Domain, list[ControlInput]] = {domain: [] for domain in domains}
    unscored: list[str] = []
    notes: list[OverrideNote] = []
    for control_id in sorted(scored):
        control = scored[control_id]
        if control.subject.domain not in by_domain:
            unscored.append(control_id)
            continue
        by_domain[control.subject.domain].append(
            ControlInput(
                control_id=control_id,
                domain=control.subject.domain,
                rating=_control_rating(control.row, control.proposed),
                catalogue_weight=control.subject.catalogue_weight,
            )
        )
        if control.row.status in _VALUE_CHANGED:
            notes.append(
                _override_note(
                    control.row,
                    "control",
                    control_id,
                    {"rating": control.proposed.rating},
                )
            )
    return by_domain, unscored, notes


def build_score_view(
    case_id: str, scoring_v: int, rows: Sequence[ReviewItemRow]
) -> ScoreRequestView:
    """The S2 request for ``case_id`` as its items stand.

    One domain per current, not-removed ``factor`` item; in it every current,
    not-removed, not-planned ``control`` item of that domain at its effective
    rating. A control whose domain has no factor item cannot be scored and is
    listed in ``unscored_control_ids``. When two current control items name the
    same control id the newest is used (a request may not count one control
    twice). Planned controls are skipped, by construction, and listed.

    Raises:
        fcrm_common.errors.ConflictError: the case has no factor item yet (the
            assessment has not run), or an item does not carry the shape its
            kind promises.
    """
    items = current_items(rows)
    domains, factor_notes = _factor_inputs(items)
    if not domains:
        raise ConflictError(
            "this case has no likelihood and impact to score yet",
            case_id=case_id,
        )
    scored, planned = _split_controls(items)
    by_domain, unscored, control_notes = _control_inputs(list(domains), scored)
    request = ScoreRequest(
        case_id=case_id,
        scoring_v=scoring_v,
        domains=[
            DomainInput(
                domain=domain,
                likelihood=domains[domain][0],
                impact=domains[domain][1],
                controls=by_domain[domain],
            )
            for domain in sorted(domains, key=lambda d: d.value)
        ],
    )
    return ScoreRequestView(
        case_id=case_id,
        request=request,
        planned_control_ids=sorted(set(planned)),
        unscored_control_ids=unscored,
        overrides=sorted([*factor_notes, *control_notes], key=lambda n: n.item_id),
        undecided_items=sum(
            1
            for row in items
            if row.kind in GATED_KINDS and row.status is ItemStatus.PROPOSED
        ),
    )


# ── evidence ────────────────────────────────────────────────────────────────────


def _doc_ids(row: ReviewItemRow) -> list[str]:
    """The documents an item's source references cite."""
    return [
        str(ref["doc_id"])
        for ref in row.source_refs
        if ref.get("kind") == "doc" and ref.get("doc_id")
    ]


def control_evidence(
    case_id: str, control_id: str, rows: Sequence[ReviewItemRow]
) -> EvidenceStoreOutput:
    """What the case holds as evidence for ``control_id``: one entry per
    distinct ``(artifact, date)`` among the control's items (a planned control
    included, its date empty), newest item first, each with the document it cites
    when it cites one. ``case_id`` is only used to refuse another case's rows.

    Raises:
        fcrm_common.errors.ConflictError: a row belongs to another case.
    """
    matching: list[ReviewItemRow] = []
    for row in rows:
        if row.case_id != case_id:
            raise ConflictError(f"review item {row.item_id} belongs to another case")
        if row.kind is not ItemKind.CONTROL:
            continue
        if row.subject.get("control_id") == control_id:
            matching.append(row)
    seen: set[tuple[str, str]] = set()
    items: list[EvidenceItem] = []
    for row in sorted(matching, key=lambda r: (r.created_at, r.item_id), reverse=True):
        artifact = str(row.proposed.get("evidence_artifact") or "")
        when = str(row.proposed.get("evidence_date") or "")
        if not artifact or (artifact, when) in seen:
            continue
        seen.add((artifact, when))
        docs = _doc_ids(row)
        items.append(
            EvidenceItem(
                artifact=artifact,
                evidence_date=when,
                doc_id=docs[0] if docs else None,
            )
        )
    return EvidenceStoreOutput(control_id=control_id, items=items)
