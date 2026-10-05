"""Which trigger starts which step, and which audit records each step writes
(design A §3.5's ``caseflow/audit_map.py``; ADR-28).

:data:`STEP_FOR` is the wiring table the engine consults: after it applies a
trigger, a step is enqueued if (and only if) the trigger is a key here. It
ships **empty** — the lane that implements a step's handler adds its entry,
in the same commit, so a trigger never enqueues a step nobody can run (the
runner would fail it with ``UnknownStepKindError`` and the run would wait).
The engine takes the table as a constructor argument, so tests inject their
own.

:data:`RECORDS_FOR` is documentation that tests hold to: for every
:class:`~fcrm_contracts.enums.StepKind`, the audit record types a finished
step has produced in S4's chain. The vocabulary is the walkthrough seed's
(``demo.audit[].type``, the 67 records of FCRM-2026-0151) plus the catalogued
event names for the paths the demo does not take. "Produced" includes a
record a service writes when H1 asks it to (``outcome.set`` from S5), because
what a reader of the chain sees is the same. Records the engine writes for a
transition itself (``workflow.started``...) and the ones S1 and H2 write
(``document.stored``, ``guard.permit``, ``guard.output``, ``agent.retry``)
belong to their writers, not to a step.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from fcrm_contracts.enums import StepKind

from fcrm_caseflow.triggers import Trigger

#: Trigger -> the step it enqueues. Empty until the cores land (I2-I4).
STEP_FOR: Final[Mapping[Trigger, StepKind]] = MappingProxyType({})

K = StepKind

#: Step kind -> the audit record types it produces, in the order they appear.
#: Every :class:`StepKind` has an entry (an empty tuple means "none").
RECORDS_FOR: Final[Mapping[StepKind, tuple[str, ...]]] = MappingProxyType(
    {
        # intake (scenes 2-7)
        K.INTAKE_OPEN: ("workflow.started",),
        K.INTAKE_DOCUMENT_STORED: (),  # publishes ingest.run; S1 wrote document.stored
        K.INTAKE_INGESTED: ("guard.input",),
        K.INTAKE_INGEST_FAILED: ("guard.input",),
        K.INTAKE_PUBLISH: (
            "proposal.extraction",
            "proposal.category",
            "proposal.gaps",
            "precedent.matched",
        ),
        K.INTAKE_FIELDSET_RESOLVED: ("fieldset.resolved",),
        K.INTAKE_PO_ACTION: ("fact.flagged",),
        K.SUBMIT_RECORD: ("gate.validation", "eval.recorded"),
        K.TRIAGE_ASSIGN: ("triage.assigned",),
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
        K.RERUN_RECORD: ("agent.rerun",),
        K.RERUN_PUBLISH: (
            "proposal.research",
            "proposal.assessment",
            "score.recomputed",
        ),
        K.REVIEW_ITEM: ("human.override", "score.recomputed", "boundary.flagged"),
        K.REVIEW_ACK: (),  # S1 wrote the gate.decided record for the ack
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
        K.COMMITTEE_ANSWER: ("committee.answer",),
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
