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
        "case.rerun_requested",
        "case.gap_recorded",
        "case.locked",
        "case.unlocked",
        "case.closed",
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
        "index.build",
        "index.built",
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
    }
)
