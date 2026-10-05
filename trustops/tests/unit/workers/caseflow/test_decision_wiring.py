"""How the decision core is wired into H1's shared tables: which trigger starts
which step, which triggers move the run (and which, like ``vote.rerequested``,
deliberately do not), and the transitions this lane added for a Band 1-2 case.
"""

from __future__ import annotations

from typing import Any

import pytest
from fcrm_caseflow.audit_map import RECORDS_FOR, STEP_FOR
from fcrm_caseflow.cores import closing, decision, default_handlers
from fcrm_caseflow.exceptions import IllegalTransition
from fcrm_caseflow.resolve import resolve_simple
from fcrm_caseflow.states import RunFlags
from fcrm_caseflow.transitions import TransitionContext, apply
from fcrm_caseflow.triggers import Trigger
from fcrm_common.ids import new_id
from fcrm_common.time import utcnow
from fcrm_contracts.enums import CaseState, StepKind
from fcrm_contracts.events.envelope import Envelope

K = StepKind

DECISION_STEP_FOR = {
    Trigger.VOTE_CAST: K.VOTE_RECORD,
    Trigger.COMMITTEE_QUESTION_ASKED: K.COMMITTEE_QUESTION,
    Trigger.COMMITTEE_ANSWERED_NO_CHANGE: K.COMMITTEE_ANSWER,
    Trigger.COMMITTEE_ANSWERED_CHANGED: K.COMMITTEE_ANSWER,
    Trigger.VOTE_TALLIED: K.DECIDE_OUTCOME,
    Trigger.VOTE_NO_MAJORITY: K.DECIDE_NO_MAJORITY,
    Trigger.CASE_CLOSE_REQUESTED: K.DECIDE_DELEGATED,
    Trigger.CLOSED_DISPATCH: K.CLOSED_FINALIZE,
}


def envelope(event_type: str, payload: dict[str, Any]) -> Envelope:
    return Envelope(
        event_id=new_id("evt"),
        type=event_type,
        case_id="FCRM-2026-0151",
        run_id="run_1",
        occurred_at=utcnow(),
        payload=payload,
    )


def test_the_decision_triggers_start_the_decision_steps() -> None:
    assert DECISION_STEP_FOR.items() <= dict(STEP_FOR).items()


def test_every_decision_and_closing_step_has_a_default_handler() -> None:
    handlers = default_handlers()
    owned = set(decision.HANDLERS) | set(closing.HANDLERS)
    assert owned == set(DECISION_STEP_FOR.values())
    assert owned <= set(handlers)


def test_the_two_cores_claim_different_steps() -> None:
    assert not set(decision.HANDLERS) & set(closing.HANDLERS)


@pytest.mark.parametrize("trigger", sorted(DECISION_STEP_FOR, key=str))
def test_every_decision_trigger_has_a_transition_from_the_state_it_arrives_in(
    trigger: Trigger,
) -> None:
    states = {
        Trigger.CLOSED_DISPATCH: CaseState.CLOSED,
        Trigger.CASE_CLOSE_REQUESTED: CaseState.DECIDE,
    }
    ctx = TransitionContext(
        band=2, committee_numbers_changed=trigger is Trigger.COMMITTEE_ANSWERED_CHANGED
    )
    apply(states.get(trigger, CaseState.DECIDE), trigger, ctx)


def test_vote_rerequested_is_not_a_trigger_and_neither_is_a_committee_outcome() -> None:
    """S5 opened the next round itself, and a committee outcome already moved
    the run on ``vote.tallied``: both are informational for the engine.
    """
    rerequested = {
        "pack_id": "pack_1",
        "version": 1,
        "round": 2,
        "member_ids": ["u-lchen"],
        "reason": "no_majority",
    }
    assert resolve_simple(envelope("vote.rerequested", rerequested)) is None
    committee = {"case_id": "FCRM-2026-0151", "mode": "committee", "outcome": "defer"}
    assert resolve_simple(envelope("outcome.set", committee)) is None


def test_a_delegated_outcome_is_a_trigger() -> None:
    delegated = {"case_id": "FCRM-2026-0151", "mode": "delegated", "outcome": "approve"}
    resolved = resolve_simple(envelope("outcome.set", delegated))
    assert resolved is not None
    assert resolved.trigger is Trigger.OUTCOME_SET_DELEGATED


@pytest.mark.parametrize(
    ("event_type", "payload", "trigger"),
    [
        (
            "vote.cast",
            {
                "pack_id": "p",
                "round": 1,
                "member_id": "u",
                "cast_count": 1,
                "quorum": 3,
            },
            Trigger.VOTE_CAST,
        ),
        (
            "vote.tallied",
            {
                "pack_id": "p",
                "round": 1,
                "outcome": "approve",
                "counts": {"approve": 3},
                "votes": [],
            },
            Trigger.VOTE_TALLIED,
        ),
        (
            "vote.no_majority",
            {"pack_id": "p", "round": 1, "counts": {}},
            Trigger.VOTE_NO_MAJORITY,
        ),
        (
            "committee.question_asked",
            {"pack_id": "p", "thread_id": "t", "member_id": "u", "text": "why"},
            Trigger.COMMITTEE_QUESTION_ASKED,
        ),
        (
            "committee.answered",
            {"pack_id": "p", "thread_id": "t", "by": "u", "numbers_changed": False},
            Trigger.COMMITTEE_ANSWERED_NO_CHANGE,
        ),
        (
            "committee.answered",
            {
                "pack_id": "p",
                "thread_id": "t",
                "by": "u",
                "numbers_changed": True,
                "unlock_reason": "a rating moved after the answer",
            },
            Trigger.COMMITTEE_ANSWERED_CHANGED,
        ),
        (
            "case.close_requested",
            {"case_id": "c", "actor": "u", "outcome": "approve", "band": 2},
            Trigger.CASE_CLOSE_REQUESTED,
        ),
    ],
)
def test_each_wire_event_resolves_to_its_trigger(
    event_type: str, payload: dict[str, Any], trigger: Trigger
) -> None:
    resolved = resolve_simple(envelope(event_type, payload))
    assert resolved is not None
    assert resolved.trigger is trigger


# ── the Band 1-2 rows this lane added ────────────────────────────────────────


def _ctx(
    band: int, *, locked: bool = True, acks_done: bool = True
) -> TransitionContext:
    return TransitionContext(
        band=band, acks_done=acks_done, flags=RunFlags(locked=locked)
    )


@pytest.mark.parametrize("state", [CaseState.RECOMMEND, CaseState.DECIDE])
@pytest.mark.parametrize("band", [1, 2])
def test_a_band_one_or_two_case_may_be_closed_from_recommend_or_decide(
    state: CaseState, band: int
) -> None:
    result = apply(state, Trigger.CASE_CLOSE_REQUESTED, _ctx(band))
    assert result.to_state is state  # S1 and S5 close it; the run moves on outcome.set


@pytest.mark.parametrize("state", [CaseState.RECOMMEND, CaseState.DECIDE])
@pytest.mark.parametrize("band", [3, 4])
def test_a_committee_band_case_cannot_be_closed_by_the_analyst(
    state: CaseState, band: int
) -> None:
    with pytest.raises(IllegalTransition):
        apply(state, Trigger.CASE_CLOSE_REQUESTED, _ctx(band))


def test_an_unlocked_recommendation_cannot_be_closed_from_recommend() -> None:
    with pytest.raises(IllegalTransition):
        apply(CaseState.RECOMMEND, Trigger.CASE_CLOSE_REQUESTED, _ctx(2, locked=False))


def test_a_close_waits_for_the_acknowledgements() -> None:
    with pytest.raises(IllegalTransition):
        apply(
            CaseState.RECOMMEND, Trigger.CASE_CLOSE_REQUESTED, _ctx(2, acks_done=False)
        )


@pytest.mark.parametrize("state", [CaseState.RECOMMEND, CaseState.DECIDE])
def test_a_delegated_outcome_closes_the_run(state: CaseState) -> None:
    result = apply(state, Trigger.OUTCOME_SET_DELEGATED, TransitionContext())
    assert result.to_state is CaseState.CLOSED


def test_a_split_keeps_the_run_in_decide_and_a_tally_closes_it() -> None:
    assert (
        apply(CaseState.DECIDE, Trigger.VOTE_NO_MAJORITY, TransitionContext()).to_state
        is CaseState.DECIDE
    )
    assert (
        apply(CaseState.DECIDE, Trigger.VOTE_TALLIED, TransitionContext()).to_state
        is CaseState.CLOSED
    )


def test_a_split_never_becomes_a_deferred_state() -> None:
    assert "deferred" not in {s.value for s in CaseState}
    assert "deferred" not in {t.value for t in Trigger}


# ── the audit vocabulary ─────────────────────────────────────────────────────


def test_the_steps_document_the_records_scenes_seventeen_to_nineteen_show() -> None:
    assert RECORDS_FOR[K.VOTE_RECORD] == ("vote.cast",)
    assert RECORDS_FOR[K.COMMITTEE_QUESTION] == ("committee.question",)
    assert RECORDS_FOR[K.DECIDE_OUTCOME] == ("vote.revealed", "outcome.set")
    assert RECORDS_FOR[K.CLOSED_FINALIZE] == (
        "outcome.delivered",
        "case.closed",
        "chain.verified",
    )
    assert set(RECORDS_FOR[K.COMMITTEE_ANSWER]) == {
        "committee.answer",
        "case.unlocked",
        "pack.superseded",
        "vote.rerequested",
    }
