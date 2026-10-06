"""D6: an agent job that fails ends in a human handoff (flow map F-path, ADR-14).

Property: a failure is never papered over. A job that cannot complete produces
``agent.failed``, which moves the run to ``handoff`` and opens a handoff gate for
the analyst; no output is invented, and the failure is a business outcome, not a
dead letter.

Why this trigger: ``JobPayload`` carries no budget field and the tier budgets are
constants in ``fcrm_guardrails.budget``, so a tiny per-job budget cannot be
injected without changing shared configuration. An ``agent.run`` for the intake
agent with no ``doc_id`` takes the identical route (H2's ``agent.failed``, H1's
``AGENT_FAILED_AFTER_RETRY`` transition to HANDOFF) at once, with no model call.
The budget-specific path (Guard 3 refusing, ``UsageLimitExceeded``, the second
attempt) is asserted over fakes in ``tests/unit/workers/agent-worker`` and
``tests/unit/packages/guardrails``.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from resilience import drills
from resilience.stack import Stack

pytestmark = pytest.mark.resilience


@pytest.mark.drill("D6", "a job that cannot run ends in a handoff, nothing invented")
def test_d6_failed_job_opens_a_handoff_and_fabricates_nothing(
    stack: Stack, scratch_case: Callable[[str], str]
) -> None:
    """Injection: an ``agent.run`` (intake, cheap tier, no ``refs``) published
    for a scratch case's run. Observable: H2's ``agent.failed`` record names a
    context pack failure; H1 records ``agent.failed.after_retry`` into state
    ``handoff``; the run's state in H1's database is ``handoff`` with an open
    ``handoff`` gate; H1 holds no result for the job and the chain has no
    ``proposal.*`` record; no dead letter; the chain verifies.
    """
    outcome = drills.fail_a_job_into_handoff(stack, scratch_case("handoff"))
    assert "context pack failed" in outcome.failed_record["summary"]
    assert outcome.failed_record["data"]["job_id"] == outcome.job_id
    assert outcome.transition_record["data"]["state_to"] == "handoff"
    assert outcome.run_state == "handoff"
    assert outcome.open_handoff_gates >= 1
    assert outcome.results_for_job == 0, "an output exists for a job that failed"
    assert outcome.proposals == [], outcome.proposals
    assert outcome.new_dead_letters == {}, outcome.new_dead_letters
    assert outcome.problems == [], outcome.problems
    assert outcome.verify["ok"] is True, outcome.verify
