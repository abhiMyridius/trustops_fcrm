"""D5: dead letters, replay and poison messages (design A section 4.5, ADR-28).

Property: nothing is dropped and nothing is stuck. A message the consumer cannot
handle is retried and then parked in ``<queue>.dlq`` while the rest of the work
goes on; an operator can replay a parked message (with a fresh retry budget) or
purge it behind an explicit flag, with the repo's own ``make`` targets.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from resilience import drills
from resilience.stack import Stack

pytestmark = pytest.mark.resilience


@pytest.mark.drill("D5a", "a parked finished step is replayed with make dlq-replay")
def test_d5a_replayed_finished_step_drains_the_dlq_and_changes_nothing(
    stack: Stack, scratch_case: Callable[[str], str]
) -> None:
    """Injection: a valid ``harness.step`` for a step that already finished is
    put on ``dlx`` (as if its handler had failed three times on an outage that is
    now over); ``make dlq-replay q=caseflow.steps id=<message id>``.
    Observable: the DLQ held it (the tool shows it with ``x-attempt`` 3), is
    back to its starting depth, the harness log says the step was already done,
    the case's chain gained no record and still verifies.
    """
    outcome = drills.replay_a_finished_step(stack, scratch_case("dlq replay"))
    assert outcome.shown is not None, "the parked message was not visible"
    assert outcome.shown["attempt"] == 3, outcome.shown
    assert outcome.make_status == 0, outcome.make_output
    assert outcome.drained, "the DLQ did not drain after the replay"
    assert outcome.skipped_in_log, "the consumer did not report the step as done"
    assert outcome.records_after == outcome.records_before
    assert outcome.verify["ok"] is True, outcome.verify


@pytest.mark.drill(
    "D5b", "poison messages are parked, other work goes on, purge drains"
)
def test_d5b_poison_messages_land_in_the_dlq_and_other_work_is_unaffected(
    stack: Stack, scratch_case: Callable[[str], str], scratch_registry: list[str]
) -> None:
    """Injection: onto ``caseflow.steps`` an unparseable body (poison: straight
    to the DLQ) and a well-formed envelope with an invalid payload (retried via
    ``caseflow.steps.retry`` three times, then parked); a new case is raised
    while the second is still retrying; both are purged by message id with
    ``make dlq-purge`` (refused without ``CONFIRM=1``). Observable: the first
    never touches the retry queue; the second does; both reach the DLQ with
    their error and attempt count; the bystander case gets its
    ``workflow.started`` meanwhile; the refused purge deleted nothing; after the
    confirmed purges the DLQ is where it started and the consumer is attached.
    """
    outcome = drills.poison_the_steps_queue(stack, scratch_case("poison"))
    scratch_registry.append(outcome.other_case)
    assert outcome.malformed_retry_peak == 0, "poison went through the retry queue"
    assert outcome.invalid_retry_peak >= 1, "the invalid payload was not retried"
    assert outcome.invalid_dlq_s >= 60, "the invalid payload skipped its retries"
    shown = outcome.shown
    assert len(shown) == 2, shown
    attempts = sorted(m["attempt"] for m in shown.values())
    assert attempts == [0, drills.STEP_ATTEMPTS], attempts
    assert outcome.other_work_s < 120, outcome.other_work_s
    assert outcome.purge_refused_status != 0
    assert outcome.depth_after_refusal >= 2, "a purge without CONFIRM deleted"
    assert outcome.purge_statuses == [0, 0], outcome.purge_statuses
    assert outcome.drained, "the DLQ did not drain after the purge"
    assert outcome.consumers_after, "the steps consumer was lost"
