"""H1's assessment core, first half: scenes 8-9 (screening, research and
assessment, the score of record, the critic, the completeness report) and the
clarification round's opening (ADR-28).

Each handler is an ``async`` function ``(HarnessStep, StepDeps) -> StepResult``
(:mod:`fcrm_caseflow.steps`): it reads what the agents kept
(:class:`~fcrm_caseflow.store.AgentResultStore`) and what S1 and S3 hold, writes
the analyst's views through S1, the evidence ledger through S4 and the walkthrough's
audit records, and *describes* what should follow (gates to open, refs to keep) in
its result for the engine to apply atomically. A handler never writes Mongo and
never publishes.

**Safe to run twice.** The broker redelivers a step whose handler failed, so every
write is idempotent: S4 proposals and records and S1's views carry a key derived
from the step id; item and gate ids are deterministic; an item S1 already holds
with the same content is kept, not re-proposed.

**Who sees what.** Everything here is analyst-only. The PO is told nothing by
these steps: a stage change reaches the PO as the stage name alone (S1 writes it on
``PATCH state``), and no event carries a score, band, rating or critic finding to
the PO stream.

**Rules.** Agents never write a score (ADR-07): the score is S2's, requested in
:func:`~fcrm_caseflow.cores.assessment_score.score_case` from the items the agents'
proposals became. A control exists as an item only with the catalogue weight S3's
inventory gives it.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any, Final, Optional

from fcrm_common.errors import NotFoundError
from fcrm_common.time import utcnow
from fcrm_contracts.api.audit import Proposal
from fcrm_contracts.api.case_internal import (
    ItemsUpsert,
    ReviewItemInternal,
)
from fcrm_contracts.api.case_internal_assessment import (
    CompletenessPush,
    CriticFindingItem,
    CriticPush,
    ScreeningPush,
)
from fcrm_contracts.enums import AgentName, Domain, GateStatus, GateType, StepKind
from fcrm_contracts.events.payloads import GateDecided, HarnessStep
from fcrm_contracts.proposals.narrative import CompletenessReport, Question
from fcrm_contracts.proposals.screening import ScreeningProposal
from fcrm_guardrails.budget import CASE_TOKEN_CAP, CASE_TOKEN_TARGET
from fcrm_guardrails.rules import check_all
from fcrm_guardrails.rules.context import RulesContext
from fcrm_guardrails.thresholds import STALE_EVIDENCE_MONTHS

from fcrm_caseflow.cores.assessment_critic import DroppedFinding, check_critic
from fcrm_caseflow.cores.assessment_items import (
    GATED_KINDS,
    ControlDrafts,
    ItemDraft,
    ItemPlan,
    clause_drafts,
    control_drafts,
    critic_drafts,
    factor_drafts,
    gap_drafts,
    parse_assessment,
    parse_critic,
    parse_research,
    plan_items,
    superseded_item_ids,
    upsert_for,
)
from fcrm_caseflow.cores.assessment_scope import domains_for, input_fields_by_domain
from fcrm_caseflow.cores.assessment_score import (
    ScoreOutcome,
    push_score,
    route_phrase,
    score_case,
)
from fcrm_caseflow.cores.intake_records import plural, short
from fcrm_caseflow.cores.refs import (
    PUBLISHED_JOBS_REF,
    RERUN_ACTIVE_REF,
    RERUN_REF,
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
from fcrm_caseflow.dispatch import COMPLETENESS_GATE_KEY, FAST_LANE_GATE_KEY
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

_DEMO_RULE_COUNT: Final = 8


# ── screening (scene 8) ─────────────────────────────────────────────────────────


def _screening_proposal(result: AgentResult) -> Optional[ScreeningProposal]:
    """The screening agent's R4 proposal in ``result``, or ``None`` when Guard 4
    kept none (a claim without a verbatim source is dropped with its whole item on
    the retry, and the proposal is one item).
    """
    for item in result.accepted():
        if "fast_lane" in item:
            return ScreeningProposal.model_validate(item)
    return None


#: What the record says when screening kept no proposal.
NO_SCREENING_REASON: Final = (
    "the screening agent's proposal did not pass the source check; the standard "
    "lane applies"
)


async def screening_publish(step: HarnessStep, deps: StepDeps) -> StepResult:
    """``agent.done screening``: keep the proposal in S4's ledger, show it to the
    analyst, record it. When a fast lane is proposed the analyst confirms it at
    the ``fast_lane_confirm`` gate (R4: the agent proposes, a person decides); the
    engine opened that gate with the id written into S1's view.

    A job whose proposal Guard 4 dropped (live: an unsourced claim on the retry)
    is not a failure of the case: it proposes no fast lane, the case takes the
    standard lane, and the record says why. The step used to raise, retry three
    times and dead-letter, leaving the case waiting for a screening result that
    would never come.
    """
    results = await deps.require_agent_results().list_for(
        step.run_id, AgentName.SCREENING
    )
    result = result_for(results, step, "screening")
    proposal = _screening_proposal(result)
    fast_lane = proposal.fast_lane if proposal is not None else False
    given = list(proposal.reasons) if proposal is not None else []
    reasons = "; ".join(r.text for r in given) or (
        NO_SCREENING_REASON if proposal is None else "no reason given"
    )
    entry = await post_proposal(
        deps,
        step,
        result,
        kind="screening",
        payload={
            "summary": f"fast lane: {'yes' if fast_lane else 'no'}",
            "fast_lane": fast_lane,
            "reasons": [r.model_dump(mode="json") for r in given],
        },
        key="screening",
    )
    case = deps.require_case()
    await case.put_screening(
        step.case_id,
        ScreeningPush(
            run_id=step.run_id,
            fast_lane_proposed=fast_lane,
            reasons=given,
            proposal_id=entry.proposal_id,
            gate_id=(
                deterministic_gate_id(
                    step.run_id, GateType.FAST_LANE_CONFIRM, FAST_LANE_GATE_KEY
                )
                if fast_lane
                else None
            ),
        ),
        idempotency_key=idem(step, "screening"),
    )
    audit = await deps.append_record(
        step,
        "screening.proposed",
        f"Screening agent · fast lane: {'yes' if fast_lane else 'no'} "
        f"({short(reasons)})",
        {"fast_lane": fast_lane, "proposal_id": entry.proposal_id},
        actor="ai",
    )
    await case.patch_state(
        step.case_id,
        state_patch(deps.run).model_copy(update={"fast_lane_proposed": fast_lane}),
    )
    await show(
        deps,
        step,
        "screening.proposed",
        "analyst",
        "Screening proposes the fast lane" if fast_lane else "Screening: full path",
        {"fast_lane": fast_lane},
        record_seq=audit.seq,
    )
    return StepResult(
        counter_deltas={"tokens_used": tokens_of([result])},
    )


async def assess_open(step: HarnessStep, deps: StepDeps) -> StepResult:
    """The assessment stage opens (the engine has just dispatched the research
    and assessment agents, or the assessment alone on the fast lane): record it,
    tell S1 the stage. Nothing else waits on this step.
    """
    flags = deps.run.snapshot.flags
    agents = ["assessment"] if flags.fast_lane else ["research", "assessment"]
    audit = await deps.append_record(
        step,
        "workflow.stage",
        "Assessment core started · per-case budget "
        f"{CASE_TOKEN_CAP // 1000}k tokens (target {CASE_TOKEN_TARGET // 1000}k)",
        {
            "fast_lane": flags.fast_lane,
            "agents": agents,
            "budget": CASE_TOKEN_CAP,
            "target": CASE_TOKEN_TARGET,
        },
        actor="system",
    )
    await deps.require_case().patch_state(step.case_id, state_patch(deps.run))
    await show(
        deps,
        step,
        "workflow.stage",
        "analyst",
        "Assessment started on the fast lane"
        if flags.fast_lane
        else "Assessment started: research and assessment in parallel",
        {"agents": agents},
        record_seq=audit.seq,
    )
    return StepResult()


# ── research and assessment: items, score, gates (scene 8-9, 11) ────────────────


def _stale_controls(drafts: Sequence[ItemDraft], today: date) -> int:
    """How many scored controls rest on evidence older than the stale limit
    (code arithmetic on the evidence date, never the model's word).
    """
    stale = 0
    for draft in drafts:
        if draft.kind.value != "control" or draft.subject.get("planned"):
            continue
        when = str(draft.proposed.get("evidence_date") or "")
        if not when:
            continue
        evidence = date.fromisoformat(when)
        months = (today.year - evidence.year) * 12 + today.month - evidence.month
        if months > STALE_EVIDENCE_MONTHS:
            stale += 1
    return stale


def _in_scope(
    drafts: Sequence[ItemDraft], domains: set[Any], dropped: list[str]
) -> list[ItemDraft]:
    """The drafts of in-scope domains; the others are counted in ``dropped``."""
    kept: list[ItemDraft] = []
    for draft in drafts:
        if draft.domain is None or draft.domain in domains:
            kept.append(draft)
        else:
            dropped.append(f"{draft.kind.value}:{'/'.join(draft.parts)}")
    return kept


async def _ledger_entries(
    deps: StepDeps,
    step: HarnessStep,
    plan: ItemPlan,
    results: Mapping[AgentName, Sequence[AgentResult]],
) -> dict[str, Proposal]:
    """One S4 ledger entry per new item, by item id (an item that supersedes
    another names that item's entry). The first entry of each job carries the
    job's token usage.
    """
    entries: dict[str, Proposal] = {}
    carried: set[str] = set()
    for planned in plan.items:
        if not planned.is_new:
            continue
        job = _job_of(results, planned.draft)
        entries[planned.item_id] = await post_proposal(
            deps,
            step,
            job,
            kind=planned.draft.ledger_kind,
            payload=planned.draft.ledger_payload,
            key=planned.item_id,
            supersedes=planned.previous_proposal_id,
            carry_usage=job.job_id not in carried,
        )
        carried.add(job.job_id)
    return entries


def _job_of(
    results: Mapping[AgentName, Sequence[AgentResult]], draft: ItemDraft
) -> AgentResult:
    """The newest result of the agent that proposed ``draft``."""
    agent_results = results.get(draft.agent)
    if not agent_results:
        raise NotFoundError(f"no {draft.agent.value} agent result for a draft")
    return agent_results[-1]


def _gate_spec(item_id: str, kind: str) -> GateSpec:
    return GateSpec(GateType.REVIEW_ITEM, item_id, {"kind": kind, "id": item_id})


def review_gate_id(run_id: str, item_id: str) -> str:
    """The id of the ``review_item`` gate of ``item_id`` (written into the item
    S1 stores and opened, with the same id, by the engine from the step's result).
    """
    return deterministic_gate_id(run_id, GateType.REVIEW_ITEM, item_id)


async def _precedent_entry(
    deps: StepDeps,
    step: HarnessStep,
    results: Mapping[AgentName, Sequence[AgentResult]],
    precedents: Sequence[Any],
    notes: Sequence[Any],
) -> None:
    """The research agent's precedent matches and jurisdiction notes are not
    items (nothing in them is decided); they are kept in the ledger as one entry.
    """
    if not precedents and not notes:
        return
    await post_proposal(
        deps,
        step,
        results[AgentName.RESEARCH][-1],
        kind="research",
        payload={
            "summary": f"{plural(len(precedents), 'precedent')} matched",
            "precedents": [p.model_dump(mode="json") for p in precedents],
            "jurisdiction_notes": [n.model_dump(mode="json") for n in notes],
        },
        key="precedents",
        carry_usage=False,
    )


async def _publish_assessment(
    step: HarnessStep, deps: StepDeps, *, rerun: bool
) -> StepResult:
    """Publish the research and assessment agents' new proposals: ledger entries,
    review items with one gate each, the score of record and what it requires the
    analyst to acknowledge. ``rerun`` is the same work after a re-run, where an
    item whose inputs the owner changed is replaced by a new one.
    """
    case = deps.require_case()
    store = deps.require_agent_results()
    results = {
        AgentName.RESEARCH: await store.list_for(step.run_id, AgentName.RESEARCH),
        AgentName.ASSESSMENT: await store.list_for(step.run_id, AgentName.ASSESSMENT),
    }
    published = {str(j) for j in deps.run.refs.get(PUBLISHED_JOBS_REF) or ()}
    fresh = frozenset(
        r.job_id for rs in results.values() for r in rs if r.job_id not in published
    )
    fresh_results = [r for rs in results.values() for r in rs if r.job_id in fresh]
    research = parse_research(results[AgentName.RESEARCH], fresh)
    assessed = parse_assessment(results[AgentName.ASSESSMENT], fresh)

    record = await case.get_record(step.case_id)
    raw_spec = deps.run.refs.get(RERUN_REF) if rerun else None
    spec: Mapping[str, Any] = raw_spec if isinstance(raw_spec, Mapping) else {}
    change_types = [t.value for t in record.change_types]
    inputs = input_fields_by_domain(
        await deps.require_policy().field_set(str(record.pins.fieldset_v)),
        change_types,
    )
    inventory = await deps.require_policy().controls(str(record.pins.controls_v))
    catalogue = {c.control_id: c for c in inventory.controls}

    scope = set(domains_for(record))
    if spec.get("domains"):
        scope &= {Domain(str(n)) for n in spec["domains"]}
    controls: ControlDrafts = control_drafts(
        assessed.controls, catalogue, record.pins.controls_v, inputs
    )
    dropped: list[str] = []
    drafts = _in_scope(
        [
            *clause_drafts(research.clauses, record.pins.corpus_v, inputs),
            *gap_drafts(research.gaps, inputs),
            *factor_drafts(assessed.factors, inputs),
            *controls.drafts,
        ],
        scope,
        dropped,
    )
    if controls.unknown or controls.wrong_domain or dropped:
        logger.warning(
            "proposals left out of the assessment",
            extra={
                "case_id": step.case_id,
                "run_id": step.run_id,
                "unknown_controls": controls.unknown,
                "wrong_domain_controls": controls.wrong_domain,
                "out_of_scope": dropped,
            },
        )

    existing = await case.list_items(step.case_id)
    changed = frozenset(str(f) for f in spec.get("changed_fields") or ())
    plan = plan_items(
        step.case_id,
        drafts,
        existing,
        changed_fields=changed,
        full=bool(spec) and spec.get("scope") != "changed_fields",
    )
    entries = await _ledger_entries(deps, step, plan, results)
    await _precedent_entry(
        deps, step, results, research.precedents, research.jurisdiction_notes
    )
    upserts = [
        upsert_for(
            planned,
            gate_id=(
                review_gate_id(step.run_id, planned.item_id)
                if planned.draft.gated
                else None
            ),
            proposal_id=entries[planned.item_id].proposal_id,
            case_id=step.case_id,
        )
        for planned in plan.items
        if planned.is_new
    ]
    if upserts:
        await case.put_items(
            step.case_id, ItemsUpsert(run_id=step.run_id, items=upserts)
        )
    held = await case.list_items(step.case_id)

    outcome = await score_case(step, deps, held)
    await push_score(step, deps, outcome, "proposed")
    seq = await _record_publish(
        step, deps, research, assessed, controls, drafts, outcome, rerun=rerun
    )
    await case.patch_state(step.case_id, state_patch(deps.run))
    await show(
        deps,
        step,
        "score.recomputed" if rerun else "score.computed",
        "analyst",
        f"Score of record: composite {outcome.response.composite}, band {outcome.band}",
        {
            "composite": outcome.response.composite,
            "band": outcome.band,
            "boundary_flag": outcome.response.boundary_flag,
        },
        record_seq=seq,
    )
    ack = outcome.ack_gate(step)
    return StepResult(
        gates_to_open=(
            *(
                _gate_spec(p.item_id, p.draft.kind.value)
                for p in plan.items
                if p.draft.gated
            ),
            *((ack,) if ack is not None else ()),
        ),
        gates_to_close=_close_superseded(plan, held),
        refs_patch={
            SCORE_SUMMARY_REF: outcome.summary(seq),
            PUBLISHED_JOBS_REF: sorted(published | fresh),
            **({RERUN_ACTIVE_REF: None} if rerun else {}),
        },
        band=outcome.band,
        counter_deltas={"tokens_used": tokens_of(fresh_results)},
    )


def _close_superseded(
    plan: ItemPlan, held: Sequence[ReviewItemInternal]
) -> tuple[GateCloseSpec, ...]:
    """The gates of every superseded gated item: this publish's own, and any an
    earlier attempt of it left (closing a gate that is already closed changes
    nothing).
    """
    kinds = {row.item_id: row.kind for row in held}
    ids = sorted({*plan.superseded_ids, *superseded_item_ids(held)})
    return tuple(
        GateCloseSpec(
            GateType.REVIEW_ITEM,
            item_id,
            "superseded",
            GateStatus.EXPIRED,
            "a re-run replaced this proposal",
        )
        for item_id in ids
        if kinds.get(item_id) in GATED_KINDS or item_id not in kinds
    )


async def _record_publish(
    step: HarnessStep,
    deps: StepDeps,
    research: Any,
    assessed: Any,
    controls: ControlDrafts,
    drafts: Sequence[ItemDraft],
    outcome: ScoreOutcome,
    *,
    rerun: bool,
) -> int:
    """The walkthrough's records for a publish; returns the seq of the score
    record (the narrative cites it).
    """
    if research.clauses or research.gaps or research.precedents:
        await deps.append_record(
            step,
            "proposal.research",
            f"Research agent · {plural(len(research.clauses), 'clause')} · "
            f"{plural(len(research.precedents), 'precedent')} · "
            f"{plural(len(research.gaps), 'gap')} declared",
            {
                "clause_ids": sorted(c.clause_id for c in research.clauses),
                "gaps": [g.domain.value for g in research.gaps],
            },
            actor="ai",
        )
    if assessed.factors or assessed.controls:
        scored = [d for d in drafts if d.kind.value == "control"]
        planned = sum(1 for d in scored if d.subject.get("planned"))
        stale = _stale_controls(drafts, utcnow().date())
        await deps.append_record(
            step,
            "proposal.assessment",
            f"Assessment agent · L/I per area · "
            f"{plural(len(scored) - planned, 'control')} rated · "
            f"{stale} evidence stale"
            + (f" · {planned} planned, not scored" if planned else ""),
            {
                "domains": [d.domain.value for d in outcome.request.domains],
                "controls": len(scored) - planned,
                "planned": planned,
                "stale": stale,
                "left_out": controls.unknown + controls.wrong_domain,
            },
            actor="ai",
        )
    response = outcome.response
    score = await deps.append_record(
        step,
        "score.recomputed" if rerun else "score.computed",
        f"Composite {response.composite} · Band {outcome.band} · "
        f"{route_phrase(response)}",
        {
            "composite": response.composite,
            "band": outcome.band,
            "route": response.route.value,
            "boundary_flag": response.boundary_flag,
            "boundary_distance": response.boundary_distance,
            "scoring_v": response.scoring_v,
            "previous_composite": outcome.previous.composite
            if outcome.previous
            else None,
        },
        actor="system",
    )
    if response.boundary_flag and outcome.ack is not None:
        await deps.append_record(
            step,
            "boundary.flagged",
            f"Boundary flag · {response.boundary_distance} from a band edge · "
            "acknowledgement required",
            {"distance": response.boundary_distance, "ack": outcome.ack},
            actor="system",
        )
    return score.seq


async def assess_publish(step: HarnessStep, deps: StepDeps) -> StepResult:
    """Research and assessment are both done: publish their proposals, score the
    case, open one gate per item and the boundary acknowledgement, and move S1's
    stage to the critic.
    """
    return await _publish_assessment(step, deps, rerun=False)


async def rerun_publish(step: HarnessStep, deps: StepDeps) -> StepResult:
    """A re-run is done: publish what is new (an item whose inputs the owner
    changed is replaced by a new item and gate; a decided one is never edited),
    re-score and open the acknowledgement a changed band needs.
    """
    return await _publish_assessment(step, deps, rerun=True)


# ── critic (scene 9, 13) ────────────────────────────────────────────────────────


def _rules_kept(findings: Sequence[Any], outcome_request: Any) -> int:
    """How many of the eight rules hold over the critic's findings and the
    request that was scored (R-1: no score in a proposal; R-7: no planned control).
    """
    results = check_all(
        RulesContext(proposals=list(findings), scoring_input=outcome_request)
    )
    return sum(1 for r in results if r.ok)


_DROP_WORDS: Final = {
    "below_flag": "under the precedent flag",
    "contradictory": "with figures that contradict each other",
}


def _dropped_phrase(dropped: Sequence[DroppedFinding]) -> str:
    """`` · 2 precedent deviations dropped (1 under the precedent flag, ...)``
    for the ``critic.findings`` summary, or nothing when none was dropped.
    """
    if not dropped:
        return ""
    counts: dict[str, int] = {}
    for d in dropped:
        counts[d.reason] = counts.get(d.reason, 0) + 1
    why = ", ".join(f"{n} {_DROP_WORDS[r]}" for r, n in sorted(counts.items()))
    return f" · {plural(len(dropped), 'precedent deviation')} dropped ({why})"


async def critic_publish(step: HarnessStep, deps: StepDeps) -> StepResult:
    """``agent.done critic``: each finding becomes a ``critic_finding`` item with
    its own gate (the analyst explains or adjusts it), the findings are shown to
    the analyst, and S1's stage moves to completeness.

    A ``precedent_deviation`` is checked first (:mod:`.assessment_critic`): its
    numbers are worked out by code and carried as the finding's ``comparison``,
    and one the numbers do not support is dropped (no item, no gate) and counted
    in the ``critic.findings`` record.
    """
    case = deps.require_case()
    results = await deps.require_agent_results().list_for(step.run_id, AgentName.CRITIC)
    result = result_for(results, step, "critic")
    checked = await check_critic(step, deps, result, parse_critic([result]))
    findings = [c.finding for c in checked.kept]
    drafts = critic_drafts(findings, result.job_id, [c.position for c in checked.kept])
    existing = await case.list_items(step.case_id)
    plan = plan_items(
        step.case_id, drafts, existing, changed_fields=frozenset(), full=False
    )
    entries = await _ledger_entries(deps, step, plan, {AgentName.CRITIC: [result]})
    upserts = [
        upsert_for(
            planned,
            gate_id=review_gate_id(step.run_id, planned.item_id),
            proposal_id=entries[planned.item_id].proposal_id,
            case_id=step.case_id,
        )
        for planned in plan.items
        if planned.is_new
    ]
    if upserts:
        await case.put_items(
            step.case_id, ItemsUpsert(run_id=step.run_id, items=upserts)
        )
    view = await case.get_score_request(step.case_id)
    await case.put_critic(
        step.case_id,
        CriticPush(
            run_id=step.run_id,
            findings=[
                CriticFindingItem(
                    finding_id=planned.item_id,
                    gate_id=review_gate_id(step.run_id, planned.item_id),
                    finding_type=finding.finding_type,
                    severity=finding.severity,
                    record_refs=list(finding.record_refs),
                    text=finding.text,
                    source_ref=finding.source_ref,
                    quote=finding.quote,
                    confidence=finding.confidence,
                    comparison=item.comparison,
                )
                for planned, item, finding in zip(
                    plan.items, checked.kept, findings, strict=True
                )
            ],
        ),
        idempotency_key=idem(step, "critic"),
    )
    kept = _rules_kept(findings, view.request)
    audit = await deps.append_record(
        step,
        "critic.findings",
        f"Critic · {plural(len(findings), 'finding')} · "
        f"{kept} of {_DEMO_RULE_COUNT} rules kept" + _dropped_phrase(checked.dropped),
        {
            "findings": [
                {
                    "type": c.finding.finding_type,
                    "severity": c.finding.severity,
                    "records": c.finding.record_refs,
                    **(
                        {
                            "control_id": c.comparison.control_id,
                            "rating": c.comparison.rating,
                            "mean": c.comparison.mean,
                            "gap": c.comparison.gap,
                            "cases": len(c.comparison.cases),
                        }
                        if c.comparison is not None
                        else {}
                    ),
                }
                for c in checked.kept
            ],
            "rules_kept": kept,
            **(
                {
                    "dropped": [
                        {
                            "position": d.position,
                            "reason": d.reason,
                            "gap": d.gap,
                            "text": d.text,
                        }
                        for d in checked.dropped
                    ]
                }
                if checked.dropped
                else {}
            ),
        },
        actor="ai",
    )
    await case.patch_state(step.case_id, state_patch(deps.run))
    await show(
        deps,
        step,
        "critic.findings",
        "analyst",
        f"The critic raised {plural(len(findings), 'finding')}",
        {"findings": len(findings)},
        record_seq=audit.seq,
    )
    return StepResult(
        gates_to_open=tuple(
            _gate_spec(p.item_id, "critic_finding") for p in plan.items
        ),
        counter_deltas={"tokens_used": tokens_of([result])},
    )


# ── completeness (scene 9-10) ───────────────────────────────────────────────────


def _completeness_parts(
    result: AgentResult,
) -> tuple[CompletenessReport, list[Question]]:
    """The completeness report and the questions in a narrative result.

    Raises:
        fcrm_common.errors.NotFoundError: the job kept no report.
    """
    report: Optional[CompletenessReport] = None
    questions: list[Question] = []
    for item in result.accepted():
        if "missing" in item and "stale" in item:
            report = CompletenessReport.model_validate(item)
        elif "question_id" in item:
            questions.append(Question.model_validate(item))
    if report is None:
        raise NotFoundError(f"the narrative job {result.job_id} kept no report")
    return report, questions


def _counted(
    report: CompletenessReport, questions: Sequence[Question]
) -> CompletenessReport:
    """``report`` with its question counts taken from the questions the job
    gave: the model's own counts are a claim (a live run reported six drafted and
    five kept with no question in the answer, and the analyst was shown a round
    to send that was empty).
    """
    return report.model_copy(
        update={
            "questions_drafted": len(questions),
            "questions_kept": sum(1 for q in questions if q.dropped_reason is None),
        }
    )


async def completeness_publish(step: HarnessStep, deps: StepDeps) -> StepResult:
    """``agent.done narrative`` (completeness mode): show the report and the
    drafted questions to the analyst, who asks the owner one round of them or
    proceeds (the ``completeness`` gate the engine opened).
    """
    results = await deps.require_agent_results().list_for(
        step.run_id, AgentName.NARRATIVE
    )
    result = result_for(results, step, "narrative")
    report, questions = _completeness_parts(result)
    report = _counted(report, questions)
    entry = await post_proposal(
        deps,
        step,
        result,
        kind="completeness",
        payload={
            "summary": f"{len(report.missing)} missing, {len(report.stale)} stale, "
            f"{len(report.contradictory)} contradictory",
            **report.model_dump(mode="json"),
            "questions": [q.model_dump(mode="json") for q in questions],
        },
        key="completeness",
    )
    gate_id = deterministic_gate_id(
        step.run_id, GateType.COMPLETENESS, COMPLETENESS_GATE_KEY
    )
    case = deps.require_case()
    await case.put_completeness(
        step.case_id,
        CompletenessPush(
            run_id=step.run_id,
            report=report,
            questions=questions,
            gate_id=gate_id,
            proposal_id=entry.proposal_id,
        ),
        idempotency_key=idem(step, "completeness"),
    )
    audit = await deps.append_record(
        step,
        "completeness.report",
        f"Narrative agent · {len(report.missing)} missing · {len(report.stale)} stale"
        f" · {len(report.contradictory)} contradictory · "
        f"{plural(report.questions_drafted, 'question')} drafted",
        {
            "missing": len(report.missing),
            "stale": len(report.stale),
            "contradictory": len(report.contradictory),
            "questions_drafted": report.questions_drafted,
            "questions_kept": report.questions_kept,
            "proposal_id": entry.proposal_id,
        },
        actor="ai",
    )
    await show(
        deps,
        step,
        "completeness.report",
        "analyst",
        "The completeness report is ready: ask the owner or proceed",
        {"questions_kept": report.questions_kept},
        record_seq=audit.seq,
    )
    return StepResult(counter_deltas={"tokens_used": tokens_of([result])})


async def clarification_record(step: HarnessStep, deps: StepDeps) -> StepResult:
    """``gate.decided completeness{ask}``: the analyst asked the owner one round
    of questions (S1 created the threads before it published the decision):
    record it, tell S1 the round is open and the analyst's clock paused.
    """
    decided = step_payload(step, GateDecided)
    detail: Mapping[str, Any] = decided.after or {}
    round_no = detail.get("round", 1)
    count = detail.get("questions")
    sent = (
        f"{plural(int(count), 'question')} sent"
        if isinstance(count, int)
        else "questions sent"
    )
    audit = await deps.append_record(
        step,
        "clarification.sent",
        f"Round {round_no} · {sent} before review · analyst clock paused",
        {"round": round_no, "questions": count, "gate_id": decided.gate_id},
        actor=decided.decided_by,
    )
    await deps.require_case().patch_state(step.case_id, state_patch(deps.run))
    await show(
        deps,
        step,
        "clarification.sent",
        "analyst",
        f"Round {round_no} sent to the owner; the analyst clock is paused",
        {"round": round_no},
        record_seq=audit.seq,
    )
    return StepResult()


async def state_push(step: HarnessStep, deps: StepDeps) -> StepResult:
    """Tell S1 where the run is (its stage and flags): the steps of a transition
    that has nothing else to do (the analyst proceeds to review; every item is
    decided and the narrative is on its way). No record: the engine already wrote
    the transition's.
    """
    await deps.require_case().patch_state(step.case_id, state_patch(deps.run))
    return StepResult()


#: The assessment core's first-half handlers by step kind.
HANDLERS: Final[Mapping[StepKind, StepHandler]] = {
    StepKind.SCREENING_PUBLISH: screening_publish,
    StepKind.ASSESS_OPEN: assess_open,
    StepKind.ASSESS_PUBLISH: assess_publish,
    StepKind.RERUN_PUBLISH: rerun_publish,
    StepKind.CRITIC_PUBLISH: critic_publish,
    StepKind.COMPLETENESS_PUBLISH: completeness_publish,
    StepKind.CLARIFICATION_RECORD: clarification_record,
    StepKind.STATE_PUSH: state_push,
}
