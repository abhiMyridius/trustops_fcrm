"""One atomic write of the analyst's, as plain data, and the pure rules behind the
gates the analyst decides (scenes 8-16 and 19).

The service decides *what* an action does (which item takes which status, which
``gate.decided`` or ``case.*`` event announces it) and hands the store ONE
:class:`AnalystCommand`; the store applies all of it in a single transaction (the
decision rows, the ledger of gates decided, the recommendation row, the staged
activity and the outbox events) or none of it. The Mongo store and its
in-memory twin read the same command, so what the unit tests show is what the
database does.

**Store before emit.** Caseflow's ``review.item`` step reads the decided value
from S1's item and re-scores from it. An item's decision therefore has to be
committed in the same transaction that enqueues ``gate.decided``; this module
only shapes both halves, the store commits them together.

The shapes here are what H1 reads (``fcrm_caseflow.cores.assessment_review``):
``before`` and ``after`` of an override are the decided-value dicts
(``{"rating": 7.6}``, ``{"likelihood": 4, "impact": 5}``), the gate's subject is
``{"kind": <item kind>, "id": <item id>}`` and the decision is one of ``accept``,
``confirm``, ``remove``, ``override``, ``explain``, ``adjust``.

Pure, no I/O.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Literal, Optional

from fcrm_common.errors import ConflictError, ValidationError
from fcrm_common.events import build_envelope
from fcrm_contracts.api.case_analyst_write import ItemDecisionBody, RoundQuestion
from fcrm_contracts.api.case_internal import UiActor, UiEventIn
from fcrm_contracts.api.case_internal_assessment import (
    ControlProposed,
    ControlValue,
    FactorProposed,
    FactorValue,
    RecommendationEdits,
)
from fcrm_contracts.enums import (
    AgentName,
    ChangeType,
    GateType,
    ItemKind,
    ItemStatus,
    Role,
)
from fcrm_contracts.events.envelope import Envelope
from fcrm_contracts.events.payloads import (
    CaseClarificationSent,
    CaseLocked,
    CaseUnlocked,
    GateDecided,
)
from pydantic import BaseModel, ConfigDict
from pydantic import ValidationError as PydanticValidationError

from fcrm_case.analyst_errors import DecisionNotAllowedError
from fcrm_case.domain.clarifications import (
    ClarificationThreadRow,
    anchored_field_ids,
    field_label_for,
    thread_id_for,
)
from fcrm_case.domain.po_commands import ItemDecision
from fcrm_case.domain.views import DecisionRow, ReviewItemRow, ScoreSnapshotRow

ANALYST_COMPONENT: Final = "S1"

AckKind = Literal["boundary", "band_change"]

#: What each item kind takes as a decision (the web posts accept/remove for a
#: clause, confirm/override for a control; a critic finding is explained).
ALLOWED_DECISIONS: Final[Mapping[ItemKind, frozenset[str]]] = {
    ItemKind.CLAUSE: frozenset({"accept", "remove"}),
    ItemKind.CONTROL: frozenset({"confirm", "override", "adjust", "remove"}),
    ItemKind.FACTOR: frozenset({"confirm", "override", "adjust"}),
    ItemKind.CRITIC_FINDING: frozenset({"explain"}),
}

#: The agent that proposed each kind of review item (mirrors the drafts in H1's
#: ``fcrm_caseflow.cores.assessment_items``); the item does not store it.
AGENT_FOR_KIND: Final[Mapping[ItemKind, AgentName]] = {
    ItemKind.CLAUSE: AgentName.RESEARCH,
    ItemKind.FACTOR: AgentName.ASSESSMENT,
    ItemKind.CONTROL: AgentName.ASSESSMENT,
    ItemKind.CRITIC_FINDING: AgentName.CRITIC,
}

#: The decisions where the analyst disagreed with the agent's proposal (counted
#: as ``fcrm_override_total``).
OVERRIDING_DECISIONS: Final = frozenset({"override", "adjust", "remove"})

#: The status each decision leaves an item in.
STATUS_FOR: Final[Mapping[str, ItemStatus]] = {
    "accept": ItemStatus.CONFIRMED,
    "confirm": ItemStatus.CONFIRMED,
    "remove": ItemStatus.REMOVED,
    "override": ItemStatus.OVERRIDDEN,
    "explain": ItemStatus.EXPLAINED,
    "adjust": ItemStatus.ADJUSTED,
}

_REPLACING: Final = frozenset({"override", "adjust"})
_VERBS: Final[Mapping[str, str]] = {
    "accept": "accepted",
    "confirm": "confirmed",
    "remove": "removed",
    "override": "overrode",
    "explain": "explained",
    "adjust": "adjusted",
}


class AnalystDecisionRow(BaseModel):
    """The ledger of gates the analyst decided (``analyst_decisions``, ``_id =
    gate_id``): the first decision on a gate emits ``gate.decided``; a repeat
    with the same ``fingerprint`` replays it, a different one is refused.
    """

    model_config = ConfigDict(extra="forbid")

    gate_id: str
    case_id: str
    gate_type: str
    decision: str
    fingerprint: str
    decided_by: str
    decided_at: datetime


@dataclass(frozen=True)
class AnalystGate:
    """A ``gate.decided`` event, enqueued only the first time its gate is decided.

    ``gate_id`` is the id Caseflow opened the gate with (read from the item or
    view that carries it, never recomputed); the store records it in the ledger
    and enqueues ``event`` when the row is new.
    """

    gate_id: str
    gate_type: GateType
    decision: str
    fingerprint: str
    event: Envelope


@dataclass(frozen=True)
class RecommendationChange:
    """A change to the case's recommendation row.

    ``kind`` ``edit`` replaces the stored ``edits`` (refused once locked);
    ``lock`` and ``unlock`` flip the lock, and are replays (nothing else of the
    command is applied) when the recommendation is already in that state.
    """

    kind: Literal["edit", "lock", "unlock"]
    by: str
    at: datetime
    edits: Optional[RecommendationEdits] = None
    reason: Optional[str] = None


@dataclass(frozen=True)
class OnceRecord:
    """A row to insert once (a planned control, a gap, a re-run request). Its id is
    derived from its content, so a row that already exists makes the whole command
    a replay: nothing else is applied and no event is enqueued.
    """

    collection: str
    row_id: str
    document: Mapping[str, Any]


@dataclass(frozen=True)
class Retag:
    """The analyst adds a change type: the store resolves the fields and domains
    that follow in the same transaction.
    """

    change_type: ChangeType


@dataclass(frozen=True)
class ThreadResolution:
    """The analyst resolved an answered question."""

    thread_id: str
    by: str
    at: datetime
    note: Optional[str] = None


@dataclass(frozen=True)
class AnalystCommand:
    """Everything one analyst action changes, applied together.

    Attributes:
        case_id: The case.
        actor: The analyst's user id (who the ledger names).
        gates: ``gate.decided`` events, each emitted at most once per gate. A
            command that has gates and decides none of them new is a replay: it
            changes nothing and enqueues nothing.
        items: Review items to decide (status and decided value).
        events: Events enqueued unconditionally on a first application
            (``case.locked``, ``case.gap_recorded``...).
        activity: Activity rows the analyst feed shows, staged in the same
            transaction.
        recommendation: A change to the recommendation row (edit, lock, unlock).
        threads: The questions of a round to open, inserted with the gate decision
            that asks them.
        resolutions: Answered questions the analyst resolves.
        records: Rows to insert once (planned controls, gaps, re-run requests).
        retag: A change type to add to the case.
    """

    case_id: str
    actor: str
    gates: Sequence[AnalystGate] = ()
    items: Sequence[ItemDecision] = ()
    events: Sequence[Envelope] = ()
    activity: Sequence[UiEventIn] = ()
    recommendation: Optional[RecommendationChange] = None
    threads: Sequence[ClarificationThreadRow] = ()
    resolutions: Sequence[ThreadResolution] = ()
    records: Sequence[OnceRecord] = ()
    retag: Optional[Retag] = None


def decision_fingerprint(*parts: Any) -> str:
    """sha256 of ``parts`` as canonical JSON: two decisions with one fingerprint
    say the same thing, so a retry replays and a different decision is a 409.
    """
    canonical = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def analyst_actor(sub: str) -> UiActor:
    """The analyst as an activity actor."""
    return UiActor(id=sub, role=Role.ANALYST.value, component=ANALYST_COMPONENT)


def activity_row(
    sub: str, type_: str, summary: str, detail: Optional[dict[str, Any]] = None
) -> UiEventIn:
    """An activity row only staff see (visibility ``analyst``)."""
    return UiEventIn(
        type=type_,
        visibility="analyst",
        summary=summary,
        detail=detail or {},
        actor=analyst_actor(sub),
    )


def gate_envelope(
    *,
    case_id: str,
    run_id: str,
    gate_id: str,
    gate_type: GateType,
    decision: str,
    decided_by: str,
    decided_at: datetime,
    subject: Mapping[str, str],
    reason: Optional[str] = None,
    before: Optional[dict[str, Any]] = None,
    after: Optional[dict[str, Any]] = None,
    item_kind: Optional[ItemKind] = None,
    changed: bool = False,
) -> Envelope:
    """The ``gate.decided`` event for the gate Caseflow opened as ``gate_id``,
    in the shape ``GateDecided`` and H1's handlers read.
    """
    payload = GateDecided(
        gate_id=gate_id,
        gate_type=gate_type,
        case_id=case_id,
        run_id=run_id,
        decision=decision,
        reason=reason,
        decided_by=decided_by,
        decided_at=decided_at,
        before=before,
        after=after,
        role=Role.ANALYST,
        subject=dict(subject),
        item_kind=item_kind,
        rating_or_control_changed=changed,
    )
    return build_envelope(
        "gate.decided",
        payload.model_dump(mode="json"),
        case_id=case_id,
        run_id=run_id,
    )


# ── a review item's decision ────────────────────────────────────────────────────


@dataclass(frozen=True)
class PlannedDecision:
    """What deciding one item does, before it is stored.

    Attributes:
        status: Where the item stands afterwards.
        value: ``decided.value``: the replacement a person set, or ``None``.
        before: The decided-value shape of what was proposed (override, adjust,
            or the removal of a scored control), or ``None``.
        after: The decided-value shape of the replacement, or ``None``.
        changed: Whether the decision changes an input S2 reads (Caseflow
            re-scores when it does).
        label: The clause or control id, or the domain of a rating, for the
            activity row.
    """

    status: ItemStatus
    value: Optional[dict[str, Any]]
    before: Optional[dict[str, Any]]
    after: Optional[dict[str, Any]]
    changed: bool
    label: str


def _refuse_item(row: ReviewItemRow, what: str) -> ConflictError:
    return ConflictError(
        f"review item {row.item_id} does not carry the shape its kind promises "
        f"({what})",
        item_id=row.item_id,
    )


def _control_plan(row: ReviewItemRow, body: ItemDecisionBody) -> PlannedDecision:
    try:
        proposed = ControlProposed.model_validate(row.proposed)
    except PydanticValidationError as exc:
        raise _refuse_item(row, "control") from exc
    control_id = str(row.subject.get("control_id", row.item_id))
    planned = row.subject.get("planned") is True
    status = STATUS_FOR[body.decision]
    if body.decision in _REPLACING:
        if planned:
            raise DecisionNotAllowedError(
                f"{control_id} is a planned control: it is recorded and never "
                "scored, so its rating cannot be replaced",
                item_id=row.item_id,
            )
        if body.value is None or body.likelihood is not None or body.impact is not None:
            raise ValidationError(
                "A control is overridden with a rating from 0 to 10.",
                errors=[{"loc": "body.value", "msg": "a rating is required"}],
            )
        if proposed.rating is not None and body.value == proposed.rating:
            raise ValidationError(
                f"{control_id} is already rated {body.value}; confirm it instead.",
                errors=[{"loc": "body.value", "msg": "same as the proposal"}],
            )
        after = ControlValue(rating=body.value).model_dump()
        return PlannedDecision(
            status, after, {"rating": proposed.rating}, after, True, control_id
        )
    if body.decision == "remove":
        before = None if proposed.rating is None else {"rating": proposed.rating}
        return PlannedDecision(status, None, before, None, not planned, control_id)
    return PlannedDecision(status, None, None, None, False, control_id)


def _factor_plan(row: ReviewItemRow, body: ItemDecisionBody) -> PlannedDecision:
    try:
        proposed = FactorProposed.model_validate(row.proposed)
    except PydanticValidationError as exc:
        raise _refuse_item(row, "rating") from exc
    domain = str(row.subject.get("domain", row.item_id))
    status = STATUS_FOR[body.decision]
    if body.decision not in _REPLACING:
        return PlannedDecision(status, None, None, None, False, domain)
    if body.likelihood is None or body.impact is None or body.value is not None:
        raise ValidationError(
            "A rating is overridden with a likelihood and an impact from 1 to 5.",
            errors=[{"loc": "body.likelihood", "msg": "likelihood and impact needed"}],
        )
    if (body.likelihood, body.impact) == (proposed.likelihood, proposed.impact):
        raise ValidationError(
            f"{domain} is already L{proposed.likelihood} I{proposed.impact}; "
            "confirm it instead.",
            errors=[{"loc": "body.likelihood", "msg": "same as the proposal"}],
        )
    before = {"likelihood": proposed.likelihood, "impact": proposed.impact}
    after = FactorValue(likelihood=body.likelihood, impact=body.impact).model_dump()
    return PlannedDecision(status, after, before, after, True, domain)


def _other_plan(row: ReviewItemRow, body: ItemDecisionBody) -> PlannedDecision:
    if body.value is not None or body.likelihood is not None or body.impact is not None:
        raise ValidationError(
            "This decision takes no replacement value.",
            errors=[{"loc": "body.value", "msg": "not accepted here"}],
        )
    label = str(row.subject.get("clause_id") or row.subject.get("text") or row.item_id)[
        :80
    ]
    return PlannedDecision(STATUS_FOR[body.decision], None, None, None, False, label)


def plan_item_decision(row: ReviewItemRow, body: ItemDecisionBody) -> PlannedDecision:
    """What ``body`` does to ``row``.

    Raises:
        DecisionNotAllowedError: The kind does not take this decision, or the
            item is a planned control a rating cannot replace.
        fcrm_common.errors.ValidationError: A replacement is missing, mixed up
            or the same as the proposal.
        fcrm_common.errors.ConflictError: The stored item is malformed.
    """
    allowed = ALLOWED_DECISIONS.get(row.kind)
    if allowed is None:
        raise DecisionNotAllowedError(
            f"{row.kind.value} items are decided by their owner, not here",
            item_id=row.item_id,
        )
    if body.decision not in allowed:
        raise DecisionNotAllowedError(
            f"a {row.kind.value} is {' or '.join(sorted(allowed))}, "
            f"not {body.decision}",
            item_id=row.item_id,
        )
    if row.kind is ItemKind.CONTROL:
        return _control_plan(row, body)
    if row.kind is ItemKind.FACTOR:
        return _factor_plan(row, body)
    return _other_plan(row, body)


def item_fingerprint(body: ItemDecisionBody) -> str:
    """The identity of one item decision (what a replay is compared on)."""
    return decision_fingerprint(
        body.decision, body.value, body.likelihood, body.impact, body.reason
    )


def item_summary(sub_name: str, plan: PlannedDecision, decision: str) -> str:
    """The analyst feed's line for a decision on one item."""
    return f"{sub_name} {_VERBS[decision]} {plan.label}"


def decide_item_command(
    *,
    row: ReviewItemRow,
    body: ItemDecisionBody,
    plan: PlannedDecision,
    case_id: str,
    run_id: str,
    gate_id: str,
    by: str,
    by_name: str,
    now: datetime,
) -> AnalystCommand:
    """The command that decides ``row`` as ``body`` says: the item's new status
    and value, and the ``gate.decided review_item`` event, stored together.
    """
    event = gate_envelope(
        case_id=case_id,
        run_id=run_id,
        gate_id=gate_id,
        gate_type=GateType.REVIEW_ITEM,
        decision=body.decision,
        decided_by=by,
        decided_at=now,
        subject={"kind": row.kind.value, "id": row.item_id},
        reason=body.reason,
        before=plan.before,
        after=plan.after,
        item_kind=row.kind,
        changed=plan.changed,
    )
    gate = AnalystGate(
        gate_id, GateType.REVIEW_ITEM, body.decision, item_fingerprint(body), event
    )
    decided = DecisionRow(value=plan.value, reason=body.reason, by=by, at=now)
    detail: dict[str, Any] = {
        "item_id": row.item_id,
        "kind": row.kind.value,
        "decision": body.decision,
    }
    return AnalystCommand(
        case_id=case_id,
        actor=by,
        gates=[gate],
        items=[ItemDecision(row.item_id, plan.status, decided)],
        activity=[
            activity_row(
                by,
                "item.decided",
                item_summary(by_name, plan, body.decision)
                + (" · reason recorded" if body.reason else ""),
                detail,
            )
        ],
    )


# ── acknowledgements (R3) ───────────────────────────────────────────────────────


def ack_kind_of(snapshot: ScoreSnapshotRow) -> AckKind:
    """Which acknowledgement the gate a score snapshot opened is for: a score
    whose band differs from the one it replaces opens ``band_change`` (one gate
    covers the boundary too), any other one the boundary.
    """
    return "band_change" if snapshot.band_changed else "boundary"


def open_ack_snapshots(
    snapshots: Sequence[ScoreSnapshotRow], decided_gate_ids: frozenset[str]
) -> list[ScoreSnapshotRow]:
    """The snapshots whose acknowledgement gate is still open (it has one and
    S1's ledger has no decision for it), oldest first.
    """
    rows = [
        s
        for s in snapshots
        if s.gate_id is not None and s.gate_id not in decided_gate_ids
    ]
    return sorted(rows, key=lambda s: (s.computed_at, s.snapshot_id))


def ack_command(
    *,
    case_id: str,
    kind: AckKind,
    note: Optional[str],
    gates: Sequence[ScoreSnapshotRow],
    fallback_run_id: str,
    by: str,
    by_name: str,
    now: datetime,
) -> AnalystCommand:
    """The command that acknowledges every open gate of ``kind`` (``gates`` are
    the snapshots that opened them): one ``gate.decided ack`` each, read from
    the snapshot's own gate id.
    """
    emissions: list[AnalystGate] = []
    for snapshot in gates:
        assert snapshot.gate_id is not None
        event = gate_envelope(
            case_id=case_id,
            run_id=snapshot.run_id or fallback_run_id,
            gate_id=snapshot.gate_id,
            gate_type=GateType.ACK,
            decision="acknowledge",
            decided_by=by,
            decided_at=now,
            subject={"kind": "ack", "id": kind},
            reason=note,
        )
        emissions.append(
            AnalystGate(
                snapshot.gate_id,
                GateType.ACK,
                "acknowledge",
                decision_fingerprint(kind, note),
                event,
            )
        )
    what = "Band change" if kind == "band_change" else "Boundary flag"
    return AnalystCommand(
        case_id=case_id,
        actor=by,
        gates=emissions,
        activity=[
            activity_row(
                by,
                "ack.decided",
                f"{by_name} acknowledged: {what.lower()}",
                {"kind": kind, "gates": len(emissions)},
            )
        ],
    )


# ── the fast lane (R4) ──────────────────────────────────────────────────────────


def fast_lane_command(
    *,
    case_id: str,
    run_id: str,
    gate_id: str,
    confirm: bool,
    reason: Optional[str],
    by: str,
    by_name: str,
    now: datetime,
) -> AnalystCommand:
    """The command that decides the ``fast_lane_confirm`` gate. Caseflow reads
    ``confirm`` as confirmed and anything else as declined, so the web's
    ``confirm_fast_lane`` is sent as ``confirm``.
    """
    decision = "confirm" if confirm else "decline"
    event = gate_envelope(
        case_id=case_id,
        run_id=run_id,
        gate_id=gate_id,
        gate_type=GateType.FAST_LANE_CONFIRM,
        decision=decision,
        decided_by=by,
        decided_at=now,
        subject={"kind": "screening", "id": "fast_lane"},
        reason=reason,
    )
    gate = AnalystGate(
        gate_id,
        GateType.FAST_LANE_CONFIRM,
        decision,
        decision_fingerprint(decision, reason),
        event,
    )
    verb = "confirmed the fast lane" if confirm else "declined the fast lane"
    return AnalystCommand(
        case_id=case_id,
        actor=by,
        gates=[gate],
        activity=[activity_row(by, "screening.decided", f"{by_name} {verb}")],
    )


# ── the recommendation: edit, lock, unlock ──────────────────────────────────────


def edit_recommendation_command(
    *,
    case_id: str,
    edits: RecommendationEdits,
    by: str,
    by_name: str,
    now: datetime,
) -> AnalystCommand:
    """The command that stores the analyst's edits of the draft (no reason: an
    edit is the analyst's own text; the lock is the gate).
    """
    return AnalystCommand(
        case_id=case_id,
        actor=by,
        recommendation=RecommendationChange("edit", by, now, edits=edits),
        activity=[
            activity_row(
                by,
                "recommendation.edited",
                f"{by_name} edited the recommendation "
                f"({edits.edit_count} edits in all)",
                {"edit_count": edits.edit_count},
            )
        ],
    )


def lock_command(
    *, case_id: str, run_id: str, by: str, by_name: str, now: datetime
) -> AnalystCommand:
    """The command that locks the recommendation and announces ``case.locked``;
    Caseflow then freezes the numbers into a pack.
    """
    event = build_envelope(
        "case.locked",
        CaseLocked(case_id=case_id, actor=by).model_dump(mode="json"),
        case_id=case_id,
        run_id=run_id,
    )
    return AnalystCommand(
        case_id=case_id,
        actor=by,
        events=[event],
        recommendation=RecommendationChange("lock", by, now),
        activity=[
            activity_row(
                by, "recommendation.locked", f"{by_name} locked the recommendation"
            )
        ],
    )


def unlock_command(
    *, case_id: str, run_id: str, reason: str, by: str, by_name: str, now: datetime
) -> AnalystCommand:
    """The command that unlocks the recommendation with a reason and announces
    ``case.unlocked{unlock_reason}``; Caseflow supersedes the pack and gives every
    item a new gate.
    """
    event = build_envelope(
        "case.unlocked",
        CaseUnlocked(case_id=case_id, actor=by, unlock_reason=reason).model_dump(
            mode="json"
        ),
        case_id=case_id,
        run_id=run_id,
    )
    return AnalystCommand(
        case_id=case_id,
        actor=by,
        events=[event],
        recommendation=RecommendationChange("unlock", by, now, reason=reason),
        activity=[
            activity_row(
                by,
                "recommendation.unlocked",
                f"{by_name} unlocked the recommendation · reason recorded",
            )
        ],
    )


# ── completeness and the clarification round (D-16) ─────────────────────────────

COMPLETENESS_SUBJECT: Final = {"kind": "completeness", "id": "report"}


def po_visible_row(
    sub: str, type_: str, summary: str, detail: Optional[dict[str, Any]] = None
) -> UiEventIn:
    """An activity row the owner also sees, written by the analyst (visibility
    ``po``; the owner's feed names the actor ``FCRM``).
    """
    return UiEventIn(
        type=type_,
        visibility="po",
        summary=summary,
        detail=detail or {},
        actor=analyst_actor(sub),
    )


def round_fingerprint(questions: Sequence[RoundQuestion]) -> str:
    """The identity of a round: its questions, in order."""
    return decision_fingerprint("ask", [(q.id, q.text, q.anchor) for q in questions])


def proceed_command(
    *,
    case_id: str,
    run_id: str,
    gate_id: str,
    by: str,
    by_name: str,
    now: datetime,
) -> AnalystCommand:
    """The command that decides the completeness gate as ``proceed``: no round,
    straight to review.
    """
    event = gate_envelope(
        case_id=case_id,
        run_id=run_id,
        gate_id=gate_id,
        gate_type=GateType.COMPLETENESS,
        decision="proceed",
        decided_by=by,
        decided_at=now,
        subject=COMPLETENESS_SUBJECT,
    )
    gate = AnalystGate(
        gate_id,
        GateType.COMPLETENESS,
        "proceed",
        decision_fingerprint("proceed"),
        event,
    )
    return AnalystCommand(
        case_id=case_id,
        actor=by,
        gates=[gate],
        activity=[
            activity_row(
                by,
                "completeness.decided",
                f"{by_name} proceeded to review without a clarification round",
            )
        ],
    )


def ask_round_command(
    *,
    case_id: str,
    run_id: str,
    gate_id: str,
    round_no: int,
    questions: Sequence[RoundQuestion],
    labels: Mapping[str, str],
    by: str,
    by_name: str,
    now: datetime,
) -> AnalystCommand:
    """The command that asks the owner one round of questions: the threads are
    opened, ``gate.decided completeness{ask}`` carries the round and the number
    of questions (what Caseflow records), and ``case.clarification_sent`` names the
    threads. All of it commits in one transaction.
    """
    threads = [
        ClarificationThreadRow(
            thread_id=thread_id_for(case_id, round_no, q.id),
            case_id=case_id,
            round=round_no,
            question_id=q.id,
            text=q.text,
            anchor=q.anchor,
            field_ids=anchored_field_ids(q.anchor),
            field_label=field_label_for(q.anchor, labels),
            opened_at=now,
            opened_by=by,
        )
        for q in questions
    ]
    decided = gate_envelope(
        case_id=case_id,
        run_id=run_id,
        gate_id=gate_id,
        gate_type=GateType.COMPLETENESS,
        decision="ask",
        decided_by=by,
        decided_at=now,
        subject=COMPLETENESS_SUBJECT,
        after={"round": round_no, "questions": len(threads)},
    )
    gate = AnalystGate(
        gate_id,
        GateType.COMPLETENESS,
        "ask",
        round_fingerprint(questions),
        decided,
    )
    sent = build_envelope(
        "case.clarification_sent",
        CaseClarificationSent(
            case_id=case_id,
            actor=by,
            round=round_no,
            thread_ids=[t.thread_id for t in threads],
            question_count=len(threads),
        ).model_dump(mode="json"),
        case_id=case_id,
        run_id=run_id,
    )
    noun = "question" if len(threads) == 1 else "questions"
    return AnalystCommand(
        case_id=case_id,
        actor=by,
        gates=[gate],
        events=[sent],
        threads=threads,
        activity=[
            activity_row(
                by,
                "clarification.sent",
                f"{by_name} sent round {round_no} to the owner: {len(threads)} {noun}"
                " · analyst clock paused",
                {"round": round_no, "questions": len(threads)},
            ),
            po_visible_row(
                by,
                "clarification.sent",
                f"FCRM has {len(threads)} {noun} for you",
                {"round": round_no},
            ),
        ],
    )


def resolve_thread_command(
    *,
    case_id: str,
    thread: ClarificationThreadRow,
    note: Optional[str],
    by: str,
    by_name: str,
    now: datetime,
) -> AnalystCommand:
    """The command that resolves an answered question."""
    return AnalystCommand(
        case_id=case_id,
        actor=by,
        resolutions=[ThreadResolution(thread.thread_id, by, now, note)],
        activity=[
            activity_row(
                by,
                "thread.resolved",
                f"{by_name} resolved question {thread.question_id} "
                f"(round {thread.round})",
                {"round": thread.round, "thread_id": thread.thread_id},
            )
        ],
    )
