"""Translating one consumed wire event into a :class:`~fcrm_caseflow
.triggers.Trigger` + :class:`~fcrm_caseflow.transitions.TransitionContext`
pair for :func:`fcrm_caseflow.transitions.apply`.

This is the module where the D-15 / failure-log watch-item gets resolved,
and where several *more* of the same shape turned up while building this
(documented below, not fixed elsewhere — see the package report). The
short version: `Trigger`'s own wire values
(:mod:`fcrm_caseflow.triggers`) are frequently more specific than
``fcrm_contracts.events.catalogue.EVENT_TYPES`` — the catalogue has one
generic ``agent.done``, ``gate.decided``, etc.; the trigger table has one
value *per case* (``agent.done.intake``, ``gate.decided.review_item``, ...)
because the pure state machine needs to react differently depending on
*which* agent/gate/outcome an otherwise-generic event carries. Deriving
the specific trigger from the event's type plus its payload and the run's
current state (never from the wire ``type`` string alone) is therefore the
general pattern here, not a one-off fix for ``agent.failed``:

- **``agent.failed`` -> ``AGENT_FAILED_AFTER_RETRY`` (D-15).** Resolved by
  always mapping the wire type ``"agent.failed"`` to this one trigger,
  unconditionally — not by inspecting a "final attempt" flag on
  :class:`~fcrm_contracts.events.payloads.AgentFailed`, because that
  payload has no such flag (only ``attempt: int``, the attempt count at
  failure, not a boolean). The payload doesn't need one: per
  :mod:`fcrm_agent_worker.handler`'s and
  :mod:`fcrm_agent_worker.live_runtime`'s own module docs, H2 owns its
  *entire* attempt loop internally (``run_agent_live`` runs every retry
  attempt the tier allows before returning) and publishes exactly one
  terminal event per job — ``agent.done`` xor ``agent.failed`` — never an
  interim per-attempt event. There is therefore no non-terminal
  ``agent.failed`` this consumer could ever receive to disambiguate
  against; every one that reaches ``caseflow.agent-results`` already *is*
  "after its one retry" by construction. Hard-coding the unconditional
  mapping is the correct, not merely convenient, resolution — adding a
  payload field or a second catalogue entry would encode a distinction
  that cannot actually occur.
- **Wire names versus trigger names (reconciled by ADR-28).** The
  catalogue now carries the spellings S5 and S1 publish: ``pack.released``,
  ``vote.tallied``, ``committee.question_asked``, ``committee.answered``,
  ``case.clarification_sent``, ``case.control_planned``. Two of them are
  *bare* events whose trigger depends on the payload, which this module
  resolves: ``committee.answered`` by ``numbers_changed`` (R6: changed
  numbers send the case back to review) and ``outcome.set`` by ``mode``
  (only ``delegated`` moves the run; a committee outcome already moved it
  on ``vote.tallied``). The older spellings (``committee.question``,
  ``vote.revealed``) are still accepted defensively. ``case.gap_recorded``
  is the trigger for the S1 event of that name and is not the
  ``gap.recorded`` *record* this package writes after handling it.
- **Guard keys come from the wire, derived facts do not.** A guard fact a
  producer owns (``four_submit_checks_passed``, ``release_checks_passed``,
  ``vote_outcome``, ``band``, ...; :data:`WIRE_CONTEXT_FIELDS`) is read from
  a top-level payload key. A fact H1 derives from its own gates collection
  (:data:`H1_DERIVED_CONTEXT_FIELDS`: ``acks_done``, ``all_items_decided``,
  ``gaps_open``, ...) is never accepted from a payload: no event may assert
  that every acknowledgement is done.
- **Guard 7 and the missing ``Principal``.** ``gate.decided``'s payload
  (:class:`fcrm_contracts.common.GateDecision`) carries only
  ``decided_by: str`` — no roles, no token type. Design A §3.2 says H1
  "refuses decisions from the wrong role", but nothing on the wire today
  lets it actually check that independently of the service that published
  the event (S1/S5, which already authenticated the original HTTP
  request). :mod:`fcrm_caseflow.gates` synthesizes a trivially-passing
  :class:`~fcrm_contracts.common.Principal` from the gate's own
  ``required_role`` so :func:`~fcrm_guardrails.human_gate
  .validate_gate_decision`'s *content* checks (gate identity/status match,
  reason-required rules) still run for real, while its identity checks
  degrade to a no-op until ``GateDecision``/``Envelope`` carry an
  authenticated principal — flagged here, not fixed in ``fcrm_contracts``.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any, Final, Optional

from fcrm_contracts.enums import AgentName, CaseState, GateType
from fcrm_contracts.events.envelope import Envelope
from fcrm_contracts.events.payloads import AgentFailed

from fcrm_caseflow.transitions import TransitionContext
from fcrm_caseflow.triggers import Trigger

#: Wire event types whose ``Trigger`` is a direct, state-independent 1:1
#: mapping (the trigger's own wire value, documented above where it
#: diverges from ``fcrm_contracts.events.catalogue.EVENT_TYPES``).
_LITERAL_TRIGGERS: Final[dict[str, Trigger]] = {
    Trigger.CASE_DOCUMENT_STORED.value: Trigger.CASE_DOCUMENT_STORED,
    Trigger.CASE_SUBMITTED.value: Trigger.CASE_SUBMITTED,
    Trigger.CASE_ANSWERED.value: Trigger.CASE_ANSWERED,
    Trigger.CASE_RETAGGED.value: Trigger.CASE_RETAGGED,
    Trigger.CASE_RERUN_REQUESTED.value: Trigger.CASE_RERUN_REQUESTED,
    Trigger.CASE_GAP_RECORDED.value: Trigger.CASE_GAP_RECORDED,
    Trigger.CASE_CONTROL_PLANNED.value: Trigger.CASE_CONTROL_PLANNED,
    Trigger.CASE_QUESTION_ASKED.value: Trigger.CASE_QUESTION_ASKED,
    Trigger.CASE_QUESTION_ANSWERED.value: Trigger.CASE_QUESTION_ANSWERED,
    Trigger.INGEST_DONE.value: Trigger.INGEST_DONE,
    Trigger.INGEST_FAILED.value: Trigger.INGEST_FAILED,
    Trigger.CASE_LOCKED.value: Trigger.CASE_LOCKED,
    Trigger.CASE_UNLOCKED.value: Trigger.CASE_UNLOCKED,
    Trigger.CASE_CLOSE_REQUESTED.value: Trigger.CASE_CLOSE_REQUESTED,
    Trigger.VOTE_CAST.value: Trigger.VOTE_CAST,
    Trigger.PACK_RELEASED.value: Trigger.PACK_RELEASED,
    Trigger.COMMITTEE_QUESTION_ASKED.value: Trigger.COMMITTEE_QUESTION_ASKED,
    Trigger.COMMITTEE_ANSWERED_NO_CHANGE.value: Trigger.COMMITTEE_ANSWERED_NO_CHANGE,
    Trigger.COMMITTEE_ANSWERED_CHANGED.value: Trigger.COMMITTEE_ANSWERED_CHANGED,
    Trigger.OUTCOME_SET_DELEGATED.value: Trigger.OUTCOME_SET_DELEGATED,
    # Catalogue spellings tolerated defensively alongside the trigger's own
    # (see module docstring): a real S5 may publish either until
    # reconciled.
    "committee.question": Trigger.COMMITTEE_QUESTION_ASKED,
    "vote.revealed": Trigger.VOTE_TALLIED,
    "vote.tallied": Trigger.VOTE_TALLIED,
    Trigger.VOTE_NO_MAJORITY.value: Trigger.VOTE_NO_MAJORITY,
}

#: ``agent.done`` while the run sits in this state, for a single-agent
#: step, maps to this trigger when the payload's ``agent`` matches.
_AGENT_DONE_SINGLE: Final[dict[CaseState, tuple[AgentName, Trigger]]] = {
    CaseState.INTAKE: (AgentName.INTAKE, Trigger.AGENT_DONE_INTAKE),
    CaseState.SCREENING: (AgentName.SCREENING, Trigger.AGENT_DONE_SCREENING),
    CaseState.CRITIC: (AgentName.CRITIC, Trigger.AGENT_DONE_CRITIC),
    CaseState.COMPLETENESS: (
        AgentName.NARRATIVE,
        Trigger.AGENT_DONE_NARRATIVE_COMPLETENESS,
    ),
    CaseState.RECOMMEND: (
        AgentName.NARRATIVE,
        Trigger.AGENT_DONE_NARRATIVE_RECOMMENDATION,
    ),
}

#: ``agent.done`` while the run sits in this state, for a two-agent step
#: (research + assessment, run in parallel), maps to this trigger once
#: *every* agent in the set has reported done (join semantics live in
#: :mod:`fcrm_caseflow.engine`, which tracks the pending set in Mongo —
#: this table only says which agents the join is over).
_AGENT_DONE_PAIR: Final[dict[CaseState, tuple[frozenset[AgentName], Trigger]]] = {
    CaseState.ASSESS: (
        frozenset({AgentName.RESEARCH, AgentName.ASSESSMENT}),
        Trigger.AGENT_DONE_ASSESS_BOTH,
    ),
    CaseState.CLARIFICATION: (
        frozenset({AgentName.RESEARCH, AgentName.ASSESSMENT}),
        Trigger.AGENT_DONE_RERUN_BOTH,
    ),
    # A re-run the analyst asks for in REVIEW (or the new domain of a re-tag).
    CaseState.REVIEW: (
        frozenset({AgentName.RESEARCH, AgentName.ASSESSMENT}),
        Trigger.AGENT_DONE_RERUN_BOTH,
    ),
}

#: ``gate.decided`` for this ``GateType`` always maps to this trigger,
#: with no further disambiguation needed on the decision string.
_GATE_DECIDED_DIRECT: Final[dict[GateType, Trigger]] = {
    GateType.FAST_LANE_CONFIRM: Trigger.GATE_DECIDED_FAST_LANE_CONFIRM,
    GateType.REVIEW_ITEM: Trigger.GATE_DECIDED_REVIEW_ITEM,
    GateType.HANDOFF: Trigger.GATE_DECIDED_HANDOFF,
}

#: ``gate.decided`` for ``GateType.COMPLETENESS``, keyed by the decision
#: string the analyst's route posts (gates-register.md: "ask / proceed").
_COMPLETENESS_DECISIONS: Final[dict[str, Trigger]] = {
    "ask": Trigger.GATE_DECIDED_COMPLETENESS_ASK,
    "proceed": Trigger.GATE_DECIDED_COMPLETENESS_PROCEED,
}

#: ``gate_type`` values whose decision only ever updates the ``gates``
#: collection (:mod:`fcrm_caseflow.gates`) — no ``Trigger`` exists for
#: them in :mod:`fcrm_caseflow.triggers` because they never drive a state
#: transition on their own (``ack``'s effect is read later, as
#: ``ctx.acks_done``, by whichever trigger's guard needs it; ``vote``,
#: ``lock``, ``pack_release`` and ``unlock`` are mirrored by dedicated
#: case-lifecycle events instead — ``vote.cast``, ``case.locked``,
#: ``pack.released``, ``case.unlocked`` — so a generic ``gate.decided``
#: never arrives for them in a well-formed system).
_GATE_DECIDED_INFORMATIONAL_ONLY: Final[frozenset[GateType]] = frozenset(
    {
        GateType.PO_CONFIRM_VALUE,
        GateType.PO_REUSE,
        GateType.PO_ANSWER_GAPS,
        GateType.ACK,
        GateType.VOTE,
        GateType.LOCK,
        GateType.PACK_RELEASE,
        GateType.UNLOCK,
        GateType.REFLAG_ACK,
    }
)

#: :class:`~fcrm_caseflow.transitions.TransitionContext` fields a producer
#: owns and puts at the **top level** of its event payload. A missing key
#: silently takes the dataclass default, so every payload that must drive a
#: guard declares its key as a field in :mod:`fcrm_contracts.events.payloads`
#: and ``tests/unit/workers/caseflow/test_payload_context_keys.py`` fails if
#: one is not declared anywhere. Which model declares which key:
#:
#: - ``four_submit_checks_passed``: ``CaseSubmitted``
#: - ``release_checks_passed``: ``PackReleased``
#: - ``vote_outcome``: ``VoteTallied``
#: - ``rerun_reason``: ``CaseRerunRequested``
#: - ``unlock_reason``: ``CaseUnlocked``, ``CommitteeAnswered``
#: - ``committee_numbers_changed``: ``CommitteeAnswered``
#: - ``band``: ``CaseCloseRequested``
#: - ``fast_lane_proposed``: ``AgentDone`` (a copy of the screening proposal)
#: - ``rating_or_control_changed``, ``band_changed``, ``boundary_flagged``:
#:   ``GateDecided``
WIRE_CONTEXT_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "four_submit_checks_passed",
        "fast_lane_proposed",
        "rating_or_control_changed",
        "band_changed",
        "boundary_flagged",
        "release_checks_passed",
        "rerun_reason",
        "unlock_reason",
        "committee_numbers_changed",
        "vote_outcome",
        "band",
    }
)

#: Context fields H1 derives itself and never takes from a payload: the open
#: gate counts (:meth:`fcrm_caseflow.engine.CaseflowEngine._refresh_gate_
#: derived_ctx`) and the fast-lane decision (read off the gate's own
#: ``decision`` string). ``resume_state`` is the run's own ``suspended_from``.
#: ``all_po_gates_decided`` is reserved: no guard reads
#: it today. A payload carrying one of these is ignored, so no event can
#: assert, say, that every acknowledgement is done.
H1_DERIVED_CONTEXT_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "all_po_gates_decided",
        "fast_lane_confirmed",
        "gaps_open",
        "all_items_decided",
        "acks_done",
        "resume_state",
    }
)

_CONTEXT_PAYLOAD_FIELDS: Final[frozenset[str]] = WIRE_CONTEXT_FIELDS


def context_from_payload(payload: dict[str, Any]) -> TransitionContext:
    """Pick out whichever producer-owned :class:`TransitionContext` fields
    (:data:`WIRE_CONTEXT_FIELDS`) ``payload`` carries (by name), leaving every
    other field at the pure dataclass's own default. A key whose value is
    ``None`` counts as absent (a model that declares an optional guard key
    dumps ``None``, which must not overwrite a default). ``flags`` and the
    :data:`H1_DERIVED_CONTEXT_FIELDS` are never sourced this way: ``flags``
    comes from the persisted run snapshot (:mod:`fcrm_caseflow.engine`),
    the rest from H1's own gates.
    """
    overrides = {
        k: v
        for k, v in payload.items()
        if k in _CONTEXT_PAYLOAD_FIELDS and v is not None
    }
    return TransitionContext(**overrides)


def _strict_flag(payload: dict[str, Any], *keys: str) -> bool:
    """The first of ``keys`` present in ``payload``, as a real boolean.

    ``bool("false")`` is ``True``, which would unlock a locked case on a
    stringly-typed flag, so anything but a JSON boolean is refused.

    Raises:
        ValueError: a present value is not a boolean.
    """
    for key in keys:
        if key in payload and payload[key] is not None:
            value = payload[key]
            if not isinstance(value, bool):
                raise ValueError(f"{key} must be a boolean, got {value!r}")
            return value
    return False


@dataclass(frozen=True)
class SimpleResolution:
    """A wire event that maps to exactly one trigger with no further I/O
    (no gate lookup, no pending-agent join) — everything
    :mod:`fcrm_caseflow.engine` can `apply()` immediately.
    """

    trigger: Trigger
    context: TransitionContext


def resolve_simple(envelope: Envelope) -> Optional[SimpleResolution]:
    """The literal-mapping and ``agent.failed`` cases — the ones that need
    nothing but the envelope itself. Returns ``None`` for anything that
    needs the run's current state (``agent.done``) or a gate lookup
    (``gate.decided``), which :mod:`fcrm_caseflow.engine` resolves instead.
    """
    if envelope.type == "agent.failed":
        # D-15: see module docstring. Unconditional by construction.
        # Parsed (not just referenced) so a malformed AgentFailed payload
        # is poison here, same as everywhere else this layer validates a
        # payload shape before trusting it.
        AgentFailed.model_validate(envelope.payload)
        return SimpleResolution(
            trigger=Trigger.AGENT_FAILED_AFTER_RETRY, context=TransitionContext()
        )
    if envelope.type == "committee.answered":
        return _resolve_committee_answered(envelope.payload)
    if envelope.type == "outcome.set":
        return _resolve_outcome_set(envelope.payload)
    trigger = _LITERAL_TRIGGERS.get(envelope.type)
    if trigger is None:
        return None
    context = context_from_payload(envelope.payload)
    if trigger is Trigger.VOTE_TALLIED and context.vote_outcome is None:
        # design A §4.3 names the tally's key ``outcome``; the guard context
        # reads ``vote_outcome``. Accept either spelling from a producer.
        context = dataclasses.replace(
            context, vote_outcome=envelope.payload.get("outcome")
        )
    return SimpleResolution(trigger=trigger, context=context)


def _resolve_committee_answered(payload: dict[str, Any]) -> SimpleResolution:
    """Bare ``committee.answered``: ``numbers_changed`` (or its mirror
    ``committee_numbers_changed``) picks between the two triggers. R6: changed
    numbers unlock the recommendation (back to REVIEW); unchanged numbers only
    attach the answer to the pack as an addendum.

    Raises:
        ValueError: the flag is present but not a boolean.
    """
    changed = _strict_flag(payload, "numbers_changed", "committee_numbers_changed")
    trigger = (
        Trigger.COMMITTEE_ANSWERED_CHANGED
        if changed
        else Trigger.COMMITTEE_ANSWERED_NO_CHANGE
    )
    context = dataclasses.replace(
        context_from_payload(payload), committee_numbers_changed=changed
    )
    return SimpleResolution(trigger=trigger, context=context)


def _resolve_outcome_set(payload: dict[str, Any]) -> Optional[SimpleResolution]:
    """Bare ``outcome.set``: only ``mode == "delegated"`` moves the run
    (R1: the analyst's delegated close). A committee outcome already moved it
    on ``vote.tallied``, so any other mode is informational (``None``).
    """
    if payload.get("mode") != "delegated":
        return None
    return SimpleResolution(
        trigger=Trigger.OUTCOME_SET_DELEGATED, context=context_from_payload(payload)
    )


def agent_done_single(state: CaseState, agent: AgentName) -> Optional[Trigger]:
    """The trigger for a single-agent ``agent.done`` in ``state``, or
    ``None`` if ``state``/``agent`` is not a known single-agent step
    (either it's a pair step — see :func:`agent_done_pair_members` — or
    this ``agent.done`` is stale/unexpected for the run's current state).
    """
    entry = _AGENT_DONE_SINGLE.get(state)
    if entry is None or entry[0] is not agent:
        return None
    return entry[1]


def agent_done_pair_members(
    state: CaseState,
) -> Optional[tuple[frozenset[AgentName], Trigger]]:
    """The agent set and trigger for a two-agent ``agent.done`` join in
    ``state``, or ``None`` if ``state`` is not a pair step.
    """
    return _AGENT_DONE_PAIR.get(state)


def agent_done_agent(envelope: Envelope) -> AgentName:
    """``envelope.payload["agent"]`` as an :class:`AgentName` — the one
    field this layer needs out of ``agent.done`` to pick a trigger.

    Deliberately not a full ``AgentDone.model_validate`` here: that model
    is ``extra="forbid"``, so a future producer adding ``band``/
    ``boundary_flagged`` alongside it (this layer's own documented,
    opportunistic convention for sourcing those two context fields — see
    module docstring) would make strict validation reject an otherwise
    well-formed message. Full-shape validation belongs to whichever lane
    actually consumes ``output`` (pushing proposals to S4/S1 — out of this
    lane's scope); this function only needs to be precise about
    ``agent``, so it is.

    Raises:
        KeyError: ``agent`` is missing.
        ValueError: ``agent`` is not a known :class:`AgentName`.
    """
    return AgentName(envelope.payload["agent"])


def gate_decided_trigger(gate_type: GateType, decision: str) -> Optional[Trigger]:
    """The trigger for a decided gate of ``gate_type``, or ``None`` when
    this gate type never drives a transition on its own
    (:data:`_GATE_DECIDED_INFORMATIONAL_ONLY`) or needs the "all open
    gates of this type are now decided" aggregation
    (``PO_CONFIRM_CATEGORY`` — :mod:`fcrm_caseflow.engine` calls
    :func:`fcrm_caseflow.gates.GateStore.count_open` for that one, not
    this function). The PO's intake decisions are among the ``None`` ones:
    they move no state, and the engine starts the ``intake.po_action`` step
    for them itself (``CaseflowEngine._po_action_step``).

    Raises:
        ValueError: ``gate_type`` is ``COMPLETENESS`` with a ``decision``
            string that is neither ``"ask"`` nor ``"proceed"`` — a poison
            condition worth surfacing rather than silently dropping.
    """
    direct = _GATE_DECIDED_DIRECT.get(gate_type)
    if direct is not None:
        return direct
    if gate_type is GateType.COMPLETENESS:
        trigger = _COMPLETENESS_DECISIONS.get(decision)
        if trigger is None:
            raise ValueError(
                f"completeness gate decided with unrecognised decision {decision!r}"
            )
        return trigger
    is_informational = gate_type in _GATE_DECIDED_INFORMATIONAL_ONLY
    if is_informational or gate_type is GateType.PO_CONFIRM_CATEGORY:
        # PO_CONFIRM_CATEGORY needs the "all open gates of this type are
        # now decided" aggregation (see docstring); that is resolved by
        # the caller, which has the gates collection, not here.
        return None
    raise ValueError(f"unhandled gate_type for gate.decided: {gate_type!r}")
