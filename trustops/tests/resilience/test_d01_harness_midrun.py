"""D1: the harness dies while an agent job is running (ADR-28, ADR-35).

Property: at-least-once delivery with idempotent steps. A job that was running
when its worker went away is delivered again and the run finishes, and nothing is
written twice: no repeated step record, no dead letter, a chain that verifies.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from resilience import drills
from resilience.scenarios import record_types
from resilience.stack import Stack

pytestmark = pytest.mark.resilience


def _assert_run_finished_cleanly(outcome: drills.MidRunOutcome) -> None:
    assert outcome.problems == [], outcome.problems
    assert "proposal.extraction" in record_types(outcome.records)
    assert outcome.verify["ok"] is True, outcome.verify
    assert outcome.verify["record_count"] == len(outcome.records)
    assert outcome.new_dead_letters == {}, outcome.new_dead_letters
    ledger = outcome.ledger
    assert ledger is not None, "H1 holds no token ledger for the case"
    assert ledger["reserved"] == 0, ledger
    assert ledger["spent"] > 0, ledger
    assert {job["status"] for job in ledger["jobs"].values()} == {"settled"}, ledger


@pytest.mark.drill("D1a", "harness restarted (SIGTERM) mid agent run")
def test_d1a_harness_restart_mid_run_finishes_once(
    stack: Stack, scratch_case: Callable[[str], str]
) -> None:
    """Injection: ``docker restart`` of the harness (SIGTERM, up to 70 s to
    drain) while the intake job of a scratch case is on ``agent-worker.run``
    unacknowledged. Observable: the case's chain reaches ``proposal.extraction``
    with gapless unique ``seq``, one ``case.created``, one record per step,
    ``audit/verify`` ok, no dead-letter queue grew, and the case's token ledger
    holds nothing reserved with every job settled (the tokens of a job that was
    cut off are not counted twice).
    """
    outcome = drills.interrupt_harness_midrun(
        stack, scratch_case("harness restart"), mode="graceful"
    )
    assert outcome.in_flight, "no job was caught in flight"
    _assert_run_finished_cleanly(outcome)


@pytest.mark.drill("D1b", "harness killed (SIGKILL) mid agent run")
def test_d1b_harness_kill_mid_run_redelivers_and_finishes_once(
    stack: Stack, scratch_case: Callable[[str], str]
) -> None:
    """Injection: ``docker kill`` (SIGKILL, no drain, no ack) while the job is
    in flight. Observable: the new process is handed the job again (its log
    shows a delivery on ``agent-worker.run``), then the same chain checks as D1a.
    """
    outcome = drills.interrupt_harness_midrun(
        stack, scratch_case("harness kill"), mode="kill"
    )
    assert outcome.redelivered, "the killed worker's job was not delivered again"
    _assert_run_finished_cleanly(outcome)


@pytest.mark.drill("D1c", "second agent.done for a finished job is ignored")
def test_d1c_a_duplicate_agent_done_changes_nothing(
    stack: Stack, scratch_case: Callable[[str], str]
) -> None:
    """Injection: after an intake run finished, publish another ``agent.done``
    for the same job under a fresh event id: what the second run of a
    redelivered job would publish if the first had already reported before it
    died. Observable: no audit record appears (no second ``agent.done.intake``,
    no second ``proposal.*``), the chain still verifies, no dead letter, and the
    token ledger is exactly as it was (a job settles once).
    """
    outcome = drills.inject_duplicate_agent_done(stack, scratch_case("duplicate done"))
    assert outcome.new_records == [], [r["type"] for r in outcome.new_records]
    assert outcome.problems == [], outcome.problems
    assert outcome.verify["ok"] is True, outcome.verify
    assert outcome.new_dead_letters == {}
    assert outcome.ledger_before is not None
    assert outcome.ledger_after is not None
    assert outcome.ledger_after["spent"] == outcome.ledger_before["spent"]
    assert outcome.ledger_after["reserved"] == 0
