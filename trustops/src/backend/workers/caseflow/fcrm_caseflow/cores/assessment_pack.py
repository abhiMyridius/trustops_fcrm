"""H1's assessment core, last half: the narrative, the lock and the pack, the
release, and the unlock (scenes 15-16, and the post-lock change of the fail flows).

* ``narrative.publish``: the narrative agent's recommendation draft, scanned for
  the words the PO may never read, pushed beside whatever the analyst edited.
* ``lock.build_pack`` (``case.locked``): freeze the locked numbers into a pack.
  **No model is involved.** The numbers are S2's answer to the case as it stands at
  the lock; the wording is the analyst's edited text, or the agent's draft; the four
  release checks are computed here, in code, from S1's items and H1's own gates.
* ``pack.approved`` (``pack.released``): the committee has the pack; R1/R2 pick the
  route from the band.
* ``unlock.supersede`` (``case.unlocked``): the analyst reopened the numbers with a
  reason; the pack is superseded (votes on it voided, kept) and every item gets a
  new gate, because Gate A is per item and a decided gate cannot be decided again.

**The pack is deterministic in what it is built from.** The audit head recorded in
it is the ``recommendation.locked`` record itself (an idempotent append returns the
same record on a retry), and "stale" is computed as of that record's date, so a
step that runs twice builds the same snapshot and S5 hands back the same pack
instead of minting version 2.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Final, Optional

from fcrm_contracts.api.case_internal import (
    CaseRecordInternal,
    ItemsUpsert,
    ReviewItemInternal,
    ReviewItemUpsert,
)
from fcrm_contracts.api.case_internal_assessment import (
    HandoffPush,
    NarrativeSectionItem,
    RecommendationDraftPush,
    RecommendationInternal,
    ScorePush,
)
from fcrm_contracts.api.scoring import ScoreRequest, ScoreResponse, ScoringModelVersion
from fcrm_contracts.enums import (
    AgentName,
    GateStatus,
    GateType,
    ItemKind,
    ItemStatus,
    Outcome,
    StepKind,
)
from fcrm_contracts.events.payloads import (
    CaseLocked,
    CaseUnlocked,
    HarnessStep,
    PackReleased,
)
from fcrm_contracts.proposals.narrative import BusinessSummary, Condition, Narrative
from fcrm_guardrails.rules.r3_po_no_restricted import restricted_terms_in
from fcrm_guardrails.thresholds import STALE_EVIDENCE_MONTHS

from fcrm_caseflow import routing
from fcrm_caseflow.cores.assessment import review_gate_id
from fcrm_caseflow.cores.assessment_items import GATED_KINDS
from fcrm_caseflow.cores.assessment_score import score_now
from fcrm_caseflow.cores.intake_records import plural, short
from fcrm_caseflow.cores.refs import (
    PACK_REF,
    ROUTE_REF,
    SCORE_SUMMARY_REF,
)
from fcrm_caseflow.cores.shared import (
    idem,
    post_proposal,
    result_for,
    show,
    state_patch,
    step_payload,
    tokens_of,
)
from fcrm_caseflow.dispatch import pack_release_gate_key
from fcrm_caseflow.gates import deterministic_gate_id
from fcrm_caseflow.steps import (
    GateCloseSpec,
    GateSpec,
    StepDeps,
    StepHandler,
    StepResult,
)
from fcrm_caseflow.store import AgentResult

logger = logging.getLogger(__name__)

#: The risk domains' names as the pack shows them (the seed's ``domains``; a lock
#: test holds this to the seed).
DOMAIN_LABELS: Final[Mapping[str, str]] = {
    "customer": "Customer and entity",
    "product": "Product and service",
    "geography": "Geography",
    "channel": "Delivery channel and third party",
}

#: How each route reads in the pack.
ROUTE_LABELS: Final[Mapping[str, str]] = {
    "analyst": "Analyst, delegated authority",
    "committee": "Risk Committee",
}

#: S5's release checks as the pack snapshot names them (the web's keys, mirrored
#: by ``fcrm_contracts.api.decision.ReleaseChecks``): the snapshot's ``checks``
#: is read by S5 and nothing else decides whether a pack may be released.
CHECK_NUMBERS_LOCKED: Final = "locked"
CHECK_BUSINESS_SUMMARY: Final = "business_summary_present"
CHECK_ITEMS_DECIDED: Final = "all_items_reviewed"
CHECK_ACKNOWLEDGED: Final = "boundary_and_band_acknowledged"

_RECOMMENDATION_KEY: Final = "recommendation"


# ── narrative (scene 15) ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class NarrativeParts:
    """What a narrative job kept in recommendation mode."""

    sections: tuple[Narrative, ...]
    conditions: tuple[Condition, ...]
    summary: Optional[BusinessSummary]


def narrative_parts(result: AgentResult) -> NarrativeParts:
    """The recommendation, conditions and business summary in a narrative result,
    by the keys only each has.
    """
    sections: list[Narrative] = []
    conditions: list[Condition] = []
    summary: Optional[BusinessSummary] = None
    for item in result.accepted():
        if "condition_id" in item:
            conditions.append(Condition.model_validate(item))
        elif "restricted_term_hits" in item:
            summary = BusinessSummary.model_validate(item)
        elif "source_ref" in item and "text" in item:
            sections.append(Narrative.model_validate(item))
    return NarrativeParts(tuple(sections), tuple(conditions), summary)


def section_key(index: int) -> str:
    """The key of the ``index``-th narrative section (the first is the
    recommendation itself).
    """
    return _RECOMMENDATION_KEY if index == 0 else f"section_{index}"


def scanned_summary(summary: Optional[BusinessSummary]) -> tuple[BusinessSummary, int]:
    """The PO-facing summary after H1's own restricted-term scan, and the number
    of hits. Guard 4 already refused such a summary; this is the second line: a
    summary with a hit is withheld (its text emptied, the hit count kept) so the
    analyst writes their own, never published with a score in it.
    """
    if summary is None:
        return BusinessSummary(text=""), 0
    hits = restricted_terms_in(summary.text)
    if hits:
        return BusinessSummary(text="", restricted_term_hits=len(hits)), len(hits)
    return BusinessSummary(text=summary.text, restricted_term_hits=0), 0


async def narrative_publish(step: HarnessStep, deps: StepDeps) -> StepResult:
    """``agent.done narrative`` (recommendation mode): keep the draft, push it to
    the analyst beside their edits, record it and the scan of the PO summary.
    """
    results = await deps.require_agent_results().list_for(
        step.run_id, AgentName.NARRATIVE
    )
    result = result_for(results, step, "narrative")
    parts = narrative_parts(result)
    summary, hits = scanned_summary(parts.summary)
    text = "\n\n".join(s.text for s in parts.sections)
    entry = await post_proposal(
        deps,
        step,
        result,
        kind="narrative",
        payload={
            "summary": f"recommendation, {plural(len(parts.conditions), 'condition')}",
            "recommendation": text,
            "conditions": [c.model_dump(mode="json") for c in parts.conditions],
        },
        key="narrative",
    )
    score = deps.run.refs.get(SCORE_SUMMARY_REF) or {}
    seq = score.get("score_record_seq")
    await deps.require_case().put_recommendation_draft(
        step.case_id,
        RecommendationDraftPush(
            run_id=step.run_id,
            sections=[
                NarrativeSectionItem(
                    key=section_key(index),
                    text=section.text,
                    source_ref=section.source_ref,
                    quote=section.quote,
                    confidence=section.confidence,
                )
                for index, section in enumerate(parts.sections)
            ],
            conditions=list(parts.conditions),
            business_summary=summary,
            score_record_seq=seq if isinstance(seq, int) else None,
            proposal_ids=[entry.proposal_id],
        ),
        idempotency_key=idem(step, "recommendation-draft"),
    )
    audit = await deps.append_record(
        step,
        "proposal.narrative",
        "Narrative agent · recommendation, "
        f"{plural(len(parts.conditions), 'condition')}, business summary",
        {"conditions": len(parts.conditions), "proposal_id": entry.proposal_id},
        actor="ai",
    )
    await deps.append_record(
        step,
        "guard.output",
        f"PO-summary scan · {plural(hits, 'restricted term')}"
        + (" · summary withheld" if hits else ""),
        {"restricted_terms": hits, "withheld": bool(hits)},
        actor="system",
    )
    await show(
        deps,
        step,
        "recommendation.draft_ready",
        "analyst",
        "The recommendation draft is ready to edit and lock",
        {"conditions": len(parts.conditions), "summary_withheld": bool(hits)},
        record_seq=audit.seq,
    )
    return StepResult(counter_deltas={"tokens_used": tokens_of([result])})


# ── the pack (scene 15-16) ──────────────────────────────────────────────────────


def _months_between(earlier: date, later: date) -> int:
    return (later.year - earlier.year) * 12 + later.month - earlier.month


def _is_stale(evidence_date: str, as_of: date) -> bool:
    if not evidence_date:
        return False
    return _months_between(date.fromisoformat(evidence_date), as_of) > (
        STALE_EVIDENCE_MONTHS
    )


def effective_sections(rec: RecommendationInternal) -> list[tuple[str, str]]:
    """The recommendation's sections as the pack carries them: each of the
    draft's, with the analyst's edit of that key in its place; an edited key the
    draft lacks follows.
    """
    edited = {s.key: s.text for s in (rec.edits.sections if rec.edits else [])}
    sections = [(s.key, edited.pop(s.key, s.text)) for s in rec.draft.sections]
    sections.extend(edited.items())
    return sections


def effective_conditions(rec: RecommendationInternal) -> list[Condition]:
    """The conditions of the pack: the analyst's list when they edited it, else
    the agent's.
    """
    if rec.edits is not None and rec.edits.conditions is not None:
        return list(rec.edits.conditions)
    return list(rec.draft.conditions)


def effective_summary(rec: RecommendationInternal) -> str:
    """The PO-facing business summary: the analyst's when they wrote one, else
    the agent's (empty if H1 withheld it).
    """
    if rec.edits is not None and rec.edits.business_summary is not None:
        return rec.edits.business_summary
    return rec.draft.business_summary.text


def release_checks(
    *,
    locked: bool,
    recommendation: RecommendationInternal,
    items: Sequence[ReviewItemInternal],
    acks_open: int,
) -> dict[str, bool]:
    """The four release checks, computed in code (a pack is releasable only when
    every one is true; S5 evaluates the same four from this dict).

    * ``locked``: the recommendation is locked (the run's flag).
    * ``business_summary_present``: the narrative produced the plain business
      summary the committee and the PO read.
    * ``all_items_reviewed``: no current gated item is still ``proposed``.
    * ``boundary_and_band_acknowledged``: no acknowledgement gate is open.
    """
    replaced = {row.supersedes for row in items if row.supersedes is not None}
    undecided = [
        row
        for row in items
        if row.kind in GATED_KINDS
        and row.item_id not in replaced
        and row.status is ItemStatus.PROPOSED
    ]
    return {
        CHECK_NUMBERS_LOCKED: locked,
        CHECK_BUSINESS_SUMMARY: bool(effective_summary(recommendation).strip()),
        CHECK_ITEMS_DECIDED: not undecided,
        CHECK_ACKNOWLEDGED: acks_open == 0,
    }


@dataclass(frozen=True)
class PackInputs:
    """Everything the snapshot is built from.

    Attributes:
        record: S1's record of the case.
        items: Every review item of the case.
        recommendation: The draft, the edits and the lock.
        request: The S2 request the locked numbers come from.
        response: S2's answer to it.
        model: The scoring model the case is pinned to (band labels).
        view_overrides: Replaced values and why (S1's score-request).
        acknowledged_by: Who last acknowledged a boundary flag or band change.
        head_seq: ``seq`` of the audit record the pack is built against.
        head_hash: Its hash.
        as_of: The date "stale" is judged on (the lock's).
        checks: The four release checks.
    """

    record: CaseRecordInternal
    items: Sequence[ReviewItemInternal]
    recommendation: RecommendationInternal
    request: ScoreRequest
    response: ScoreResponse
    model: ScoringModelVersion
    view_overrides: Sequence[Any]
    acknowledged_by: Optional[str]
    head_seq: int
    head_hash: str
    as_of: date
    checks: Mapping[str, bool]


def _area(request: ScoreRequest, response: ScoreResponse) -> list[dict[str, Any]]:
    """One area per scored domain: L and I from the request, S2's figures from
    the response, in the response's order.
    """
    inputs = {d.domain: d for d in request.domains}
    return [
        {
            "domain": result.domain.value,
            "label": DOMAIN_LABELS.get(result.domain.value, result.domain.value),
            "L": inputs[result.domain].likelihood,
            "I": inputs[result.domain].impact,
            "inherent": result.inherent,
            "ce": result.control_effectiveness,
            "retention": result.retention,
            "residual": result.residual,
            "weight": result.domain_weight_normalised,
            "contribution": result.contribution,
        }
        for result in response.domains
    ]


def _controls(inputs: PackInputs) -> list[dict[str, Any]]:
    """Every scored control at its effective rating, with its normalised weight
    inside its domain and whether its evidence is stale as of the lock.
    """
    replaced = {r.supersedes for r in inputs.items if r.supersedes is not None}
    held = {
        str(r.subject.get("control_id")): r
        for r in inputs.items
        if r.kind is ItemKind.CONTROL
        and r.item_id not in replaced
        and not r.subject.get("planned")
    }
    entries: list[dict[str, Any]] = []
    for domain in inputs.request.domains:
        total = sum(c.catalogue_weight for c in domain.controls) or 1.0
        for control in domain.controls:
            row = held.get(control.control_id)
            proposed = row.proposed if row is not None else {}
            when = str(proposed.get("evidence_date") or "")
            entries.append(
                {
                    "id": control.control_id,
                    "name": (row.subject.get("name") if row else None)
                    or control.control_id,
                    "domain": domain.domain.value,
                    "rating": control.rating,
                    "weight_norm": round(control.catalogue_weight / total, 2),
                    "evidence": proposed.get("evidence_artifact", ""),
                    "stale": _is_stale(when, inputs.as_of),
                }
            )
    return entries


def _boundary_text(response: ScoreResponse, acknowledged_by: Optional[str]) -> str:
    if not response.boundary_flag:
        return "No boundary flag."
    who = f" · acknowledged by {acknowledged_by}" if acknowledged_by else ""
    return f"{response.boundary_distance} from a band edge{who}"


def _band_label(model: ScoringModelVersion, response: ScoreResponse) -> str:
    return next(
        (b.label for b in model.bands if b.band is response.band), response.band.value
    )


def _display_version(value: object) -> str:
    """A pinned version as the pack shows it: ``v3``, ``v14``, ``v2026-06``."""
    text = str(value)
    return text if text.startswith("v") else f"v{text}"


def _display_pins(pins: Mapping[str, Any]) -> dict[str, str]:
    """The run's integer pins as S5's display strings (``PinnedVersions``)."""
    return {
        "scoring": _display_version(pins["scoring_v"]),
        "corpus": _display_version(pins["corpus_v"]),
        "controls": _display_version(pins["controls_v"]),
        "field_set": _display_version(pins["fieldset_v"]),
        "jurisdictions": _display_version(pins["jurisdictions_v"]),
        "routing": _display_version(pins["routing_v"]),
        "prompts": str(pins["prompts_v"]),
    }


def _record_seqs(refs: Sequence[object]) -> list[int]:
    """Audit record references (``#30`` or ``30``) as the sequence numbers the
    pack lists; a reference that is not a number is dropped, never invented.
    """
    seqs: list[int] = []
    for ref in refs:
        digits = str(ref).lstrip("#").strip()
        if digits.isdigit():
            seqs.append(int(digits))
        else:
            logger.warning("dropped a record reference that is not a number")
    return seqs


def build_pack_snapshot(inputs: PackInputs) -> dict[str, Any]:
    """The frozen content of the pack: ``PackView``'s body (design C §1.4, the
    web's ``PackView`` less the fields S5 adds: id, version, hash, status, the
    addenda) plus ``checks``, the four release checks S5 reads.

    Pure and deterministic: nothing here reads a clock or a random id, so the
    same inputs always give the same snapshot, and S5's content hash is stable.
    """
    record, response, rec = inputs.record, inputs.response, inputs.recommendation
    replaced = {r.supersedes for r in inputs.items if r.supersedes is not None}
    current = [r for r in inputs.items if r.item_id not in replaced]
    conditions = effective_conditions(rec)
    sections = effective_sections(rec)
    explicit = rec.edits.outcome if rec.edits is not None else None
    outcome = explicit or (
        Outcome.APPROVE_WITH_CONDITIONS if conditions else Outcome.APPROVE
    )
    controls_overridden = [o for o in inputs.view_overrides if o.kind == "control"]
    return {
        "case_id": record.case_id,
        "title": record.title,
        "unit": record.unit or "",
        "owner_name": record.owner.name,
        "analyst_name": record.analyst.name if record.analyst else "",
        "score": {
            "composite": response.composite,
            "band": int(response.band.value),
            "band_label": _band_label(inputs.model, response),
            "route_label": ROUTE_LABELS.get(response.route.value, response.route.value),
            "areas": _area(inputs.request, response),
        },
        "boundary": {
            "flag": response.boundary_flag,
            "distance": response.boundary_distance,
            "acknowledged_by": inputs.acknowledged_by,
            "text": _boundary_text(response, inputs.acknowledged_by),
        },
        "recommendation": {
            "outcome": outcome.value,
            "text": "\n\n".join(text for _, text in sections),
            "edits_by_analyst": rec.edits.edit_count if rec.edits else 0,
        },
        "business_summary": effective_summary(rec),
        "conditions": [
            {
                "id": c.condition_id,
                "text": c.text,
                "owner": c.owner,
                "due": c.due,
                "status": c.status,
                "from": c.source,
            }
            for c in conditions
        ],
        "overrides": [
            {
                "item": o.subject_id,
                "from": o.proposed.get("rating"),
                "to": o.decided.get("rating"),
                "by": o.by,
                "reason": o.reason or "",
                "record_seq": None,
            }
            for o in controls_overridden
        ],
        "removals": [
            {
                "item": str(r.subject.get("clause_id") or r.subject.get("control_id")),
                "reason": r.decided.reason or "",
                "by": r.decided.by,
            }
            for r in current
            if r.status is ItemStatus.REMOVED and r.decided is not None
        ],
        "clauses": [
            {
                "id": str(r.subject["clause_id"]),
                "title": str(r.subject["clause_id"]),
                "quote": str(r.proposed.get("quote", "")),
                "ref": f"corpus v{r.subject.get('corpus_v')}",
            }
            for r in current
            if r.kind is ItemKind.CLAUSE and r.status is not ItemStatus.REMOVED
        ],
        "gaps": [
            str(r.subject["description"])
            for r in current
            if r.kind is ItemKind.GAP and r.subject.get("origin") == "research"
        ],
        "controls": _controls(inputs),
        "planned": [
            {
                "description": str(r.proposed.get("rationale", "")),
                "owner": "",
                "due": "",
            }
            for r in current
            if r.kind is ItemKind.CONTROL and r.subject.get("planned")
        ],
        "critic_findings": [
            {
                "id": r.item_id,
                "type": r.subject.get("finding_type"),
                "severity": r.subject.get("severity"),
                "text": r.subject.get("text"),
                "records": _record_seqs(r.subject.get("record_refs", [])),
            }
            for r in current
            if r.kind is ItemKind.CRITIC_FINDING
        ],
        "clarification_rounds": [],
        "pins": _display_pins(record.pins.model_dump(mode="json")),
        "audit_head": {"seq": inputs.head_seq, "hash": inputs.head_hash},
        "checks": dict(inputs.checks),
        "change_types": [str(t) for t in record.change_types],
    }


async def lock_build_pack(step: HarnessStep, deps: StepDeps) -> StepResult:
    """``case.locked``: record the lock, freeze the numbers into a pack and run the
    four release checks. No model is involved.

    The numbers are S2's answer to the case as it stands now; if they differ from
    the score of record the run last saw (a decision that was never re-scored: a
    fault, logged) the fresh answer is pushed as the score of record too, so S1,
    the pack and the chain agree. S5 returns the pack it already built for the
    same snapshot, so a retried step builds no second version.
    """
    locked = step_payload(step, CaseLocked)
    case = deps.require_case()
    record = await case.get_record(step.case_id)
    recommendation = await case.get_recommendation(step.case_id)
    analyst = record.analyst.name if record.analyst else locked.actor
    edits = recommendation.edits.edit_count if recommendation.edits else 0
    lock = await deps.append_record(
        step,
        "recommendation.locked",
        f"Locked by {analyst} · {plural(edits, 'edit')}",
        {"edits": edits, "pack_version": deps.run.snapshot.flags.pack_version},
        actor=locked.actor,
    )
    items = await case.list_items(step.case_id)
    view, response = await score_now(step, deps, items)
    await _reconcile_score(step, deps, view.request, response)
    model = await deps.require_scoring().model(response.scoring_v)
    acks_open = await deps.require_gates().count_open(
        step.case_id, GateType.ACK, run_id=step.run_id
    )
    acknowledged_by = await deps.require_gates().last_decider(
        step.case_id, GateType.ACK, run_id=step.run_id
    )
    checks = release_checks(
        locked=deps.run.snapshot.flags.locked,
        recommendation=recommendation,
        items=items,
        acks_open=acks_open,
    )
    snapshot = build_pack_snapshot(
        PackInputs(
            record=record,
            items=items,
            recommendation=recommendation,
            request=view.request,
            response=response,
            model=model,
            view_overrides=view.overrides,
            acknowledged_by=acknowledged_by,
            head_seq=lock.seq,
            head_hash=lock.hash,
            as_of=lock.created_at.date(),
            checks=checks,
        )
    )
    pack = await deps.require_decision().build_pack(
        step.case_id, snapshot, idempotency_key=idem(step, "pack")
    )
    await deps.append_record(
        step,
        "pack.assembled",
        f"Pack v{pack.version} from locked numbers · no model involved",
        {"pack_id": pack.pack_id, "version": pack.version, "hash": pack.content_hash},
        actor="system",
    )
    verdict = dict(checks)
    passed = sum(1 for ok in verdict.values() if ok)
    await deps.append_record(
        step,
        "gate.validation",
        f"{passed} of {len(verdict)} release checks passed",
        {"checks": verdict},
        actor="system",
    )
    release_gate = deterministic_gate_id(
        step.run_id,
        GateType.PACK_RELEASE,
        pack_release_gate_key(deps.run.snapshot.flags.pack_version),
    )
    await case.put_handoff(
        step.case_id,
        HandoffPush(
            run_id=step.run_id,
            pack_id=pack.pack_id,
            pack_version=pack.version,
            content_hash=pack.content_hash,
            checks=verdict,
            audit_head_seq=lock.seq,
            audit_head_hash=lock.hash,
            gate_id=release_gate,
        ),
        idempotency_key=idem(step, "handoff"),
    )
    await case.patch_state(step.case_id, state_patch(deps.run))
    await show(
        deps,
        step,
        "pack.assembled",
        "analyst",
        f"Pack v{pack.version} assembled: {passed} of {len(verdict)} release "
        "checks passed",
        {"pack_id": pack.pack_id, "checks": verdict},
        record_seq=lock.seq,
    )
    return StepResult(
        refs_patch={
            PACK_REF: {
                "pack_id": pack.pack_id,
                "pack_version": pack.version,
                "pack_hash": pack.content_hash,
                "lock_record_seq": lock.seq,
                "gate_version": deps.run.snapshot.flags.pack_version,
            }
        }
    )


async def _reconcile_score(
    step: HarnessStep, deps: StepDeps, request: ScoreRequest, response: ScoreResponse
) -> None:
    """Make the score of record match what the pack will freeze.

    Every decision that changes a rating is re-scored by ``review.item``, so the
    run's ``score_summary`` already holds this composite and band. If it does not,
    a decision slipped through without a re-score: log it (an error, not a
    recoverable state) and store the fresh answer as the score of record.
    """
    summary = deps.run.refs.get(SCORE_SUMMARY_REF) or {}
    if summary.get("composite") == response.composite and summary.get("band") == int(
        response.band.value
    ):
        return
    logger.error(
        "the locked numbers differ from the score of record; storing the fresh score",
        extra={
            "case_id": step.case_id,
            "run_id": step.run_id,
            "step_id": step.step_id,
            "recorded": summary.get("composite"),
            "fresh": response.composite,
        },
    )
    await deps.require_case().put_score(
        step.case_id,
        ScorePush(
            run_id=step.run_id,
            purpose="confirmed",
            request=request,
            score=response,
            boundary=response.boundary_flag,
        ),
        idempotency_key=idem(step, "score", "locked"),
    )


# ── release (scene 16) ──────────────────────────────────────────────────────────


async def pack_approved(step: HarnessStep, deps: StepDeps) -> StepResult:
    """``pack.released`` (S5; the guard: the release checks passed): the pack went
    to the committee. Record it, close the ``pack_release`` gate (its job is
    done), tell S1 the case is in the decision stage and keep the route R1/R2
    chose from the band.
    """
    released = step_payload(step, PackReleased)
    band = deps.run.band
    route = routing.route_for_band(band).value if band is not None else None
    audit = await deps.append_record(
        step,
        "pack.approved",
        f"Released to {plural(len(released.released_to), 'committee member')} · "
        "votes hidden until all cast",
        {
            "pack_id": released.pack_id,
            "version": released.version,
            "released_to": list(released.released_to),
            "route": route,
        },
        actor="system",
    )
    await deps.require_case().patch_state(step.case_id, state_patch(deps.run))
    await show(
        deps,
        step,
        "pack.approved",
        "analyst",
        "The pack was released"
        + (f" ({ROUTE_LABELS.get(route, route)})" if route else ""),
        {"pack_id": released.pack_id, "route": route},
        record_seq=audit.seq,
    )
    return StepResult(
        gates_to_close=(
            GateCloseSpec(
                GateType.PACK_RELEASE,
                pack_release_gate_key(deps.run.snapshot.flags.pack_version),
                "released",
                GateStatus.DECIDED,
                f"pack v{released.version} released to the committee",
            ),
        ),
        refs_patch={ROUTE_REF: route} if route else {},
    )


# ── unlock (the fail flows; R6) ─────────────────────────────────────────────────


def reopen_gates(
    step: HarnessStep, items: Sequence[ReviewItemInternal]
) -> tuple[tuple[GateSpec, ...], list[ReviewItemUpsert]]:
    """A new ``review_item`` gate for every current gated item, and the item
    rewrites that point each at it.

    Unlocking reopens the numbers, but a decided gate cannot be decided again
    (Guard 7), and Gate A is per item, never bulk (rule 2): each item the analyst
    may change needs a gate of its own. The run stays in review until each is
    decided again. The id carries the step id, so a retry opens the same gates.
    """
    replaced = {row.supersedes for row in items if row.supersedes is not None}
    gates: list[GateSpec] = []
    rewrites: list[ReviewItemUpsert] = []
    for row in items:
        if row.kind not in GATED_KINDS or row.item_id in replaced:
            continue
        key = f"{row.item_id}@{step.step_id}"
        gates.append(
            GateSpec(
                GateType.REVIEW_ITEM,
                key,
                {"kind": row.kind.value, "id": row.item_id},
            )
        )
        rewrites.append(
            ReviewItemUpsert(
                item_id=row.item_id,
                kind=row.kind,
                proposal_id=row.proposal_id,
                gate_id=review_gate_id(step.run_id, key),
                subject=dict(row.subject),
                proposed=dict(row.proposed),
                input_fields=list(row.input_fields),
                supersedes=row.supersedes,
                status=row.status,
            )
        )
    return tuple(gates), rewrites


async def unlock_supersede(step: HarnessStep, deps: StepDeps) -> StepResult:
    """``case.unlocked`` (the guard: a reason is present): record the unlock,
    supersede the pack if one was built (S5 voids the votes cast on it and keeps
    them), expire its release gate and give every item a new gate so the analyst
    can decide again.
    """
    unlocked = step_payload(step, CaseUnlocked)
    case = deps.require_case()
    raw_pack = deps.run.refs.get(PACK_REF)
    pack: Mapping[str, Any] = raw_pack if isinstance(raw_pack, Mapping) else {}
    pack_id = pack.get("pack_id")
    unlock = await deps.append_record(
        step,
        "case.unlocked",
        f"Unlocked by {unlocked.actor} · {short(unlocked.unlock_reason)}",
        {"reason": unlocked.unlock_reason},
        actor=unlocked.actor,
    )
    closes: tuple[GateCloseSpec, ...] = ()
    if pack_id:
        await deps.require_decision().supersede(
            str(pack_id),
            unlocked.unlock_reason,
            idempotency_key=idem(step, "supersede"),
        )
        await deps.append_record(
            step,
            "pack.superseded",
            f"Pack v{pack.get('pack_version')} superseded · votes cast on it are "
            "voided and kept",
            {"pack_id": pack_id, "reason": unlocked.unlock_reason},
            actor="system",
        )
        closes = (
            GateCloseSpec(
                GateType.PACK_RELEASE,
                pack_release_gate_key(int(pack.get("gate_version") or 0)),
                "superseded",
                GateStatus.EXPIRED,
                unlocked.unlock_reason,
            ),
        )
    items = await case.list_items(step.case_id)
    gates, rewrites = reopen_gates(step, items)
    if rewrites:
        await case.put_items(
            step.case_id, ItemsUpsert(run_id=step.run_id, items=rewrites)
        )
    await case.patch_state(step.case_id, state_patch(deps.run))
    await show(
        deps,
        step,
        "case.unlocked",
        "analyst",
        "The recommendation was unlocked: decide the items again before you lock",
        {"reason": unlocked.unlock_reason},
        record_seq=unlock.seq,
    )
    return StepResult(
        gates_to_open=gates,
        gates_to_close=closes,
        refs_patch={PACK_REF: None},
    )


#: The pack half's handlers by step kind.
HANDLERS: Final[Mapping[StepKind, StepHandler]] = {
    StepKind.NARRATIVE_PUBLISH: narrative_publish,
    StepKind.LOCK_BUILD_PACK: lock_build_pack,
    StepKind.PACK_APPROVED: pack_approved,
    StepKind.UNLOCK_SUPERSEDE: unlock_supersede,
}

__all__ = [
    "HANDLERS",
    "PackInputs",
    "build_pack_snapshot",
    "narrative_publish",
    "lock_build_pack",
    "pack_approved",
    "release_checks",
    "reopen_gates",
    "unlock_supersede",
]
