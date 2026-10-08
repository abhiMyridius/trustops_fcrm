"""The event catalogue: every ``Envelope.type`` value this system emits or
consumes, one place so a typo is a lookup failure, not a silent mismatch.
Mirrored in docs/architecture/event-catalogue.md with routing-key detail.
"""

from __future__ import annotations

EVENT_TYPES: frozenset[str] = frozenset(
    {
        # case lifecycle (S1 -> H1)
        "case.created",
        "case.document_stored",
        "case.submitted",
        "case.answered",
        "case.retagged",
        "case.types_changed",  # S1 -> H1: the owner added or removed a type (ADR-72)
        "case.rerun_requested",
        "case.gap_recorded",
        "case.locked",
        "case.unlocked",
        "case.close_requested",
        "case.closed",
        # conditions (oversight, post-close; design A §3.6, D-19a's 14-day reminder)
        "condition.activated",
        "condition.updated",
        "condition.completed",
        "condition.reminder_due",
        # harness -> services
        "workflow.started",
        "agent.run",
        "agent.done",
        "agent.failed",
        "agent.retry",
        "gate.opened",
        "gate.decided",
        "score.computed",
        "score.recomputed",
        "boundary.flagged",
        "triage.assigned",
        "screening.proposed",
        "critic.findings",
        "completeness.report",
        "clarification.sent",
        "fieldset.resolved",
        "values.reused",
        "gap.recorded",
        "recommendation.locked",
        "pack.assembled",
        "pack.approved",
        "pack.superseded",
        "human.override",
        "human.handoff",
        # ingestion / context
        "ingest.run",
        "ingest.done",
        "ingest.failed",
        "index.build",
        "index.built",
        "index.failed",
        # decision (S5 -> H1)
        "vote.cast",
        "vote.revealed",
        "vote.no_majority",
        "vote.rerequested",
        "committee.question",
        "committee.answer",
        "outcome.set",
        # oversight
        "reflag.proposed",
        "reflag.acknowledged",
        "chain.verified",
        "outcome.delivered",
        "eval.recorded",
        # live wiring (ADR-28; routing in docs/architecture/event-catalogue.md)
        "harness.step",  # H1 -> H1: HTTP-side work, run outside the Mongo txn
        "pack.released",  # S5 -> H1, S1
        "vote.tallied",  # S5 -> H1: the tally, votes revealed together
        "committee.question_asked",  # S5 -> H1
        "committee.answered",  # S5 -> H1
        "case.clarification_sent",  # S1 -> H1
        "case.control_planned",  # S1 -> H1
        "jurisdiction.list_published",  # S3 -> H1 (watchlist)
        "examiner.asked",  # S4 -> H1 (examiner)
        # a clause the analyst adds from a corpus search (ADR-74)
        "case.clause_added",  # S1 -> H1
        "case.clause_removed",  # S1 -> H1
        "clause.added",  # the audit record H1 writes for it
        "clause.removed",  # the audit record H1 writes for its removal
        # ad-hoc questions to the owner (ADR-73): the events S1 publishes and the
        # records H1's steps write for them
        "case.question_asked",  # S1 -> H1
        "case.question_answered",  # S1 -> H1
        "question.asked",
        "question.answered",
    }
)
