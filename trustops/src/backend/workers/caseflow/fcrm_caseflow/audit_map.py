"""Which trigger starts which step, and which audit records each step writes
(design A §3.5's ``caseflow/audit_map.py``; ADR-28).

:data:`STEP_FOR` is the wiring table the engine consults: after it applies a
trigger, a step is enqueued if (and only if) the trigger is a key here. An
entry is added by the lane that implements the step's handler, in the same
commit, so a trigger never enqueues a step nobody can run (the runner would
fail it with ``UnknownStepKindError`` and the run would wait; ``build_unit``
refuses to start in that case). The engine takes the table as a constructor
argument, so tests inject their own.

:data:`RECORDS_FOR` is documentation that tests hold to: for every
:class:`~fcrm_contracts.enums.StepKind`, the audit record types a finished
step has produced in S4's chain. The vocabulary is the walkthrough seed's
(``demo.audit[].type``, the 67 records of FCRM-2026-0151) plus the catalogued
event names for the paths the demo does not take. "Produced" includes a
record a service writes when H1 asks it to (``outcome.set`` from S5), because
what a reader of the chain sees is the same. The engine also writes one
generic record per applied transition (type = the trigger's value), except
where the step writes a record of that very type (``case.created``,
``case.submitted``): :class:`~fcrm_caseflow.engine.CaseflowEngine` reads this
table to avoid the duplicate. H2 writes ``guard.permit``, ``guard.output`` and
``agent.retry`` itself.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from fcrm_contracts.enums import StepKind

from fcrm_caseflow.triggers import Trigger

K = StepKind

#: Trigger -> the step it enqueues. One entry per step a core has a handler
#: for (``fcrm_caseflow.cores``): the intake core's (scenes 2-7) are here; the
#: assessment, decision and oversight cores add theirs with their handlers.
STEP_FOR: Final[Mapping[Trigger, StepKind]] = MappingProxyType(
    {
        Trigger.CASE_CREATED: K.INTAKE_OPEN,
        Trigger.CASE_DOCUMENT_STORED: K.INTAKE_DOCUMENT_STORED,
        Trigger.INGEST_DONE: K.INTAKE_INGESTED,
        Trigger.INGEST_FAILED: K.INTAKE_INGEST_FAILED,
        Trigger.AGENT_DONE_INTAKE: K.INTAKE_PUBLISH,
        Trigger.GATE_DECIDED_PO_CONFIRM_CATEGORY_ALL: K.INTAKE_FIELDSET_RESOLVED,
        Trigger.GATE_DECIDED_PO_ACTION: K.INTAKE_PO_ACTION,
        Trigger.CASE_SUBMITTED: K.SUBMIT_RECORD,
        Trigger.TRIAGE_DISPATCH: K.TRIAGE_ASSIGN,
        # assessment (scenes 8-16)
        Trigger.AGENT_DONE_SCREENING: K.SCREENING_PUBLISH,
        Trigger.ASSESS_DISPATCH: K.ASSESS_OPEN,
        Trigger.AGENT_DONE_ASSESS_BOTH: K.ASSESS_PUBLISH,
        Trigger.AGENT_DONE_CRITIC: K.CRITIC_PUBLISH,
        Trigger.AGENT_DONE_NARRATIVE_COMPLETENESS: K.COMPLETENESS_PUBLISH,
        Trigger.GATE_DECIDED_COMPLETENESS_ASK: K.CLARIFICATION_RECORD,
        Trigger.GATE_DECIDED_COMPLETENESS_PROCEED: K.STATE_PUSH,
        Trigger.CASE_ANSWERED: K.RERUN_RECORD,
        Trigger.AGENT_DONE_RERUN_BOTH: K.RERUN_PUBLISH,
        Trigger.GATE_DECIDED_REVIEW_ITEM: K.REVIEW_ITEM,
        Trigger.GATE_DECIDED_ACK: K.REVIEW_ACK,
        Trigger.CASE_GAP_RECORDED: K.GAP_RECORD,
        Trigger.CASE_CONTROL_PLANNED: K.CONTROL_PLANNED,
        Trigger.CASE_RETAGGED: K.RETAG_RECORD,
        Trigger.CASE_RERUN_REQUESTED: K.RERUN_REQUESTED,
        Trigger.ALL_ITEMS_DECIDED: K.STATE_PUSH,
        Trigger.AGENT_DONE_NARRATIVE_RECOMMENDATION: K.NARRATIVE_PUBLISH,
        Trigger.CASE_LOCKED: K.LOCK_BUILD_PACK,
        Trigger.PACK_RELEASED: K.PACK_APPROVED,
        Trigger.CASE_UNLOCKED: K.UNLOCK_SUPERSEDE,
        # decision and closing (scenes 17-19)
        Trigger.VOTE_CAST: K.VOTE_RECORD,
        Trigger.COMMITTEE_QUESTION_ASKED: K.COMMITTEE_QUESTION,
        Trigger.COMMITTEE_ANSWERED_NO_CHANGE: K.COMMITTEE_ANSWER,
        Trigger.COMMITTEE_ANSWERED_CHANGED: K.COMMITTEE_ANSWER,
        Trigger.VOTE_TALLIED: K.DECIDE_OUTCOME,
        Trigger.VOTE_NO_MAJORITY: K.DECIDE_NO_MAJORITY,
        Trigger.CASE_CLOSE_REQUESTED: K.DECIDE_DELEGATED,
        Trigger.CLOSED_DISPATCH: K.CLOSED_FINALIZE,
    }
)

#: Step kind -> the audit record types it produces, in the order they appear.
#: Every :class:`StepKind` has an entry (an empty tuple means "none").
RECORDS_FOR: Final[Mapping[StepKind, tuple[str, ...]]] = MappingProxyType(
    {
        # intake (scenes 2-7)
        # S1 writes no audit records of its own; the steps record what the PO and
        # S1 did on the chain, so scenes 2-7 read as the walkthrough's.
        K.INTAKE_OPEN: ("case.created", "workflow.started"),
        K.INTAKE_DOCUMENT_STORED: ("document.stored",),  # + ingest.run, by the engine
        K.INTAKE_INGESTED: ("guard.input",),
        K.INTAKE_INGEST_FAILED: ("guard.input",),
        K.INTAKE_PUBLISH: (
            "proposal.extraction",
            "proposal.category",
            "proposal.gaps",
            "precedent.matched",
            "fact.flagged",
        ),
        K.INTAKE_FIELDSET_RESOLVED: ("gate.decided", "fieldset.resolved"),
        K.INTAKE_PO_ACTION: (
            "gate.decided",
            "owner.corrected",
            "values.reused",
            "owner.answered",
        ),
        K.SUBMIT_RECORD: ("gate.validation", "case.submitted"),
        K.TRIAGE_ASSIGN: ("triage.assigned", "eval.recorded"),
        # assessment (scenes 8-9)
        K.SCREENING_PUBLISH: ("screening.proposed",),
        K.ASSESS_OPEN: ("workflow.stage",),
        K.ASSESS_PUBLISH: (
            "proposal.research",
            "proposal.assessment",
            "score.computed",
            "boundary.flagged",
        ),
        K.CRITIC_PUBLISH: ("critic.findings",),
        K.COMPLETENESS_PUBLISH: ("completeness.report",),
        # clarification and review (scenes 10-14)
        K.CLARIFICATION_RECORD: ("clarification.sent",),
        K.RERUN_RECORD: ("owner.answered", "agent.rerun"),
        K.RERUN_PUBLISH: (
            "proposal.research",
            "proposal.assessment",
            "score.recomputed",
            "boundary.flagged",
        ),
        K.REVIEW_ITEM: (
            "gate.decided",
            "human.override",
            "score.recomputed",
            "boundary.flagged",
        ),
        K.REVIEW_ACK: ("gate.decided",),
        K.GAP_RECORD: ("gap.recorded",),
        K.CONTROL_PLANNED: ("control.planned",),
        K.RETAG_RECORD: ("fieldset.resolved",),
        K.RERUN_REQUESTED: ("agent.rerun",),
        # recommendation and pack (scenes 15-16)
        K.NARRATIVE_PUBLISH: ("proposal.narrative", "guard.output"),
        K.LOCK_BUILD_PACK: (
            "recommendation.locked",
            "pack.assembled",
            "gate.validation",
        ),
        K.PACK_APPROVED: ("pack.approved",),
        K.UNLOCK_SUPERSEDE: ("case.unlocked", "pack.superseded"),
        # committee and close (scenes 17-19)
        K.VOTE_RECORD: ("vote.cast",),
        K.COMMITTEE_QUESTION: ("committee.question",),
        # R6 (numbers changed): the answer, the unlock, the superseded pack and the
        # re-request of every cast vote; an addendum answer writes only the first.
        K.COMMITTEE_ANSWER: (
            "committee.answer",
            "case.unlocked",
            "pack.superseded",
            "vote.rerequested",
        ),
        K.DECIDE_DELEGATED: ("outcome.set",),
        K.DECIDE_OUTCOME: ("vote.revealed", "outcome.set"),
        K.DECIDE_NO_MAJORITY: ("vote.no_majority", "vote.rerequested"),
        K.CLOSED_FINALIZE: ("outcome.delivered", "case.closed", "chain.verified"),
        K.HANDOFF_OPEN: ("agent.failed", "human.handoff"),
        # oversight (scene 20)
        K.EXAMINER_ANSWER: ("examiner.asked",),
        K.WATCHLIST_PUBLISH: ("reflag.proposed",),
        K.REFLAG_ACK: ("reflag.acknowledged",),
        K.STATE_PUSH: (),  # pushes snapshots to S1's view store, no record
    }
)
