"""Rule 4: only the product owner edits the submission."""

from __future__ import annotations

import pytest
from fcrm_contracts.enums import Role
from fcrm_guardrails.rules.r4_analyst_no_submission_edit import (
    SUBMISSION_EDIT_ACTIONS,
    check,
)


@pytest.mark.parametrize("action", sorted(SUBMISSION_EDIT_ACTIONS))
def test_check_po_may_edit_the_submission(action: str) -> None:
    assert check(Role.PO, action).ok


@pytest.mark.parametrize("action", sorted(SUBMISSION_EDIT_ACTIONS))
def test_check_analyst_may_never_edit_the_submission(action: str) -> None:
    result = check(Role.ANALYST, action)
    assert not result.ok
    assert "fcrm_analyst" in result.detail


@pytest.mark.parametrize("role", [Role.COMMITTEE, Role.OBSERVER, Role.SERVICE])
def test_check_other_roles_are_refused_too(role: Role) -> None:
    assert not check(role, "submission_field_edit").ok


def test_check_analyst_clarification_question_is_allowed() -> None:
    assert check(Role.ANALYST, "clarification_question").ok


def test_check_role_given_as_string() -> None:
    assert check("fcrm_po", "owner_answer").ok
    assert not check("fcrm_analyst", "owner_answer").ok


def test_check_unknown_role_fails_closed() -> None:
    result = check("fcrm_admin", "submission_field_edit")
    assert not result.ok
    assert "unknown role" in result.detail
