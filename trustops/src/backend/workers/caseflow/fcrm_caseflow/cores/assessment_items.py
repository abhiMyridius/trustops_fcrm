"""The assessment half's proposals as review items: pure functions, no I/O.

The research and assessment agents each return a list of kept proposals (clauses,
gaps, precedents; factors and controls) that H2 dumps without a type tag. This
module reads them back as their contract models (:func:`parse_research`,
:func:`parse_assessment`), turns each into the :class:`ItemDraft` S1 stores as a
review item (and S4 stores as a ledger entry), and decides, against what S1
already holds, which drafts are new, which supersede an item, and which change
nothing (:func:`plan_items`).

Nothing is generated here. Every quote, source reference, rating, likelihood and
impact is the agent's, copied; the catalogue weight of a control is the one thing
code adds, from S3's inventory, and a control the inventory does not know is left
out (counted, never given an invented weight).

**Identity.** An item's id is ``deterministic_id("itm", case, kind, *key)`` (the
formula S1's own ``item_id_for`` uses), with a revision suffix after a re-run
supersedes it: the same proposal always addresses the same row, so a step that
runs twice writes the same rows, and a decided item is never edited: a re-run
proposes a new item that names it in ``supersedes``.

**What a re-run replaces.** An item is replaced when its inputs changed (the
owner changed a field the item's domain depends on) *and* the agent's new
proposal differs from what S1 holds. Unchanged inputs keep the item, decided or
not; an identical proposal changes nothing, which is also what makes a retried
publish a no-op.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Optional

from fcrm_common.errors import ValidationError as FcrmValidationError
from fcrm_common.ids import deterministic_id
from fcrm_contracts.api.case_internal import ReviewItemInternal, ReviewItemUpsert
from fcrm_contracts.api.case_internal_assessment import (
    ClauseProposed,
    ClauseSubject,
    ControlProposed,
    ControlSubject,
    CriticFindingSubject,
    FactorProposed,
    FactorSubject,
    GapSubject,
)
from fcrm_contracts.api.policy import ControlOut
from fcrm_contracts.enums import AgentName, Domain, ItemKind, ItemStatus
from fcrm_contracts.proposals.assessment import ControlProposal, FactorProposal
from fcrm_contracts.proposals.critic import CriticFinding
from fcrm_contracts.proposals.provenance import ClauseSourceRef, SourceRef
from fcrm_contracts.proposals.research import (
    ClauseProposal,
    JurisdictionNote,
    PolicyGap,
    PrecedentMatch,
)
from pydantic import BaseModel, ValidationError

from fcrm_caseflow.store import AgentResult

logger = logging.getLogger(__name__)

#: Item kinds an analyst decides through a ``review_item`` gate. A research
#: ``gap`` is a recorded absence: shown, never decided.
GATED_KINDS: Final = frozenset(
    {ItemKind.CLAUSE, ItemKind.FACTOR, ItemKind.CONTROL, ItemKind.CRITIC_FINDING}
)

_REVISION: Final = "r{n}"
_DIGEST_CHARS: Final = 12


def item_id_for(case_id: str, kind: ItemKind, *parts: str) -> str:
    """The ``itm_`` id of the item of ``kind`` about ``parts`` on ``case_id``
    (the formula of S1's ``fcrm_case.domain.intake.item_id_for``).
    """
    return deterministic_id("itm", case_id, kind.value, *parts)


def revision_parts(parts: tuple[str, ...], revision: int) -> tuple[str, ...]:
    """``parts`` with the suffix of revision ``revision`` (none for the first)."""
    return parts if revision == 0 else (*parts, _REVISION.format(n=revision))


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:_DIGEST_CHARS]


# ── reading the agents' kept proposals ──────────────────────────────────────────


@dataclass(frozen=True)
class ParsedResearch:
    """What the research agent kept, by shape."""

    clauses: tuple[ClauseProposal, ...] = ()
    gaps: tuple[PolicyGap, ...] = ()
    precedents: tuple[PrecedentMatch, ...] = ()
    jurisdiction_notes: tuple[JurisdictionNote, ...] = ()


@dataclass(frozen=True)
class ParsedAssessment:
    """What the assessment agent kept, by shape."""

    factors: tuple[FactorProposal, ...] = ()
    controls: tuple[ControlProposal, ...] = ()


def _parse_as(
    model: type[BaseModel], item: Mapping[str, Any], result: AgentResult
) -> BaseModel:
    try:
        return model.model_validate(item)
    except ValidationError as exc:
        raise FcrmValidationError(
            f"an accepted {result.agent.value} proposal is not a valid "
            f"{model.__name__}",
            job_id=result.job_id,
            errors_count=exc.error_count(),
        ) from exc


def _research_model(item: Mapping[str, Any]) -> Optional[type[BaseModel]]:
    """Which research shape ``item`` is, from the keys only one has."""
    if "clause_id" in item:
        return ClauseProposal
    if "searched" in item:
        return PolicyGap
    if "jurisdiction" in item:
        return JurisdictionNote
    if "similarity" in item and "outcome" in item:
        return PrecedentMatch
    return None


def _assessment_model(item: Mapping[str, Any]) -> Optional[type[BaseModel]]:
    """Which assessment shape ``item`` is, from the keys only one has."""
    if "likelihood" in item:
        return FactorProposal
    if "control_id" in item:
        return ControlProposal
    return None


def _walk(
    results: Sequence[AgentResult],
    pick: Any,
    jobs: Optional[frozenset[str]],
) -> Iterable[BaseModel]:
    for result in results:
        if jobs is not None and result.job_id not in jobs:
            continue
        for item in result.accepted():
            model = pick(item)
            if model is None:
                raise FcrmValidationError(
                    f"an accepted {result.agent.value} proposal matches no known shape",
                    job_id=result.job_id,
                    keys=sorted(str(k) for k in item),
                )
            yield _parse_as(model, item, result)


def parse_research(
    results: Sequence[AgentResult], jobs: Optional[frozenset[str]] = None
) -> ParsedResearch:
    """The research agent's kept proposals in ``results`` (only those of the
    jobs in ``jobs`` when given), by shape.

    Raises:
        fcrm_common.errors.ValidationError: a kept proposal is not any of the
            four research shapes, or is not valid as the one it looks like.
    """
    clauses: list[ClauseProposal] = []
    gaps: list[PolicyGap] = []
    precedents: list[PrecedentMatch] = []
    notes: list[JurisdictionNote] = []
    for parsed in _walk(results, _research_model, jobs):
        if isinstance(parsed, ClauseProposal):
            clauses.append(parsed)
        elif isinstance(parsed, PolicyGap):
            gaps.append(parsed)
        elif isinstance(parsed, JurisdictionNote):
            notes.append(parsed)
        elif isinstance(parsed, PrecedentMatch):
            precedents.append(parsed)
    return ParsedResearch(tuple(clauses), tuple(gaps), tuple(precedents), tuple(notes))


def parse_assessment(
    results: Sequence[AgentResult], jobs: Optional[frozenset[str]] = None
) -> ParsedAssessment:
    """The assessment agent's kept proposals in ``results`` (only those of the
    jobs in ``jobs`` when given), by shape.

    Raises:
        fcrm_common.errors.ValidationError: a kept proposal is neither a factor
            nor a control, or is not valid as the one it looks like.
    """
    factors: list[FactorProposal] = []
    controls: list[ControlProposal] = []
    for parsed in _walk(results, _assessment_model, jobs):
        if isinstance(parsed, FactorProposal):
            factors.append(parsed)
        elif isinstance(parsed, ControlProposal):
            controls.append(parsed)
    return ParsedAssessment(tuple(factors), tuple(controls))


def parse_critic(results: Sequence[AgentResult]) -> list[CriticFinding]:
    """The critic's kept findings in ``results``, in order.

    Raises:
        fcrm_common.errors.ValidationError: a kept proposal is not a finding.
    """
    return [
        parsed
        for parsed in _walk(
            results,
            lambda item: CriticFinding if "finding_type" in item else None,
            None,
        )
        if isinstance(parsed, CriticFinding)
    ]


# ── drafts ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ItemDraft:
    """One proposal as the item S1 will store and the entry S4 will keep.

    Attributes:
        kind: The item kind.
        parts: What identifies the item among its kind (with the case id and the
            kind they make its id).
        subject: The item's ``subject`` (a typed model's JSON).
        proposed: The item's ``proposed``.
        source_refs: Where the proposal's claim comes from.
        input_fields: The field ids the item depends on.
        agent: Which agent proposed it.
        ledger_kind: The S4 ledger ``kind``.
        ledger_payload: The S4 ledger payload, in the shape the context-pack
            builders read (``clauses``, ``controls``, ``factors``, ``gap``,
            ``findings``).
        domain: The risk domain, for the re-run's scope.
        gated: Whether the analyst decides it through a ``review_item`` gate.
    """

    kind: ItemKind
    parts: tuple[str, ...]
    subject: dict[str, Any]
    proposed: dict[str, Any]
    source_refs: tuple[SourceRef, ...]
    input_fields: tuple[str, ...]
    agent: AgentName
    ledger_kind: str
    ledger_payload: dict[str, Any]
    domain: Optional[Domain] = None
    gated: bool = True


def _best[T](items: Iterable[T], key: Any, confidence: Any) -> dict[Any, T]:
    """The most confident of the items sharing a ``key`` (ties by the key's own
    order of first appearance, so the same results always give the same pick).
    """
    chosen: dict[Any, T] = {}
    for item in items:
        k = key(item)
        known = chosen.get(k)
        if known is None or confidence(item) > confidence(known):
            chosen[k] = item
    return chosen


def clause_drafts(
    proposals: Iterable[ClauseProposal],
    corpus_v: int,
    inputs: Mapping[Domain, Sequence[str]],
) -> list[ItemDraft]:
    """A ``clause`` draft per distinct clause (the most confident proposal when
    the agent named one twice), sorted by clause id.
    """
    best = _best(proposals, lambda p: p.clause_id, lambda p: p.confidence)
    drafts: list[ItemDraft] = []
    for clause_id in sorted(best):
        p = best[clause_id]
        drafts.append(
            ItemDraft(
                kind=ItemKind.CLAUSE,
                parts=(clause_id,),
                subject=ClauseSubject(
                    clause_id=clause_id, domain=p.domain, corpus_v=corpus_v
                ).model_dump(mode="json"),
                proposed=ClauseProposed(
                    quote=p.quote,
                    applicability=p.applicability,
                    confidence=p.confidence,
                ).model_dump(mode="json"),
                source_refs=(
                    p.source_ref
                    if isinstance(p.source_ref, ClauseSourceRef)
                    else ClauseSourceRef(clause_id=clause_id, corpus_v=corpus_v),
                ),
                input_fields=tuple(inputs.get(p.domain, ())),
                agent=AgentName.RESEARCH,
                ledger_kind="research",
                ledger_payload={
                    "summary": f"{clause_id} applies to {p.domain.value}",
                    "clauses": [
                        {
                            "clause_id": clause_id,
                            "quote": p.quote,
                            "domain": p.domain.value,
                            "applicability": p.applicability,
                            "confidence": p.confidence,
                        }
                    ],
                },
                domain=p.domain,
            )
        )
    return drafts


def gap_drafts(
    gaps: Iterable[PolicyGap], inputs: Mapping[Domain, Sequence[str]]
) -> list[ItemDraft]:
    """A ``gap`` draft per declared policy gap (not gated: a recorded absence)."""
    drafts: list[ItemDraft] = []
    for gap in sorted(gaps, key=lambda g: (g.domain.value, g.description)):
        drafts.append(
            ItemDraft(
                kind=ItemKind.GAP,
                parts=(gap.domain.value, _digest(gap.description)),
                subject=GapSubject(
                    domain=gap.domain,
                    description=gap.description,
                    searched_query=gap.searched.query,
                    searched_indexes=list(gap.searched.indexes),
                    searched_hits=gap.searched.hits,
                ).model_dump(mode="json"),
                proposed={},
                source_refs=(),
                input_fields=tuple(inputs.get(gap.domain, ())),
                agent=AgentName.RESEARCH,
                ledger_kind="research",
                ledger_payload={
                    "summary": gap.description,
                    "gap": gap.description,
                    "domain": gap.domain.value,
                    "searched": gap.searched.model_dump(mode="json"),
                },
                domain=gap.domain,
                gated=False,
            )
        )
    return drafts


def factor_drafts(
    proposals: Iterable[FactorProposal], inputs: Mapping[Domain, Sequence[str]]
) -> list[ItemDraft]:
    """A ``factor`` draft per in-scope domain (the most confident proposal when
    the agent named a domain twice), sorted by domain.
    """
    best = _best(proposals, lambda p: p.domain, lambda p: p.confidence)
    drafts: list[ItemDraft] = []
    for domain in sorted(best, key=lambda d: d.value):
        p = best[domain]
        drafts.append(
            ItemDraft(
                kind=ItemKind.FACTOR,
                parts=(domain.value,),
                subject=FactorSubject(domain=domain).model_dump(mode="json"),
                proposed=FactorProposed(
                    likelihood=p.likelihood,
                    impact=p.impact,
                    justification=p.justification,
                    quote=p.quote,
                    confidence=p.confidence,
                ).model_dump(mode="json"),
                source_refs=(p.source_ref,),
                input_fields=tuple(inputs.get(domain, ())),
                agent=AgentName.ASSESSMENT,
                ledger_kind="assessment",
                ledger_payload={
                    "summary": f"{domain.value}: likelihood {p.likelihood}, "
                    f"impact {p.impact}",
                    "factors": {
                        domain.value: {
                            "L": p.likelihood,
                            "I": p.impact,
                            "justification": p.justification,
                            "confidence": p.confidence,
                        }
                    },
                },
                domain=domain,
            )
        )
    return drafts


@dataclass(frozen=True)
class ControlDrafts:
    """The control drafts and the proposals that could not be drafted.

    Attributes:
        drafts: One per distinct ``(control, planned)``.
        unknown: Control ids the catalogue (S3's inventory at the pinned
            version) does not know: left out, never given a weight.
        wrong_domain: Control ids the catalogue puts in another domain than the
            agent named: left out.
    """

    drafts: list[ItemDraft] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)
    wrong_domain: list[str] = field(default_factory=list)


def control_drafts(
    proposals: Iterable[ControlProposal],
    catalogue: Mapping[str, ControlOut],
    controls_v: int,
    inputs: Mapping[Domain, Sequence[str]],
) -> ControlDrafts:
    """A ``control`` draft per distinct ``(control id, planned)``, carrying the
    catalogue weight from ``catalogue``; sorted by control id then planned last.

    The agent proposes a rating (or marks the control planned); the weight and
    the name are the inventory's. A control the inventory does not hold, or holds
    in another domain, is left out and reported (rule: an agent never supplies a
    weight, and a made-up one would be a made-up number in the score).
    """
    best = _best(proposals, lambda p: (p.control_id, p.planned), lambda p: p.confidence)
    result = ControlDrafts()
    for control_id, planned in sorted(best):
        p = best[(control_id, planned)]
        entry = catalogue.get(control_id)
        if entry is None:
            result.unknown.append(control_id)
            continue
        if entry.domain != p.domain.value:
            result.wrong_domain.append(control_id)
            continue
        result.drafts.append(
            ItemDraft(
                kind=ItemKind.CONTROL,
                parts=(control_id, "planned" if planned else "scored"),
                subject=ControlSubject(
                    control_id=control_id,
                    domain=p.domain,
                    planned=planned,
                    name=entry.name,
                    catalogue_weight=entry.catalogue_weight,
                    controls_v=controls_v,
                ).model_dump(mode="json"),
                proposed=ControlProposed(
                    rating=p.rating,
                    rationale=p.rationale,
                    evidence_artifact=p.evidence_artifact,
                    evidence_date=p.evidence_date,
                    quote=p.quote,
                    confidence=p.confidence,
                ).model_dump(mode="json"),
                source_refs=(p.source_ref,),
                input_fields=tuple(inputs.get(p.domain, ())),
                agent=AgentName.ASSESSMENT,
                ledger_kind="assessment",
                ledger_payload={
                    "summary": f"{control_id} "
                    + ("planned, not scored" if planned else f"rated {p.rating}"),
                    "controls": [
                        {
                            "id": control_id,
                            "domain": p.domain.value,
                            "rating": p.rating,
                            "evidence": p.evidence_artifact,
                            "evidence_date": p.evidence_date,
                            "planned": planned,
                            "rationale": p.rationale,
                            "confidence": p.confidence,
                        }
                    ],
                },
                domain=p.domain,
            )
        )
    return result


def critic_drafts(findings: Sequence[CriticFinding], job_id: str) -> list[ItemDraft]:
    """A ``critic_finding`` draft per finding, identified by the job and the
    finding's position (a finding has no subject of its own to key on).
    """
    drafts: list[ItemDraft] = []
    for index, finding in enumerate(findings, start=1):
        drafts.append(
            ItemDraft(
                kind=ItemKind.CRITIC_FINDING,
                parts=(job_id, str(index)),
                subject=CriticFindingSubject(
                    finding_type=finding.finding_type,
                    severity=finding.severity,
                    record_refs=list(finding.record_refs),
                    text=finding.text,
                    confidence=finding.confidence,
                    quote=finding.quote,
                ).model_dump(mode="json"),
                proposed={},
                source_refs=(finding.source_ref,),
                input_fields=(),
                agent=AgentName.CRITIC,
                ledger_kind="critic",
                ledger_payload={
                    "summary": finding.text,
                    "findings": [
                        {
                            "type": finding.finding_type,
                            "severity": finding.severity,
                            "text": finding.text,
                            "records": list(finding.record_refs),
                        }
                    ],
                },
            )
        )
    return drafts


# ── planning against what S1 holds ──────────────────────────────────────────────


@dataclass(frozen=True)
class PlannedItem:
    """One draft with the id it gets.

    Attributes:
        draft: The draft.
        item_id: Its id (with a revision suffix when it supersedes one).
        supersedes: The id of the item it replaces, if any.
        previous_proposal_id: The S4 proposal that item carried, which the new
            ledger entry supersedes.
        existing: The item S1 already holds under this id, when the draft is
            unchanged and nothing is written for it.
    """

    draft: ItemDraft
    item_id: str
    supersedes: Optional[str] = None
    previous_proposal_id: Optional[str] = None
    existing: Optional[ReviewItemInternal] = None

    @property
    def is_new(self) -> bool:
        """Whether this draft becomes a new row (and a new ledger entry)."""
        return self.existing is None


@dataclass(frozen=True)
class ItemPlan:
    """What a publish does about its drafts.

    Attributes:
        items: Every draft, new or unchanged, in input order.
        superseded_ids: Ids of the items this publish replaces.
        kept: How many drafts changed nothing (inputs unchanged, or an
            identical proposal).
    """

    items: tuple[PlannedItem, ...]
    superseded_ids: tuple[str, ...]
    kept: int


def _same_content(draft: ItemDraft, row: ReviewItemInternal) -> bool:
    return row.subject == draft.subject and row.proposed == draft.proposed


def _current(
    case_id: str, draft: ItemDraft, by_id: Mapping[str, ReviewItemInternal]
) -> tuple[Optional[ReviewItemInternal], int]:
    """The newest revision of ``draft`` S1 holds, and its revision number
    (``(None, -1)`` when S1 holds none).
    """
    found: Optional[ReviewItemInternal] = None
    revision = -1
    index = 0
    while True:
        row = by_id.get(
            item_id_for(case_id, draft.kind, *revision_parts(draft.parts, index))
        )
        if row is None:
            return found, revision
        found, revision = row, index
        index += 1


def plan_items(
    case_id: str,
    drafts: Sequence[ItemDraft],
    existing: Sequence[ReviewItemInternal],
    *,
    changed_fields: frozenset[str],
    full: bool,
) -> ItemPlan:
    """Decide, for each draft, whether it is new, replaces an item, or changes
    nothing, against the items S1 holds (module docstring: "What a re-run
    replaces").

    Args:
        case_id: The case.
        drafts: What the agents proposed now.
        existing: Every item of the case S1 holds, superseded ones included.
        changed_fields: The field ids the owner changed.
        full: A full re-run: every item's inputs count as changed.
    """
    by_id = {row.item_id: row for row in existing}
    planned: list[PlannedItem] = []
    superseded: list[str] = []
    kept = 0
    for draft in drafts:
        row, revision = _current(case_id, draft, by_id)
        if row is None:
            planned.append(
                PlannedItem(draft, item_id_for(case_id, draft.kind, *draft.parts))
            )
            continue
        inputs_changed = full or bool(
            changed_fields & (set(row.input_fields) | set(draft.input_fields))
        )
        if _same_content(draft, row) or not inputs_changed:
            kept += 1
            planned.append(PlannedItem(draft, row.item_id, existing=row))
            continue
        new_id = item_id_for(
            case_id, draft.kind, *revision_parts(draft.parts, revision + 1)
        )
        superseded.append(row.item_id)
        planned.append(
            PlannedItem(
                draft,
                new_id,
                supersedes=row.item_id,
                previous_proposal_id=row.proposal_id,
            )
        )
    return ItemPlan(tuple(planned), tuple(superseded), kept)


def superseded_item_ids(existing: Iterable[ReviewItemInternal]) -> list[str]:
    """Ids of the gated items some later item names in ``supersedes``.

    Their gates must not stay open. A retried publish finds its own earlier
    writes here, and closing a gate twice changes nothing.
    """
    rows = list(existing)
    replaced = {row.supersedes for row in rows if row.supersedes is not None}
    return sorted(
        row.item_id
        for row in rows
        if row.item_id in replaced and row.kind in GATED_KINDS
    )


def upsert_for(
    planned: PlannedItem,
    *,
    gate_id: Optional[str],
    proposal_id: Optional[str],
    case_id: str,
) -> ReviewItemUpsert:
    """The ``PUT items`` row for a new planned item.

    Raises:
        ValueError: ``planned`` is not new (nothing is written for it).
    """
    if not planned.is_new:
        raise ValueError("an unchanged item is never written")
    draft = planned.draft
    return ReviewItemUpsert(
        item_id=planned.item_id,
        kind=draft.kind,
        proposal_id=proposal_id,
        gate_id=gate_id,
        subject=dict(draft.subject),
        proposed=dict(draft.proposed),
        source_refs=list(draft.source_refs),
        input_fields=list(draft.input_fields),
        supersedes=planned.supersedes,
        status=ItemStatus.PROPOSED,
    )
