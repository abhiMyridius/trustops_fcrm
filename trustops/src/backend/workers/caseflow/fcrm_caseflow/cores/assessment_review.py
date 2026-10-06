"""H1's assessment core, second half: the clarification round's answer and re-run,
and the analyst's decisions in review (scenes 10-14).

* ``rerun.record`` (``case.answered``): the owner answered; once any document they
  added is extracted, dispatch research and assessment again on the changed fields.
* ``review.item`` (``gate.decided review_item``): record the decision; when it
  changed a rating or a control, re-score from the case as it now stands and open the
  acknowledgement a changed band needs (R3).
* ``review.ack`` (``gate.decided ack``): record the acknowledgement.
* ``gap.record``, ``control.planned``, ``retag.record``, ``rerun.requested``: the
  analyst's other review actions.

**The score moves only through S2.** ``review.item`` never edits a number: the
decision is already in S1's item (S1 stores it before it publishes the gate
decision), the new score is S2's answer over S1's request, and the record says what
changed. A decision that changes nothing S2 reads (accepting a clause, explaining
a finding) is only recorded.

**Safe to run twice**: see :mod:`fcrm_caseflow.cores.assessment`.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any, Final, Optional

from fcrm_contracts.api.case_internal import ReviewItemInternal
from fcrm_contracts.enums import AgentName, ItemKind, StepKind
from fcrm_contracts.events.payloads import (
    CaseAnswered,
    CaseControlPlanned,
    CaseGapRecorded,
    CaseRerunRequested,
    CaseRetagged,
    GateDecided,
    HarnessStep,
)

from fcrm_caseflow.cores.assessment_scope import domains_for, scope_refs
from fcrm_caseflow.cores.assessment_score import (
    ScoreOutcome,
    push_score,
    route_phrase,
    score_case,
)
from fcrm_caseflow.cores.intake_records import plural, short
from fcrm_caseflow.cores.refs import (
    RERUN_PENDING_REF,
    RERUN_REF,
    SCORE_SUMMARY_REF,
)
from fcrm_caseflow.cores.shared import show, step_payload
from fcrm_caseflow.dispatch import AgentDispatch
from fcrm_caseflow.steps import (
    AgentJobSpec,
    StepDeps,
    StepHandler,
    StepResult,
)

logger = logging.getLogger(__name__)

#: Decisions on a factor or a control that change what S2 reads.
_VALUE_DECISIONS: Final = frozenset({"override", "remove", "adjust"})
_VERBS: Final[Mapping[str, str]] = {
    "accept": "accepted",
    "confirm": "confirmed",
    "remove": "removed",
    "explain": "explained",
    "adjust": "adjusted",
    "override": "overridden",
}
_KIND_NOUNS: Final[Mapping[ItemKind, str]] = {
    ItemKind.CLAUSE: "Clause",
    ItemKind.CONTROL: "Control",
    ItemKind.FACTOR: "Rating",
    ItemKind.CRITIC_FINDING: "Critic finding",
}


# ── the clarification round's answer and the re-run (scene 11) ──────────────────


#: The fields research's retrieval is worded from: the description (the policy and
#: precedent queries), the partner and the corridor (the jurisdiction lines). An
#: answer that changes none of them cannot change which clauses apply, so the
#: owner's answer re-runs assessment only (a research job is 12 to 15k tokens of
#: a case's 120k, and the clauses it proposed stay with their decisions).
RESEARCH_INPUT_FIELDS: Final = frozenset({"F02", "F06", "F20"})


def research_reruns(changed: Sequence[str]) -> bool:
    """Whether an answer that changed ``changed`` re-runs research too."""
    return bool(RESEARCH_INPUT_FIELDS & set(changed))


def _rerun_jobs(
    record_refs: Mapping[str, Any],
    *,
    scope: str,
    changed: Sequence[str],
    step: HarnessStep,
    agents: Sequence[AgentName] = (AgentName.RESEARCH, AgentName.ASSESSMENT),
) -> tuple[AgentJobSpec, ...]:
    """``agents`` (research and assessment unless told otherwise), scoped, with the
    refs H2's context packs read.
    """
    return tuple(
        AgentJobSpec(
            AgentDispatch(
                agent,
                scope=scope,
                changed_fields=tuple(changed),
                step_key=f"{scope}:{step.step_id}",
            ),
            dict(record_refs),
        )
        for agent in agents
    )


async def rerun_record(step: HarnessStep, deps: StepDeps) -> StepResult:
    """``case.answered``: record the answer, then re-run research and assessment
    on the fields the owner changed.

    A document the owner added with the answer may hold what a changed field now
    says, so the re-run waits for it: this step runs again (as a follow-up the
    intake steps enqueue) when each added document is extracted, and dispatches
    once none is still being read. The re-run's scope is ``changed_fields``; the
    agents see only what changed, and an item whose inputs did not change keeps
    its proposal and any decision on it.
    """
    case = deps.require_case()
    record = await case.get_record(step.case_id)
    if step.args.get("trigger") == "follow_up":
        pending = deps.run.refs.get(RERUN_PENDING_REF)
        if not isinstance(pending, Mapping):
            logger.info("no answer is waiting for a document; nothing to dispatch")
            return StepResult()
        spec: dict[str, Any] = dict(pending)
    else:
        answered = step_payload(step, CaseAnswered)
        spec = {
            "changed_fields": sorted(answered.changed_field_ids),
            "doc_ids": sorted(answered.added_document_ids),
            "round": answered.round,
            "submission_version": answered.submission_version,
        }
        version = answered.submission_version
        await deps.append_record(
            step,
            "owner.answered",
            f"Submission v{version} · "
            f"{plural(len(spec['changed_fields']), 'field')} changed · "
            f"{plural(len(spec['doc_ids']), 'document')} added · "
            f"v{max(version - 1, 1)} retained",
            {
                "round": answered.round,
                "submission_version": version,
                "changed_fields": spec["changed_fields"],
                "added_documents": spec["doc_ids"],
            },
            actor=answered.actor,
        )
    waiting = [
        d.doc_id
        for d in record.documents
        if d.doc_id in set(spec["doc_ids"]) and d.status == "stored"
    ]
    version_ref = {"submission_version": spec["submission_version"]}
    if waiting:
        logger.info(
            "the re-run waits for added documents to be extracted",
            extra={"case_id": step.case_id, "waiting": waiting},
        )
        return StepResult(refs_patch={RERUN_PENDING_REF: spec, **version_ref})
    refs = {**scope_refs(record), **version_ref}
    changed = list(spec["changed_fields"])
    agents = (
        (AgentName.RESEARCH, AgentName.ASSESSMENT)
        if research_reruns(changed)
        else (AgentName.ASSESSMENT,)
    )
    what = "Research and assessment" if len(agents) == 2 else "Assessment"
    audit = await deps.append_record(
        step,
        "agent.rerun",
        f"{what} re-run on changed fields only",
        {"changed_fields": changed, "round": spec["round"]},
        actor="system",
    )
    await show(
        deps,
        step,
        "agent.rerun",
        "analyst",
        f"{what} {'are' if len(agents) == 2 else 'is'} re-running on the changed "
        "fields",
        {"changed_fields": changed},
        record_seq=audit.seq,
    )
    return StepResult(
        agent_jobs_to_dispatch=_rerun_jobs(
            refs, scope="changed_fields", changed=changed, step=step, agents=agents
        ),
        refs_patch={
            RERUN_REF: {
                "scope": "changed_fields",
                "changed_fields": changed,
                "domains": [],
                "reason": f"clarification round {spec['round']}",
            },
            RERUN_PENDING_REF: None,
            **version_ref,
        },
        counter_deltas={"agents_run": len(agents)},
    )


# ── the analyst decides an item (scenes 12-14) ──────────────────────────────────


def _label(row: Optional[ReviewItemInternal], decided: GateDecided) -> str:
    """What the item is, for a record: the clause or control id, the domain of a
    rating, the text of a finding.
    """
    subject = row.subject if row is not None else {}
    if "clause_id" in subject:
        return str(subject["clause_id"])
    if "control_id" in subject:
        return str(subject["control_id"])
    if "text" in subject:
        return short(subject["text"])
    if "domain" in subject:
        return f"{subject['domain']} factors"
    return decided.subject.get("id", "item")


def _shown(value: Optional[Mapping[str, Any]]) -> str:
    """A decided value as a record shows it: ``4.5``, or ``L4 I5`` for a factor."""
    if not value:
        return "-"
    if "rating" in value:
        return str(value["rating"])
    return " ".join(f"{k[0].upper()}{v}" for k, v in value.items())


def _needs_rescore(decided: GateDecided, kind: Optional[ItemKind]) -> bool:
    """Whether the decision changed a value S2 reads."""
    if decided.rating_or_control_changed:
        return True
    return kind in (ItemKind.CONTROL, ItemKind.FACTOR) and (
        decided.decision in _VALUE_DECISIONS
    )


async def _record_decision(
    step: HarnessStep,
    deps: StepDeps,
    decided: GateDecided,
    row: Optional[ReviewItemInternal],
) -> int:
    """The record of one decision: ``human.override`` when the analyst replaced a
    value, ``gate.decided`` otherwise. Returns its seq.
    """
    label = _label(row, decided)
    reason = f" · {short(decided.reason)}" if decided.reason else ""
    data = {
        "gate_id": decided.gate_id,
        "item_id": decided.subject.get("id"),
        "decision": decided.decision,
        "reason": decided.reason,
        "before": decided.before,
        "after": decided.after,
    }
    if decided.decision == "override":
        audit = await deps.append_record(
            step,
            "human.override",
            f"{label} {_shown(decided.before)} → {_shown(decided.after)} by "
            f"{decided.decided_by} · reason recorded · hash-chained",
            data,
            actor=decided.decided_by,
        )
        return audit.seq
    kind = row.kind if row is not None else decided.item_kind
    noun = _KIND_NOUNS.get(kind, "Item") if kind is not None else "Item"
    verb = _VERBS.get(decided.decision, decided.decision)
    shown = ""
    if kind is ItemKind.CONTROL and row is not None:
        rating = row.proposed.get("rating")
        shown = f" {rating}" if rating is not None else ""
    audit = await deps.append_record(
        step,
        "gate.decided",
        f"{noun} {verb}: {label}{shown}{reason}",
        data,
        actor=decided.decided_by,
    )
    return audit.seq


async def _record_rescore(
    step: HarnessStep, deps: StepDeps, outcome: ScoreOutcome
) -> int:
    """``score.recomputed`` (and ``boundary.flagged`` when it asks for an
    acknowledgement); returns the score record's seq.
    """
    response = outcome.response
    score = await deps.append_record(
        step,
        "score.recomputed",
        f"Composite {response.composite} · Band {outcome.band} · "
        f"{route_phrase(response)}",
        {
            "composite": response.composite,
            "band": outcome.band,
            "route": response.route.value,
            "boundary_flag": response.boundary_flag,
            "boundary_distance": response.boundary_distance,
            "previous_composite": outcome.previous.composite
            if outcome.previous
            else None,
            "previous_band": int(outcome.previous.band.value)
            if outcome.previous
            else None,
            "band_changed": outcome.band_changed,
        },
        actor="system",
    )
    if outcome.ack is not None and response.boundary_flag:
        await deps.append_record(
            step,
            "boundary.flagged",
            f"Boundary flag · {response.boundary_distance} from a band edge · "
            "acknowledgement required",
            {"distance": response.boundary_distance, "ack": outcome.ack},
            actor="system",
        )
    return score.seq


async def review_item(step: HarnessStep, deps: StepDeps) -> StepResult:
    """``gate.decided review_item``: record the decision and, when it changed a
    rating or a control, re-score and open the acknowledgement a changed band
    needs. The decision is already in S1's item; this step only reads it.
    """
    decided = step_payload(step, GateDecided)
    case = deps.require_case()
    items = await case.list_items(step.case_id)
    item_id = decided.subject.get("id")
    row = next((r for r in items if r.item_id == item_id), None)
    if row is None:
        logger.warning(
            "the decided item is not in S1's list",
            extra={"case_id": step.case_id, "gate_id": decided.gate_id},
        )
    decision_seq = await _record_decision(step, deps, decided, row)
    kind = row.kind if row is not None else decided.item_kind
    if not _needs_rescore(decided, kind):
        return StepResult()
    outcome = await score_case(step, deps, items)
    await push_score(step, deps, outcome, "confirmed")
    seq = await _record_rescore(step, deps, outcome)
    await show(
        deps,
        step,
        "score.recomputed",
        "analyst",
        f"Score re-computed: composite {outcome.response.composite}, "
        f"band {outcome.band}"
        + (" (the band changed: acknowledge it)" if outcome.band_changed else ""),
        {
            "composite": outcome.response.composite,
            "band": outcome.band,
            "band_changed": outcome.band_changed,
            "decision_seq": decision_seq,
        },
        record_seq=seq,
    )
    ack = outcome.ack_gate(step)
    return StepResult(
        gates_to_open=(ack,) if ack is not None else (),
        refs_patch={SCORE_SUMMARY_REF: outcome.summary(seq)},
        band=outcome.band,
    )


async def review_ack(step: HarnessStep, deps: StepDeps) -> StepResult:
    """``gate.decided ack``: the analyst acknowledged a boundary flag or a band
    change (R3). Recorded as the walkthrough has it; once every item and every
    acknowledgement is done the engine's chain moves the run on to the narrative.
    """
    decided = step_payload(step, GateDecided)
    kind = decided.subject.get("id", "boundary")
    flagged = bool((deps.run.refs.get(SCORE_SUMMARY_REF) or {}).get("boundary_flag"))
    what = (
        "Band change and boundary flag"
        if kind == "band_change" and flagged
        else "Band change"
        if kind == "band_change"
        else "Boundary flag"
    )
    audit = await deps.append_record(
        step,
        "gate.decided",
        f"{what} acknowledged by analyst",
        {
            "gate_id": decided.gate_id,
            "kind": kind,
            "note": decided.reason,
        },
        actor=decided.decided_by,
    )
    await show(
        deps,
        step,
        "ack.recorded",
        "analyst",
        f"{what} acknowledged",
        {"kind": kind},
        record_seq=audit.seq,
    )
    return StepResult()


# ── the analyst's other review actions ──────────────────────────────────────────


async def gap_record(step: HarnessStep, deps: StepDeps) -> StepResult:
    """``case.gap_recorded``: the analyst searched the corpus by hand and found
    nothing for something the case needs. Record it and hand it to the
    ``policy_gap`` agent as an oversight job: never on the decision path (ADR-18),
    so nothing waits for it.
    """
    gap = step_payload(step, CaseGapRecorded)
    record = await deps.require_case().get_record(step.case_id)
    audit = await deps.append_record(
        step,
        "gap.recorded",
        f"Policy gap recorded: {short(gap.text)} · noted for the analyst's "
        "assurance review",
        {"gap_id": gap.gap_id, "scope": gap.scope},
        actor=gap.actor,
    )
    refs = {
        **scope_refs(record),
        "gap_records": [{"case_id": step.case_id, "seq": audit.seq, "text": gap.text}],
    }
    return StepResult(
        agent_jobs_to_dispatch=(
            AgentJobSpec(
                AgentDispatch(AgentName.POLICY_GAP, step_key=f"gap:{gap.gap_id}"), refs
            ),
        ),
        counter_deltas={"agents_run": 1},
    )


async def control_planned(step: HarnessStep, deps: StepDeps) -> StepResult:
    """``case.control_planned``: record the planned control. It is shown to the
    analyst and never scored (rule 7): S1 keeps it apart from the items S2 reads,
    so there is nothing to do here but say so on the chain.
    """
    planned = step_payload(step, CaseControlPlanned)
    detail = planned.planned_control
    what = detail.get("description") or detail.get("control_id") or "control"
    await deps.append_record(
        step,
        "control.planned",
        f"{short(what)} recorded as planned control · not scored",
        {
            "control_id": detail.get("control_id"),
            "owner": detail.get("owner"),
            "target_date": detail.get("target_date"),
        },
        actor=planned.actor,
    )
    return StepResult()


async def retag_record(step: HarnessStep, deps: StepDeps) -> StepResult:
    """``case.retagged``: the analyst added a change type (S1 expanded the field
    set). Record it and, if it puts the case in a domain it was not assessed in,
    run research and assessment for that domain only.
    """
    retagged = step_payload(step, CaseRetagged)
    record = await deps.require_case().get_record(step.case_id)
    known = {str(d) for d in deps.run.refs.get("domains") or ()}
    current = [d.value for d in domains_for(record)]
    added = sorted(set(current) - known)
    required = len(record.required_field_ids)
    audit = await deps.append_record(
        step,
        "fieldset.resolved",
        f"Field set expanded for {retagged.change_type.value.replace('_', ' ')} · "
        f"{plural(required, 'field')} required",
        {
            "change_type": retagged.change_type.value,
            "reason": retagged.reason,
            "required_field_ids": list(record.required_field_ids),
            "new_domains": added,
        },
        actor=retagged.actor,
    )
    refs_patch: dict[str, Any] = {"domains": current}
    if not added:
        return StepResult(refs_patch=refs_patch)
    job_refs = {**scope_refs(record), "domains": added}
    await show(
        deps,
        step,
        "agent.rerun",
        "analyst",
        f"Research and assessment are running for {', '.join(added)}",
        {"domains": added},
        record_seq=audit.seq,
    )
    return StepResult(
        agent_jobs_to_dispatch=_rerun_jobs(
            job_refs, scope="full", changed=[], step=step
        ),
        refs_patch={
            **refs_patch,
            RERUN_REF: {
                "scope": "new_domain",
                "changed_fields": [],
                "domains": added,
                "reason": retagged.reason,
            },
        },
        counter_deltas={"agents_run": 2},
    )


async def rerun_requested(step: HarnessStep, deps: StepDeps) -> StepResult:
    """``case.rerun_requested`` (the guard: a reason is present): run research and
    assessment again. The request names a scope but no list of fields, so either
    scope is a full re-run of the in-scope domains; an item is still only
    replaced when the new proposal differs from it, so nothing churns.
    """
    requested = step_payload(step, CaseRerunRequested)
    record = await deps.require_case().get_record(step.case_id)
    scope_note = (
        "changed fields requested, no field list given: treated as full"
        if requested.scope == "changed_fields"
        else "full"
    )
    audit = await deps.append_record(
        step,
        "agent.rerun",
        f"Research and assessment re-run · {scope_note} · "
        f"{short(requested.rerun_reason)}",
        {"scope": requested.scope, "reason": requested.rerun_reason},
        actor=requested.actor,
    )
    await show(
        deps,
        step,
        "agent.rerun",
        "analyst",
        "Research and assessment are re-running",
        {"scope": "full"},
        record_seq=audit.seq,
    )
    return StepResult(
        agent_jobs_to_dispatch=_rerun_jobs(
            scope_refs(record), scope="full", changed=[], step=step
        ),
        refs_patch={
            RERUN_REF: {
                "scope": "full",
                "changed_fields": [],
                "domains": [],
                "reason": requested.rerun_reason,
            }
        },
        counter_deltas={"agents_run": 2},
    )


#: The review half's handlers by step kind.
HANDLERS: Final[Mapping[StepKind, StepHandler]] = {
    StepKind.RERUN_RECORD: rerun_record,
    StepKind.REVIEW_ITEM: review_item,
    StepKind.REVIEW_ACK: review_ack,
    StepKind.GAP_RECORD: gap_record,
    StepKind.CONTROL_PLANNED: control_planned,
    StepKind.RETAG_RECORD: retag_record,
    StepKind.RERUN_REQUESTED: rerun_requested,
}
