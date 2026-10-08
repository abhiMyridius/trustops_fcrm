"""Shared enumerations.

Every service, worker and the web client import these rather than redefining
string literals. See BUILD-PLAN.md §3 and design A §3.1 for the case lifecycle,
and the decision log (ADR-01..21) for the choices behind each value set.
"""

from __future__ import annotations

from enum import StrEnum


class CaseState(StrEnum):
    """Caseflow's state machine (design A §3.1). Sub-states are run-snapshot
    flags (fast_lane, round_open, locked, pack_version, vote_round), not
    enum values.
    """

    INTAKE = "intake"
    REVIEW_AND_SUBMIT = "review_and_submit"
    TRIAGE = "triage"
    SCREENING = "screening"
    ASSESS = "assess"
    CRITIC = "critic"
    COMPLETENESS = "completeness"
    CLARIFICATION = "clarification"
    REVIEW = "review"
    RECOMMEND = "recommend"
    DECIDE = "decide"
    CLOSED = "closed"
    HANDOFF = "handoff"


class GateType(StrEnum):
    """Gate subjects opened by Guard 7 (human_gate). See gates-register.md."""

    PO_CONFIRM_CATEGORY = "po_confirm_category"
    PO_CONFIRM_VALUE = "po_confirm_value"
    PO_REUSE = "po_reuse"
    PO_ANSWER_GAPS = "po_answer_gaps"
    FAST_LANE_CONFIRM = "fast_lane_confirm"
    REVIEW_ITEM = "review_item"
    ACK = "ack"
    COMPLETENESS = "completeness"
    LOCK = "lock"
    PACK_RELEASE = "pack_release"
    VOTE = "vote"
    REFLAG_ACK = "reflag_ack"
    HANDOFF = "handoff"
    UNLOCK = "unlock"


class GateStatus(StrEnum):
    OPEN = "open"
    DECIDED = "decided"
    EXPIRED = "expired"


class Outcome(StrEnum):
    """Four outcomes required by the brief; no fifth value, ever (rule: nothing
    is approved or rejected automatically).
    """

    APPROVE = "approve"
    APPROVE_WITH_CONDITIONS = "approve_with_conditions"
    DEFER = "defer"
    REJECT = "reject"


class VoteOption(StrEnum):
    """Mirrors Outcome; kept distinct so a cast vote and a tallied outcome are
    never structurally confusable.
    """

    APPROVE = "approve"
    APPROVE_WITH_CONDITIONS = "approve_with_conditions"
    DEFER = "defer"
    REJECT = "reject"


class ChangeType(StrEnum):
    NEW_PRODUCT = "new_product"
    NEW_FEATURE = "new_feature"
    PROCESS_CHANGE = "process_change"
    VENDOR_ONBOARDING = "vendor_onboarding"
    NEW_GEOGRAPHY = "new_geography"
    NEW_SEGMENT = "new_segment"


class Domain(StrEnum):
    """The four FFIEC risk domains (scoring v3 / design A §5)."""

    CUSTOMER = "customer"
    PRODUCT = "product"
    GEOGRAPHY = "geography"
    CHANNEL = "channel"


class Band(StrEnum):
    BAND_1 = "1"
    BAND_2 = "2"
    BAND_3 = "3"
    BAND_4 = "4"


class Route(StrEnum):
    ANALYST = "analyst"
    COMMITTEE = "committee"


class ItemKind(StrEnum):
    """What an S1 ``review_item`` carries. ``clause``, ``factor``, ``control``
    and ``critic_finding`` sit behind Gate A (the analyst's ``review_item``
    gates); ``category``, ``value`` and ``gap`` are the intake items the PO
    decides (``po_confirm_category``, ``po_confirm_value``, ``po_answer_gaps``).
    """

    CLAUSE = "clause"
    FACTOR = "factor"
    CONTROL = "control"
    CRITIC_FINDING = "critic_finding"
    CATEGORY = "category"
    VALUE = "value"
    GAP = "gap"


class ItemStatus(StrEnum):
    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    OVERRIDDEN = "overridden"
    REMOVED = "removed"
    EXPLAINED = "explained"
    ADJUSTED = "adjusted"


class Role(StrEnum):
    PO = "fcrm_po"
    ANALYST = "fcrm_analyst"
    COMMITTEE = "fcrm_committee"
    OBSERVER = "fcrm_observer"
    SERVICE = "fcrm_service"


class Tier(StrEnum):
    """Model tiers. Model ids are never here — only in ai/models/tiers.yaml
    (ADR-04); this enum is the tier *name* agents and budgets key on.
    """

    CHEAP = "cheap"
    MID = "mid"
    STRONG = "strong"
    JUDGE = "judge"


class AgentName(StrEnum):
    SCREENING = "screening"
    INTAKE = "intake"
    RESEARCH = "research"
    ASSESSMENT = "assessment"
    CRITIC = "critic"
    NARRATIVE = "narrative"
    EXAMINER = "examiner"
    WATCHLIST = "watchlist"
    POLICY_GAP = "policy_gap"


class StepKind(StrEnum):
    """What one ``harness.step`` message asks H1 to do (ADR-28).

    A trigger that needs HTTP work (S1/S2/S3/S4/S5 calls, audit records)
    cannot run inside the consumer's Mongo transaction, so the engine
    enqueues a ``harness.step`` carrying one of these kinds in that same
    transaction; ``caseflow.steps`` runs the matching handler outside it
    and finishes with ``CaseflowEngine.complete_step``. The names are
    ``<area>.<verb>``; the audit records each kind writes are documented in
    ``fcrm_caseflow.audit_map.RECORDS_FOR``.
    """

    INTAKE_OPEN = "intake.open"
    INTAKE_DOCUMENT_STORED = "intake.document_stored"
    INTAKE_INGESTED = "intake.ingested"
    INTAKE_INGEST_FAILED = "intake.ingest_failed"
    INTAKE_PUBLISH = "intake.publish"
    INTAKE_FIELDSET_RESOLVED = "intake.fieldset_resolved"
    INTAKE_PO_ACTION = "intake.po_action"
    INTAKE_TYPES_CHANGED = "intake.types_changed"
    SUBMIT_RECORD = "submit.record"
    TRIAGE_ASSIGN = "triage.assign"
    SCREENING_PUBLISH = "screening.publish"
    ASSESS_OPEN = "assess.open"
    ASSESS_PUBLISH = "assess.publish"
    CRITIC_PUBLISH = "critic.publish"
    COMPLETENESS_PUBLISH = "completeness.publish"
    CLARIFICATION_RECORD = "clarification.record"
    RERUN_RECORD = "rerun.record"
    RERUN_PUBLISH = "rerun.publish"
    REVIEW_ITEM = "review.item"
    REVIEW_ACK = "review.ack"
    GAP_RECORD = "gap.record"
    CONTROL_PLANNED = "control.planned"
    RETAG_RECORD = "retag.record"
    RERUN_REQUESTED = "rerun.requested"
    #: The analyst added a clause from a corpus search, or removed one they added
    #: (ADR-74): ``clause.added`` / ``clause.removed`` on the chain, no state change.
    CLAUSE_RECORD = "clause.record"
    NARRATIVE_PUBLISH = "narrative.publish"
    LOCK_BUILD_PACK = "lock.build_pack"
    PACK_APPROVED = "pack.approved"
    UNLOCK_SUPERSEDE = "unlock.supersede"
    VOTE_RECORD = "vote.record"
    COMMITTEE_QUESTION = "committee.question"
    COMMITTEE_ANSWER = "committee.answer"
    DECIDE_DELEGATED = "decide.delegated"
    DECIDE_OUTCOME = "decide.outcome"
    DECIDE_NO_MAJORITY = "decide.no_majority"
    CLOSED_FINALIZE = "closed.finalize"
    HANDOFF_OPEN = "handoff.open"
    EXAMINER_ANSWER = "examiner.answer"
    WATCHLIST_PUBLISH = "watchlist.publish"
    REFLAG_ACK = "reflag.ack"
    STATE_PUSH = "state.push"


class ClarificationStatus(StrEnum):
    AWAITING_RESPONSE = "awaiting-response"
    ANSWERED = "answered"
    RESOLVED = "resolved"


class ControlStaleness(StrEnum):
    FRESH = "fresh"
    AGING = "aging"
    STALE = "stale"
    EXPIRED = "expired"


class LlmMode(StrEnum):
    """ADR-12: deterministic runtime modes so the stack and the E2E suite
    reproduce the seed's figures with or without AWS.
    """

    LIVE = "live"
    REPLAY = "replay"
    MOCK = "mock"
