"""Every trigger the state machine responds to (design A §3.1's "Trigger
(consumed)" column). A handful of rows are marked "internal" in the design
table — meaning Caseflow dispatches the next step itself on completing the
previous one, with no external event — and are modelled here as ordinary
triggers Caseflow raises against itself, since that is exactly what they
are: a typed self-event, not a special case the state machine needs to
know about.
"""

from __future__ import annotations

from enum import StrEnum


class Trigger(StrEnum):
    CASE_CREATED = "case.created"
    CASE_DOCUMENT_STORED = "case.document_stored"
    AGENT_DONE_INTAKE = "agent.done.intake"
    GATE_DECIDED_PO_CONFIRM_CATEGORY_ALL = "gate.decided.po_confirm_category.all"
    # One of the PO's other intake decisions: a change type confirmed while
    # others are still open, a value confirmed or corrected, values reused from
    # a precedent, a gap question answered. Like ``CASE_CREATED`` it is a
    # step-only trigger: no transition exists for it and the run does not move;
    # the engine only starts the step that records what the PO did
    # (``audit_map.STEP_FOR``).
    GATE_DECIDED_PO_ACTION = "gate.decided.po_action"
    CASE_SUBMITTED = "case.submitted"

    TRIAGE_DISPATCH = "internal.triage_dispatch"
    SCREENING_DISPATCH = "internal.screening_dispatch"
    AGENT_DONE_SCREENING = "agent.done.screening"
    GATE_DECIDED_FAST_LANE_CONFIRM = "gate.decided.fast_lane_confirm"

    ASSESS_DISPATCH = "internal.assess_dispatch"
    AGENT_DONE_ASSESS_BOTH = "agent.done.research_and_assessment"

    CRITIC_DISPATCH = "internal.critic_dispatch"
    AGENT_DONE_CRITIC = "agent.done.critic"

    COMPLETENESS_DISPATCH = "internal.completeness_dispatch"
    AGENT_DONE_NARRATIVE_COMPLETENESS = "agent.done.narrative.completeness"
    GATE_DECIDED_COMPLETENESS_ASK = "gate.decided.completeness.ask"
    GATE_DECIDED_COMPLETENESS_PROCEED = "gate.decided.completeness.proceed"

    CASE_ANSWERED = "case.answered"
    # D-130: an intake job failed before the owner submitted. Resolved by the
    # engine from ``agent.failed`` in INTAKE (never on the wire): the run stays
    # in INTAKE with the owner instead of going to HANDOFF.
    INTAKE_JOB_FAILED = "agent.failed.intake"
    # D-130: an operator returns a case stuck in HANDOFF from INTAKE to its
    # owner (the cases that failed before INTAKE_JOB_FAILED existed).
    CASE_INTAKE_RECOVER = "case.intake_recover"
    # The analyst resolved the last answer of the clarification round (D-129):
    # the round is done once the re-run has landed too.
    CASE_CLARIFICATION_RESOLVED = "case.clarification_resolved"
    AGENT_DONE_RERUN_BOTH = "agent.done.rerun.research_and_assessment"

    # H3's result for an ``ingest.run`` H1 published. Both are self-loops:
    # the run stays in INTAKE (first submission) or CLARIFICATION (a document
    # added with an answer); the work is the step they enqueue.
    INGEST_DONE = "ingest.done"
    INGEST_FAILED = "ingest.failed"

    GATE_DECIDED_REVIEW_ITEM = "gate.decided.review_item"
    # The analyst acknowledged a boundary flag or a band change (R3). Like
    # ``GATE_DECIDED_PO_ACTION`` it is step-only: no transition exists for it and
    # the run does not move; the engine only starts the step that records the
    # acknowledgement (``audit_map.STEP_FOR``), after which the internal chain
    # re-checks whether every item and acknowledgement is now done.
    GATE_DECIDED_ACK = "gate.decided.ack"
    CASE_RETAGGED = "case.retagged"
    # The owner added or removed a change type outside a po_confirm_category
    # gate (ADR-75): a self-loop in INTAKE whose step records it and the field
    # set it resolves.
    CASE_TYPES_CHANGED = "case.types_changed"
    CASE_RERUN_REQUESTED = "case.rerun_requested"
    CASE_GAP_RECORDED = "case.gap_recorded"
    CASE_CONTROL_PLANNED = "case.control_planned"
    # The analyst added a clause from a corpus search, or removed one they added
    # (ADR-74). Step-only, like ``GATE_DECIDED_ACK``: no transition, the run does
    # not move (review or recommend); the step writes the audit record.
    CASE_CLAUSE_ADDED = "case.clause_added"
    CASE_CLAUSE_REMOVED = "case.clause_removed"
    # The analyst asked the owner an ad-hoc question, or the owner answered one
    # (ADR-73). Self-loops in every state from TRIAGE to DECIDE and in HANDOFF:
    # the run never moves on them; the work is the step they enqueue.
    CASE_QUESTION_ASKED = "case.question_asked"
    CASE_QUESTION_ANSWERED = "case.question_answered"
    ALL_ITEMS_DECIDED = "internal.all_items_decided"

    AGENT_DONE_NARRATIVE_RECOMMENDATION = "agent.done.narrative.recommendation"
    CASE_LOCKED = "case.locked"
    PACK_RELEASED = "pack.released"
    CASE_UNLOCKED = "case.unlocked"

    CASE_CLOSE_REQUESTED = "case.close_requested"
    VOTE_CAST = "vote.cast"
    COMMITTEE_QUESTION_ASKED = "committee.question_asked"
    COMMITTEE_ANSWERED_NO_CHANGE = "committee.answered.no_change"
    COMMITTEE_ANSWERED_CHANGED = "committee.answered.changed"
    VOTE_TALLIED = "vote.tallied"
    VOTE_NO_MAJORITY = "vote.no_majority"
    OUTCOME_SET_DELEGATED = "outcome.set.delegated"

    CLOSED_DISPATCH = "internal.closed_dispatch"

    # Oversight (scene 20). Every one is step-only: no transition row exists, the
    # run does not move, and the engine only starts the step that does the work
    # (``audit_map.STEP_FOR``). The two events name no open run
    # (``fcrm_caseflow.oversight_runs``); the rest come from H2's results of the
    # examiner and the watchlist, and from an analyst's re-flag acknowledgement.
    EXAMINER_ASKED = "examiner.asked"
    JURISDICTION_LIST_PUBLISHED = "jurisdiction.list_published"
    AGENT_DONE_EXAMINER = "agent.done.examiner"
    AGENT_FAILED_EXAMINER = "agent.failed.examiner"
    AGENT_DONE_WATCHLIST = "agent.done.watchlist"
    AGENT_FAILED_WATCHLIST = "agent.failed.watchlist"
    GATE_DECIDED_REFLAG_ACK = "gate.decided.reflag_ack"

    AGENT_FAILED_AFTER_RETRY = "agent.failed.after_retry"
    GATE_DECIDED_HANDOFF = "gate.decided.handoff"
