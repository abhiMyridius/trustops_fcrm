"""The assessment, policy, risk and handoff views as the analyst reads them,
built from stored review items, the score snapshot of record and the views H1
pushed. Pure, no I/O, no clock (callers pass ``today``).

Nothing here computes a score, a band, a rating or a recommendation:

* every figure of a risk area (L, I, inherent, control effectiveness, retention,
  residual, weight, contribution) is the stored snapshot's, S2's answer;
* a control's rating and a factor's justification are the agent's, or the
  analyst's replacement, copied from the item; a clause's **quote** is the
  stored item's, so the corpus lookup only says which source and section it is;
* the one derived number is a control's share of its domain's catalogue weight
  (``weight_norm``), a presentation of the weights the items carry.

**Two item shapes.** An item H1 wrote carries the typed ``subject`` and
``proposed`` of :mod:`fcrm_contracts.api.case_internal_assessment`. An item the
seed wrote for a case that predates this system is flatter (a likelihood and an
impact are two items; a control carries ``evidence`` instead of
``evidence_artifact``). The readers below accept both and never guess: a field an
item does not hold is left empty, not invented.

**Superseded items are history.** Every view starts from
:func:`fcrm_case.domain.assessment.current_items`.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Final, Optional

from fcrm_contracts.api.case_analyst_read import (
    AgentFailed,
    AssessmentSummary,
    ClarificationRound,
    ClarificationsView,
    ClauseItem,
    CompletenessSummary,
    ControlItem,
    CriticFinding,
    DraftQuestion,
    FactorAnchor,
    Flag,
    HandoffView,
    ItemDecision,
    OutcomeLine,
    Override,
    PackCounts,
    PackSummary,
    PlannedControl,
    PolicyGap,
    PolicyView,
    ReleaseCheck,
    RiskArea,
    RiskView,
    ScoreBox,
    ScoreShown,
    SubmissionSummary,
    ThreadAnalyst,
    ThreadAnswerView,
)
from fcrm_contracts.api.case_po import SourceRef
from fcrm_contracts.enums import CaseState, Domain, ItemKind, ItemStatus

from fcrm_case.domain.analyst_clauses import (
    added_reason,
    is_analyst_clause,
    shown_clause_rows,
)
from fcrm_case.domain.analyst_records import GapRow, PlannedControlRow
from fcrm_case.domain.analyst_views import assessment_ready, band_no, score_box
from fcrm_case.domain.assessment import (
    CompletenessViewRow,
    CriticViewRow,
    HandoffViewRow,
    RecommendationRow,
    current_items,
)
from fcrm_case.domain.clarifications import ClarificationThreadRow
from fcrm_case.domain.completion import compute_completion
from fcrm_case.domain.record import CaseRecord
from fcrm_case.domain.views import (
    DocumentRow,
    FieldValueRow,
    IntakeViewRow,
    ReviewItemRow,
    ScoreSnapshotRow,
)
from fcrm_case.reference import (
    RECENCY_MONTHS,
    ClauseRef,
    ControlRef,
    domain_labels,
    impact_scale,
    likelihood_scale,
    months_between,
    scoring_model_version,
)

logger = logging.getLogger(__name__)

_GATED = frozenset(
    {ItemKind.CLAUSE, ItemKind.FACTOR, ItemKind.CONTROL, ItemKind.CRITIC_FINDING}
)
_VALUE_CHANGED = frozenset({ItemStatus.OVERRIDDEN, ItemStatus.ADJUSTED})
_SEVERITY_ORDER: Final = {"high": 0, "medium": 1, "low": 2}
_DIGITS = re.compile(r"\d+")

_LEGACY_FACTOR_TEXT: Final = (
    "Seeded case: factor justifications are not part of the seed."
)

#: The four release checks in the web's order, with their labels.
CHECK_LABELS: Final[tuple[tuple[str, str], ...]] = (
    ("locked", "Recommendation locked"),
    ("all_items_reviewed", "Every item reviewed"),
    ("boundary_and_band_acknowledged", "Boundary flag and band change acknowledged"),
    ("business_summary_present", "Business summary present"),
)


# -- reading items -----------------------------------------------------------------


def decision_of(row: ReviewItemRow) -> ItemDecision:
    """An item's state in the web's words: undecided, accepted (a clause the
    analyst kept), confirmed, overridden (a replaced value) or removed.
    """
    status = row.status
    if status is ItemStatus.PROPOSED:
        return "pending"
    if status is ItemStatus.REMOVED:
        return "removed"
    if status in _VALUE_CHANGED:
        return "overridden"
    if row.kind is ItemKind.CLAUSE:
        return "accepted"
    return "confirmed"


def gate_open(row: ReviewItemRow) -> bool:
    """Whether an analyst's gate on ``row`` is still waiting: a gated kind that
    has a gate and no decision.
    """
    return (
        row.kind in _GATED
        and row.gate_id is not None
        and row.status is ItemStatus.PROPOSED
    )


def _text(value: Any) -> str:
    return "" if value is None else str(value)


@dataclass(frozen=True)
class ControlFacts:
    """A control item read tolerantly: what the row says about the control, with
    the catalogue filling a name or weight only an older item lacks.
    """

    item: ReviewItemRow
    control_id: str
    domain: str
    planned: bool
    name: str
    weight: float
    proposed_rating: Optional[float]
    rating: Optional[float]
    evidence: str
    evidence_date: str


def control_facts(
    row: ReviewItemRow, catalogue: Mapping[str, ControlRef]
) -> Optional[ControlFacts]:
    """``row`` as a control, or ``None`` when it names no control or domain."""
    control_id = row.subject.get("control_id")
    domain = row.subject.get("domain")
    if not control_id or not domain:
        logger.warning(
            "control item without a control or domain",
            extra={"case_id": row.case_id, "item_id": row.item_id},
        )
        return None
    known = catalogue.get(str(control_id))
    weight = row.subject.get("catalogue_weight")
    proposed = row.proposed.get("rating")
    rating = proposed
    if row.status in _VALUE_CHANGED and row.decided is not None:
        replaced = (row.decided.value or {}).get("rating")
        rating = replaced if replaced is not None else proposed
    return ControlFacts(
        item=row,
        control_id=str(control_id),
        domain=str(domain),
        planned=bool(row.subject.get("planned", False)),
        name=_text(row.subject.get("name") or (known.name if known else control_id)),
        weight=float(weight)
        if weight is not None
        else (known.weight if known else 0.0),
        proposed_rating=float(proposed) if proposed is not None else None,
        rating=float(rating) if rating is not None else None,
        evidence=_text(
            row.proposed.get("evidence_artifact") or row.proposed.get("evidence")
        ),
        evidence_date=_text(row.proposed.get("evidence_date")),
    )


def controls_of(
    rows: Sequence[ReviewItemRow], catalogue: Mapping[str, ControlRef]
) -> list[ControlFacts]:
    """The current control items, in the stored order."""
    found = (
        control_facts(r, catalogue)
        for r in current_items(rows)
        if r.kind is ItemKind.CONTROL
    )
    return [f for f in found if f is not None]


def weight_shares(controls: Sequence[ControlFacts]) -> dict[str, float]:
    """Each scored control's share of its domain's catalogue weight (a planned or
    removed control has none), rounded to four places for display.
    """
    scored = [
        c for c in controls if not c.planned and c.item.status is not ItemStatus.REMOVED
    ]
    totals: dict[str, float] = {}
    for c in scored:
        totals[c.domain] = totals.get(c.domain, 0.0) + c.weight
    return {
        c.control_id: round(c.weight / totals[c.domain], 4) if totals[c.domain] else 0.0
        for c in scored
    }


def is_stale(evidence_date: str, today: date) -> bool:
    """Whether evidence dated ``evidence_date`` (ISO) is older than the recency
    bound; evidence with no readable date is not called stale.
    """
    try:
        dated = date.fromisoformat(evidence_date[:10])
    except ValueError:
        return False
    return months_between(dated, today) > RECENCY_MONTHS


def _display_name(record: CaseRecord, user_id: str) -> str:
    if record.analyst_id is not None and user_id == record.analyst_id:
        return record.analyst_name or user_id
    return user_id


# -- critic findings ---------------------------------------------------------------


def _record_numbers(refs: Sequence[Any]) -> list[int]:
    """The audit record numbers a finding cites (``"#34"`` or ``"34"``)."""
    numbers: list[int] = []
    for ref in refs:
        match = _DIGITS.search(str(ref))
        if match is not None:
            numbers.append(int(match.group()))
    return numbers


def _finding_target(finding: Mapping[str, Any]) -> Optional[str]:
    source = finding.get("source_ref")
    if isinstance(source, Mapping) and source.get("kind") == "control":
        return _text(source.get("control_id")) or None
    return None


def critic_findings(
    view: Optional[CriticViewRow], items: Sequence[ReviewItemRow]
) -> list[CriticFinding]:
    """The critic's current findings: those whose review item is not superseded,
    each with the gate that decides it, most severe first.
    """
    if view is None:
        return []
    by_id = {
        r.item_id: r for r in current_items(items) if r.kind is ItemKind.CRITIC_FINDING
    }
    found: list[CriticFinding] = []
    for raw in view.findings:
        item = by_id.get(_text(raw.get("finding_id")))
        if item is None and by_id:
            continue
        found.append(
            CriticFinding(
                id=_text(raw["finding_id"]),
                type=_text(raw.get("finding_type")),
                severity=raw.get("severity", "low"),
                text=_text(raw.get("text")),
                records=_record_numbers(raw.get("record_refs") or []),
                target=_finding_target(raw),
                gate_id=item.gate_id if item is not None else raw.get("gate_id"),
                gate_open=gate_open(item) if item is not None else False,
            )
        )
    return sorted(found, key=lambda f: (_SEVERITY_ORDER.get(f.severity, 3), f.id))


# -- the assessment summary --------------------------------------------------------


def _flags(
    items: Sequence[ReviewItemRow],
    fields: Sequence[FieldValueRow],
    intake: Optional[IntakeViewRow],
    corpus_version: str,
) -> list[Flag]:
    """What the analyst should notice, from what is stored: a new fact the owner
    raised at intake, a gap the research agent searched for and did not find, and
    a value the owner corrected (the extracted original is kept).
    """
    flags: list[Flag] = []
    for index, raw in enumerate(intake.flags if intake is not None else []):
        text = _text(raw.get("text"))
        if text:
            flags.append(
                Flag(
                    id=f"fl_intake_{index + 1}",
                    text=f"New fact at intake: {text}",
                    source=SourceRef(label="owner answer"),
                )
            )
    for row in current_items(items):
        if row.kind is ItemKind.GAP and row.subject.get("origin") == "research":
            flags.append(
                Flag(
                    id=f"fl_gap_{len(flags) + 1}",
                    text=_text(row.subject.get("description")),
                    source=SourceRef(label=f"corpus {corpus_version} search"),
                )
            )
    for value in fields:
        original = next((h for h in value.history if h.origin == "extracted"), None)
        if value.origin == "owner_declared" and original is not None:
            where = original.source
            label = (
                f"case p.{where.page} l.{where.line_start}"
                if where is not None and where.page is not None
                else "case record"
            )
            flags.append(
                Flag(
                    id=f"fl_corrected_{value.field_id}",
                    text=(
                        f"Owner corrected {value.label}: {_text(original.value)} "
                        f"→ {_text(value.value)} (owner declared, original kept)"
                    ),
                    source=SourceRef(label=label),
                )
            )
    return flags


def _completeness(view: Optional[CompletenessViewRow]) -> CompletenessSummary:
    if view is None:
        return CompletenessSummary(
            missing=[], stale=[], contradictory=[], drafted=0, kept=0
        )
    report = view.report
    return CompletenessSummary(
        missing=[str(x) for x in report.get("missing", [])],
        stale=[str(x) for x in report.get("stale", [])],
        contradictory=[str(x) for x in report.get("contradictory", [])],
        drafted=int(report.get("questions_drafted", 0)),
        kept=int(report.get("questions_kept", 0)),
        gate_id=view.gate_id,
        gate_open=False,
    )


@dataclass(frozen=True)
class AssessmentInputs:
    """Everything the assessment summary is built from, already read."""

    record: CaseRecord
    snapshot: Optional[ScoreSnapshotRow]
    review_started: bool
    items: Sequence[ReviewItemRow]
    fields: Sequence[FieldValueRow]
    documents: Sequence[DocumentRow]
    intake: Optional[IntakeViewRow]
    critic: Optional[CriticViewRow]
    completeness: Optional[CompletenessViewRow]
    decided_gate_ids: frozenset[str]


def assessment_summary(inputs: AssessmentInputs) -> AssessmentSummary:
    """The Summary tab: what the submission holds, what to notice, the critic's
    findings, the completeness report and the score box (withheld until review
    starts). ``ready`` says whether the assessment core has finished its part.
    """
    record = inputs.record
    ready = assessment_ready(record, inputs.completeness is not None)
    filled = {f.field_id for f in inputs.fields if (f.value or "").strip()}
    completion = compute_completion(record.required_fields, filled)
    completeness = _completeness(inputs.completeness)
    completeness = completeness.model_copy(
        update={
            "gate_open": _completeness_open(
                inputs.completeness, inputs.decided_gate_ids
            )
        }
    )
    box = score_box(
        inputs.snapshot,
        review_started=inputs.review_started,
        assessment_ready=ready,
    )
    return AssessmentSummary(
        ready=ready,
        submission=SubmissionSummary(
            version=record.current_submission_v or 0,
            fields_filled=completion.filled_count,
            fields_required=completion.required_count,
            documents=len(inputs.documents),
            submitted_at=record.submitted_at.isoformat()
            if record.submitted_at is not None
            else None,
        ),
        flags=_flags(
            inputs.items,
            inputs.fields,
            inputs.intake,
            f"v{record.pinned_versions.corpus_v}",
        )
        if ready
        else [],
        completeness=completeness,
        critic_findings=critic_findings(inputs.critic, inputs.items) if ready else [],
        score=box,
        agent_failed=AgentFailed(
            step="agent run",
            reason="An agent failed twice. The case is with you: nothing was skipped.",
        )
        if record.state is CaseState.HANDOFF
        else None,
    )


def _completeness_open(
    view: Optional[CompletenessViewRow], decided_gate_ids: frozenset[str]
) -> bool:
    """The completeness gate (ask or proceed) is open from the report until the
    analyst's ledger holds a decision on it.
    """
    return view is not None and view.gate_id not in decided_gate_ids


# -- policy ------------------------------------------------------------------------


def _corpus(row: ReviewItemRow) -> str:
    version = row.subject.get("corpus_v")
    return f"v{version}" if version is not None else ""


def clause_items(
    rows: Sequence[ReviewItemRow], catalogue: Mapping[str, ClauseRef]
) -> list[ClauseItem]:
    """The current clause items with their verbatim stored quote and the source
    and section the corpus gives the clause. A clause the corpus does not hold is
    shown by its id with no source (never an invented one).
    """
    items: list[ClauseItem] = []
    for row in shown_clause_rows(rows):
        clause_id = _text(row.subject.get("clause_id"))
        known = catalogue.get(clause_id)
        # A clause the analyst added carries the citation S3 gave it (ADR-74).
        mine = is_analyst_clause(row)
        if known is None and not mine:
            logger.warning(
                "clause item names a clause the corpus does not hold",
                extra={"case_id": row.case_id, "clause_id": clause_id},
            )
        items.append(
            ClauseItem(
                id=clause_id,
                source=_text(row.subject.get("source_id"))
                if mine
                else (known.source if known else ""),
                title=_text(row.subject.get("title") or clause_id)
                if mine
                else (known.title if known else clause_id),
                quote=_text(row.proposed.get("quote")),
                ref=_text(row.subject.get("ref"))
                if mine
                else (known.ref if known else ""),
                corpus_version=_corpus(row),
                decision=decision_of(row),
                reason=row.decided.reason if row.decided is not None else None,
                item_id=row.item_id,
                gate_id=row.gate_id,
                gate_open=gate_open(row),
                origin="analyst" if mine else "agent",
                added_by=_text(
                    row.subject.get("added_by_name") or row.subject.get("added_by")
                )
                if mine
                else None,
                added_reason=added_reason(row),
            )
        )
    return sorted(items, key=lambda c: c.id)


def policy_view(
    record: CaseRecord,
    rows: Sequence[ReviewItemRow],
    catalogue: Mapping[str, ClauseRef],
    ready: bool,
    recorded_gaps: Sequence[GapRow] = (),
) -> PolicyView:
    """The Policy tab: the clauses research proposed (each with its decision), the
    gap research declared when a domain had no applicable clause (``recorded`` once
    the analyst recorded it as a policy gap), and the pinned corpus version. Empty
    until the assessment has run.
    """
    gaps = [
        r
        for r in current_items(rows)
        if r.kind is ItemKind.GAP and r.subject.get("origin") == "research"
    ]
    recorded = {g.text.strip() for g in recorded_gaps}
    first = gaps[0] if gaps else None
    return PolicyView(
        clauses=clause_items(rows, catalogue) if ready else [],
        gap=PolicyGap(
            text=_text(first.subject.get("description")),
            recorded=_text(first.subject.get("description")).strip() in recorded,
        )
        if first is not None and ready
        else None,
        guard4_note=None,
        corpus_version=f"v{record.pinned_versions.corpus_v}",
    )


# -- risk and controls -------------------------------------------------------------


def _control_item(
    facts: ControlFacts,
    record: CaseRecord,
    share: float,
    targeted: set[str],
    today: date,
) -> ControlItem:
    row = facts.item
    decided = row.decided
    override: Optional[Override] = None
    if row.status in _VALUE_CHANGED and decided is not None:
        override = Override.model_validate(
            {
                "from": facts.proposed_rating
                if facts.proposed_rating is not None
                else 0.0,
                "to": facts.rating if facts.rating is not None else 0.0,
                "by": _display_name(record, decided.by),
                "reason": decided.reason or "",
            }
        )
    rating = facts.rating if facts.rating is not None else 0.0
    return ControlItem(
        id=facts.control_id,
        name=facts.name,
        domain=Domain(facts.domain),
        weight_norm=share,
        rating=rating,
        proposed_rating=facts.proposed_rating
        if facts.proposed_rating is not None
        else rating,
        evidence=facts.evidence,
        evidence_date=facts.evidence_date,
        stale=is_stale(facts.evidence_date, today),
        decision=decision_of(row),
        override=override,
        critic=facts.control_id in targeted,
        item_id=row.item_id,
        gate_id=row.gate_id,
        gate_open=gate_open(row),
    )


def _factor_for(rows: Sequence[ReviewItemRow], domain: str) -> Optional[ReviewItemRow]:
    """The newest current typed factor item of ``domain`` (the seed's two-item
    factors carry no justification and are not returned).
    """
    typed = [
        r
        for r in current_items(rows)
        if r.kind is ItemKind.FACTOR
        and r.subject.get("domain") == domain
        and "likelihood" in r.proposed
    ]
    return max(typed, key=lambda r: (r.created_at, r.item_id), default=None)


def _int(value: float) -> int:
    return int(round(value))


def _area(
    domain: str,
    figures: Mapping[str, float],
    controls: list[ControlItem],
    total_matched: int,
    factor: Optional[ReviewItemRow],
    lock_blocking: bool,
) -> RiskArea:
    return RiskArea(
        domain=Domain(domain),
        label=domain_labels().get(domain, domain),
        L=_int(figures.get("L", 0.0)),
        I=_int(figures.get("I", 0.0)),
        inherent=figures.get("inherent", 0.0),
        ce=figures.get("ce", 0.0),
        retention=figures.get("retention", 0.0),
        residual=figures.get("residual", 0.0),
        weight=figures.get("weight", 0.0),
        contribution=figures.get("contribution", 0.0),
        controls=controls,
        matched_caption=f"{len(controls)} of {total_matched} matched controls shown",
        justification=_text(factor.proposed.get("justification"))
        if factor is not None
        else _LEGACY_FACTOR_TEXT,
        lock_blocking=lock_blocking,
        factor_item_id=factor.item_id if factor is not None else None,
        factor_gate_id=factor.gate_id if factor is not None else None,
        factor_gate_open=gate_open(factor) if factor is not None else False,
    )


@dataclass(frozen=True)
class RiskInputs:
    """Everything the risk view is built from, already read. ``review_started``
    is true by the time the route builds it: opening the view starts review.
    """

    record: CaseRecord
    snapshot: Optional[ScoreSnapshotRow]
    review_started: bool
    items: Sequence[ReviewItemRow]
    critic: Optional[CriticViewRow]
    completeness_present: bool
    catalogue: Mapping[str, ControlRef]
    today: date
    planned_rows: Sequence[PlannedControlRow] = ()


def risk_view(inputs: RiskInputs) -> RiskView:
    """The Risk & Controls tab: the score of record by domain with the controls
    behind each, the planned controls (never scored), the critic's banner and the
    scale anchors. When the score is withheld there are no areas: a figure is
    score content.
    """
    record = inputs.record
    ready = assessment_ready(record, inputs.completeness_present)
    box: ScoreBox = score_box(
        inputs.snapshot,
        review_started=inputs.review_started,
        assessment_ready=ready,
    )
    version = (
        f"v{inputs.snapshot.scoring_v}"
        if inputs.snapshot is not None and inputs.snapshot.scoring_v is not None
        else scoring_model_version()
    )
    scale = (
        [FactorAnchor(value=v, label=t) for v, t in likelihood_scale()],
        [FactorAnchor(value=v, label=t) for v, t in impact_scale()],
    )
    if not isinstance(box, ScoreShown) or inputs.snapshot is None:
        return RiskView(
            areas=[],
            score=box,
            planned=[],
            critic=None,
            band_crossed=None,
            likelihood_scale=scale[0],
            impact_scale=scale[1],
            model_version=version,
        )
    findings = critic_findings(inputs.critic, inputs.items)
    targeted = {f.target for f in findings if f.target is not None}
    open_targets = {f.target for f in findings if f.target is not None and f.gate_open}
    facts = controls_of(inputs.items, inputs.catalogue)
    shares = weight_shares(facts)
    scored = [f for f in facts if not f.planned]
    areas: list[RiskArea] = []
    for domain in sorted(inputs.snapshot.domains):
        in_area = [f for f in scored if f.domain == domain]
        controls = [
            _control_item(
                f, record, shares.get(f.control_id, 0.0), targeted, inputs.today
            )
            for f in in_area
        ]
        areas.append(
            _area(
                domain,
                inputs.snapshot.domains[domain],
                controls,
                len(scored),
                _factor_for(inputs.items, domain),
                any(c.id in open_targets for c in controls),
            )
        )
    planned = [
        PlannedControl(
            id=row.planned_id,
            control_id=row.control_id,
            description=row.description,
            owner=row.owner,
            due=row.due,
        )
        for row in inputs.planned_rows
    ] + [
        PlannedControl(
            id=f.item.item_id,
            control_id=f.control_id,
            description=_text(f.item.proposed.get("rationale")) or f.name,
            owner="Unassigned",
            due="No date set",
            gate_open=gate_open(f.item),
        )
        for f in facts
        if f.planned and f.item.status is not ItemStatus.REMOVED
    ]
    banner = next((f for f in findings if f.target is not None), None)
    return RiskView(
        areas=areas,
        score=box,
        planned=planned,
        critic=banner,
        band_crossed=None,
        likelihood_scale=scale[0],
        impact_scale=scale[1],
        model_version=version,
    )


# -- the handoff -------------------------------------------------------------------


def effective_business_summary(row: Optional[RecommendationRow]) -> str:
    """The business summary the owner will read: the analyst's edit when there is
    one, else the draft's (empty when H1 withheld it for a restricted term).
    """
    if row is None:
        return ""
    if row.edits is not None and row.edits.get("business_summary") is not None:
        return _text(row.edits["business_summary"])
    summary = row.draft.get("business_summary") or {}
    return _text(summary.get("text"))


def provisional_checks(
    record: CaseRecord,
    items: Sequence[ReviewItemRow],
    owed: Sequence[str],
    recommendation: Optional[RecommendationRow],
) -> dict[str, bool]:
    """The four release checks as the stored rows stand, for a case whose pack
    Caseflow has not built yet: the same four facts, read from the same rows, and
    only a preview. Once Caseflow builds the pack its stored checks replace these.
    """
    undecided = [
        r
        for r in current_items(items)
        if r.kind in _GATED and r.status is ItemStatus.PROPOSED
    ]
    return {
        "locked": record.stage_flags.locked,
        "all_items_reviewed": not undecided,
        "boundary_and_band_acknowledged": not owed,
        "business_summary_present": bool(
            effective_business_summary(recommendation).strip()
        ),
    }


_DETAIL: Final = {
    ("locked", True): "The recommendation is locked.",
    ("locked", False): "Lock the recommendation first.",
    ("all_items_reviewed", True): "Every item has a decision.",
    ("all_items_reviewed", False): "Some items are still undecided.",
    ("boundary_and_band_acknowledged", True): "Nothing is waiting to be acknowledged.",
    (
        "boundary_and_band_acknowledged",
        False,
    ): "Acknowledge the boundary flag or band change.",
    ("business_summary_present", True): "The owner's summary is written.",
    ("business_summary_present", False): "The owner's summary is missing.",
}


def release_checks(
    stored: Optional[Mapping[str, bool]], provisional: Mapping[str, bool]
) -> list[ReleaseCheck]:
    """The four checks in the web's order: Caseflow's stored answer when the
    pack was built, else the provisional one (marked in its detail).
    """
    source = stored if stored is not None else provisional
    suffix = "" if stored is not None else " (not yet checked by Caseflow)"
    checks: list[ReleaseCheck] = []
    for key, label in CHECK_LABELS:
        ok = bool(source.get(key, False))
        checks.append(
            ReleaseCheck(
                id=key,  # type: ignore[arg-type]
                label=label,
                ok=ok,
                detail=_DETAIL[(key, ok)] + suffix,
            )
        )
    return checks


def pack_status(record: CaseRecord) -> str:
    """Where the stored pack stands: decided once the case is closed, released
    while the committee holds it, superseded once the recommendation was unlocked
    again, built otherwise.
    """
    if record.state is CaseState.CLOSED:
        return "decided"
    if record.state is CaseState.DECIDE:
        return "released"
    if record.state in (CaseState.RECOMMEND, CaseState.HANDOFF):
        return "built"
    return "superseded"


@dataclass(frozen=True)
class HandoffInputs:
    """Everything the handoff view is built from, already read."""

    record: CaseRecord
    snapshot: Optional[ScoreSnapshotRow]
    review_started: bool
    items: Sequence[ReviewItemRow]
    owed: Sequence[str]
    handoff: Optional[HandoffViewRow]
    recommendation: Optional[RecommendationRow]
    catalogue: Mapping[str, ControlRef]
    released_to: Sequence[str]
    outcome_label: Optional[str]
    outcome_at: Optional[datetime]


def _counts(inputs: HandoffInputs, snapshot: ScoreSnapshotRow) -> PackCounts:
    facts = controls_of(inputs.items, inputs.catalogue)
    current = current_items(inputs.items)
    return PackCounts(
        composite=snapshot.composite,
        band=band_no(snapshot.band),  # type: ignore[arg-type]
        areas=len(snapshot.domains),
        controls=sum(
            1
            for f in facts
            if not f.planned and f.item.status is not ItemStatus.REMOVED
        ),
        overrides=sum(1 for f in facts if f.item.status in _VALUE_CHANGED),
        planned=sum(1 for f in facts if f.planned),
        clauses=sum(
            1
            for r in current
            if r.kind is ItemKind.CLAUSE and r.status is ItemStatus.CONFIRMED
        ),
        gaps=sum(
            1
            for r in current
            if r.kind is ItemKind.GAP and r.subject.get("origin") == "research"
        ),
        rounds=max((inputs.record.current_submission_v or 1) - 1, 0),
    )


def handoff_view(inputs: HandoffInputs) -> HandoffView:
    """The Handoff tab: the pack Caseflow built (id, version, hash, its checks, and
    the status it stands in), the route and band of the score, whether the analyst
    may close it under delegated authority and who the committee is, as stored.
    """
    record = inputs.record
    box = score_box(
        inputs.snapshot,
        review_started=inputs.review_started,
        assessment_ready=True,
    )
    shown = box if isinstance(box, ScoreShown) else None
    stored = inputs.handoff
    pack: Optional[PackSummary] = None
    if stored is not None and inputs.snapshot is not None and shown is not None:
        pack = PackSummary(
            pack_id=stored.pack_id,
            version=stored.pack_version,
            content_hash=stored.content_hash,
            status=pack_status(record),  # type: ignore[arg-type]
            built_at=stored.updated_at.isoformat(),
            counts=_counts(inputs, inputs.snapshot),
        )
    provisional = provisional_checks(
        record, inputs.items, inputs.owed, inputs.recommendation
    )
    band = shown.band if shown is not None else None
    return HandoffView(
        pack=pack,
        release_checks=release_checks(
            stored.checks if stored is not None else None, provisional
        ),
        route=shown.route if shown is not None else None,
        band=band,
        can_close_delegated=band is not None
        and band <= 2
        and record.stage_flags.locked
        and record.state is not CaseState.CLOSED,
        released_to=list(inputs.released_to),
        outcome=OutcomeLine(
            label=inputs.outcome_label, at=inputs.outcome_at.isoformat()
        )
        if inputs.outcome_label is not None and inputs.outcome_at is not None
        else None,
    )


# -- the clarification round -------------------------------------------------------

_THREAD_STATUS: Final = {
    "awaiting_response": "open",
    "answered": "answered",
    "resolved": "resolved",
}


def _thread(row: ClarificationThreadRow) -> ThreadAnalyst:
    answer = row.answer
    return ThreadAnalyst(
        id=row.thread_id,
        round=row.round,
        anchor=row.anchor or "",
        question=row.text,
        status=_THREAD_STATUS[row.status],  # type: ignore[arg-type]
        answer=ThreadAnswerView(
            text=answer.text,
            documents=list(answer.documents),
            at=answer.at.isoformat(),
            by=answer.by,
        )
        if answer is not None
        else None,
    )


def clarifications_view(
    record: CaseRecord,
    completeness: Optional[CompletenessViewRow],
    decided: Optional[str],
    threads: Sequence[ClarificationThreadRow],
) -> ClarificationsView:
    """The Inbox tab: the questions the narrative agent drafted (with the ones it
    dropped and why), the round the analyst sent (each question with the owner's
    answer, in the analyst's terms: the anchor may name a control), whether a round
    can still be sent and whether the analyst's clock is paused on the owner.
    """
    drafted = [
        DraftQuestion(
            id=_text(q.get("question_id")),
            text=_text(q.get("text")),
            anchor=q.get("anchored_to"),
            keep=q.get("dropped_reason") is None,
            dropped_reason=q.get("dropped_reason"),
        )
        for q in (completeness.questions if completeness is not None else [])
    ]
    by_round: dict[int, list[ClarificationThreadRow]] = {}
    for row in threads:
        by_round.setdefault(row.round, []).append(row)
    rounds = [
        ClarificationRound(
            round=number,
            sent_at=min(r.opened_at for r in rows).isoformat(),
            to=record.owner_name or record.owner,
            threads=[_thread(r) for r in sorted(rows, key=lambda r: r.question_id)],
        )
        for number, rows in sorted(by_round.items())
    ]
    return ClarificationsView(
        drafted=drafted,
        rounds=rounds,
        can_send=decided == "ask" and not threads,
        clock_paused=any(r.status == "awaiting_response" for r in threads),
    )
