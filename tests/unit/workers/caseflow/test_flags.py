"""Tests for :mod:`fcrm_caseflow.flags`: the one place run-flag side
effects (``fast_lane``, ``round_open``, ``locked``, ``pack_version``,
``vote_round``) are actually computed, since
:func:`fcrm_caseflow.transitions.apply` never touches them.
"""

from __future__ import annotations

from fcrm_caseflow.flags import next_flags
from fcrm_caseflow.states import RunFlags
from fcrm_caseflow.transitions import TransitionContext
from fcrm_caseflow.triggers import Trigger


def test_fast_lane_confirm_sets_fast_lane_true() -> None:
    flags = next_flags(
        Trigger.GATE_DECIDED_FAST_LANE_CONFIRM,
        TransitionContext(fast_lane_confirmed=True),
        RunFlags(),
    )
    assert flags.fast_lane is True


def test_fast_lane_decline_leaves_fast_lane_false() -> None:
    flags = next_flags(
        Trigger.GATE_DECIDED_FAST_LANE_CONFIRM,
        TransitionContext(fast_lane_confirmed=False),
        RunFlags(fast_lane=True),
    )
    assert flags.fast_lane is False


def test_completeness_ask_opens_the_round() -> None:
    flags = next_flags(
        Trigger.GATE_DECIDED_COMPLETENESS_ASK, TransitionContext(), RunFlags()
    )
    assert flags.round_open is True


def test_rerun_both_closes_the_round() -> None:
    flags = next_flags(
        Trigger.AGENT_DONE_RERUN_BOTH,
        TransitionContext(),
        RunFlags(round_open=True),
    )
    assert flags.round_open is False


def test_case_locked_locks_and_bumps_pack_version() -> None:
    flags = next_flags(Trigger.CASE_LOCKED, TransitionContext(), RunFlags())
    assert flags.locked is True
    assert flags.pack_version == 1

    flags_again = next_flags(Trigger.CASE_LOCKED, TransitionContext(), flags)
    assert flags_again.pack_version == 2


def test_case_unlocked_unlocks() -> None:
    flags = next_flags(
        Trigger.CASE_UNLOCKED, TransitionContext(), RunFlags(locked=True)
    )
    assert flags.locked is False


def test_committee_answered_changed_unlocks_too() -> None:
    flags = next_flags(
        Trigger.COMMITTEE_ANSWERED_CHANGED,
        TransitionContext(committee_numbers_changed=True),
        RunFlags(locked=True),
    )
    assert flags.locked is False


def test_pack_released_opens_a_voting_round() -> None:
    flags = next_flags(Trigger.PACK_RELEASED, TransitionContext(), RunFlags())
    assert flags.vote_round == 1


def test_no_majority_opens_another_round() -> None:
    flags = next_flags(
        Trigger.VOTE_NO_MAJORITY, TransitionContext(), RunFlags(vote_round=1)
    )
    assert flags.vote_round == 2


def test_an_unrelated_trigger_leaves_flags_unchanged() -> None:
    original = RunFlags(fast_lane=True, locked=True, pack_version=3, vote_round=2)
    flags = next_flags(Trigger.VOTE_CAST, TransitionContext(), original)
    assert flags == original
