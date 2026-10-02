"""Rule 5: a closed case is untouched."""

from __future__ import annotations

import pytest
from fcrm_contracts.enums import CaseState
from fcrm_guardrails.rules.r5_closed_untouched import ALLOWED_ON_CLOSED, check

WRITES = [
    "review_item_decide",
    "override",
    "rerun",
    "submission_field_edit",
    "unlock",
    "vote",
]


@pytest.mark.parametrize("action", WRITES)
def test_check_writes_on_a_closed_case_fail(action: str) -> None:
    result = check(CaseState.CLOSED, action)
    assert not result.ok
    assert "closed" in result.detail


@pytest.mark.parametrize("action", sorted(ALLOWED_ON_CLOSED))
def test_check_oversight_actions_on_a_closed_case_pass(action: str) -> None:
    assert check(CaseState.CLOSED, action).ok


@pytest.mark.parametrize("state", [s for s in CaseState if s is not CaseState.CLOSED])
def test_check_open_states_allow_writes(state: CaseState) -> None:
    assert check(state, "override").ok


def test_check_state_given_as_string() -> None:
    assert not check("closed", "override").ok


def test_check_unknown_state_raises() -> None:
    with pytest.raises(ValueError, match="closd"):
        check("closd", "override")
