"""``resilience.approvals``: what a drill needs approval for, and the guard that
keeps a drill that makes live AWS calls from running unless a person said so.
"""

from __future__ import annotations

import pytest
from resilience.approvals import (
    AWS,
    DISRUPTIVE,
    ENV_AWS,
    ENV_DISRUPTIVE,
    approval_skip_reason,
)


def test_a_drill_that_needs_no_approval_runs_without_any() -> None:
    assert approval_skip_reason({"resilience", "drill"}, {}) is None


def test_a_disruptive_drill_is_skipped_until_the_switch_is_set() -> None:
    reason = approval_skip_reason({DISRUPTIVE}, {})
    assert reason is not None
    assert ENV_DISRUPTIVE in reason
    assert "make resilience DISRUPTIVE=1" in reason
    assert approval_skip_reason({DISRUPTIVE}, {ENV_DISRUPTIVE: "1"}) is None


def test_an_aws_drill_is_skipped_until_its_own_switch_is_set() -> None:
    reason = approval_skip_reason({AWS}, {})
    assert reason is not None
    assert ENV_AWS in reason
    assert "Bedrock" in reason
    assert approval_skip_reason({AWS}, {ENV_AWS: "1"}) is None


def test_disruptive_approval_alone_does_not_approve_aws_calls() -> None:
    environ = {ENV_DISRUPTIVE: "1"}
    reason = approval_skip_reason({DISRUPTIVE, AWS}, environ)
    assert reason is not None
    assert ENV_AWS in reason


def test_aws_approval_alone_does_not_approve_restarting_containers() -> None:
    environ = {ENV_AWS: "1"}
    reason = approval_skip_reason({DISRUPTIVE, AWS}, environ)
    assert reason is not None
    assert ENV_DISRUPTIVE in reason


def test_both_switches_approve_a_drill_that_needs_both() -> None:
    environ = {ENV_AWS: "1", ENV_DISRUPTIVE: "1"}
    assert approval_skip_reason({DISRUPTIVE, AWS}, environ) is None


@pytest.mark.parametrize("value", ["", "0", "no", "false", "yes", "true", " 1"])
def test_only_exactly_one_is_an_approval(value: str) -> None:
    assert approval_skip_reason({AWS}, {ENV_AWS: value}) is not None
