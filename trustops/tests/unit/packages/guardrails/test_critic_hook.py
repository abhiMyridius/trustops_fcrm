"""Guard 6: when H1 schedules the critic."""

from __future__ import annotations

import pytest
from fcrm_contracts.enums import CaseState
from fcrm_contracts.events.catalogue import EVENT_TYPES
from fcrm_guardrails.critic_hook import (
    UnknownEventError,
    may_enter_completeness,
    should_schedule_critic,
)


@pytest.mark.parametrize("state", [None, CaseState.ASSESS, CaseState.CRITIC])
def test_should_schedule_critic_after_first_score(state: CaseState | None) -> None:
    assert should_schedule_critic("score.computed", state)


@pytest.mark.parametrize(
    "state",
    [s for s in CaseState if s not in (CaseState.ASSESS, CaseState.CRITIC)],
)
def test_should_schedule_critic_not_for_scores_outside_assess(state: CaseState) -> None:
    assert not should_schedule_critic("score.computed", state)


@pytest.mark.parametrize("event", sorted(EVENT_TYPES - {"score.computed"}))
def test_should_schedule_critic_only_on_score_computed(event: str) -> None:
    assert not should_schedule_critic(event, CaseState.ASSESS)


def test_should_schedule_critic_unknown_event_raises() -> None:
    with pytest.raises(UnknownEventError):
        should_schedule_critic("score.compute")


@pytest.mark.parametrize(
    ("status", "allowed"), [("pending", False), ("done", True), ("handoff", True)]
)
def test_may_enter_completeness(status: str, allowed: bool) -> None:
    assert may_enter_completeness(status) is allowed  # type: ignore[arg-type]
