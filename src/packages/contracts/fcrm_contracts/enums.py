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
    """What a review_item (Gate A, review_item gate) carries."""

    CLAUSE = "clause"
    FACTOR = "factor"
    CONTROL = "control"
    CRITIC_FINDING = "critic_finding"


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


class VectorBackend(StrEnum):
    S3VECTORS = "s3vectors"
    MEMORY = "memory"
