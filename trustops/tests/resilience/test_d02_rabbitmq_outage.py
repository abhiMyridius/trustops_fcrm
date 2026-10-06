"""D2: RabbitMQ goes away and comes back (ADR-02, ADR-28).

Property: the transactional outbox is the durability boundary. A write made while
the broker is down is accepted, its event waits in the outbox, and it is
delivered exactly once after the broker returns; every consumer and the relay
reconnect on their own.
"""

from __future__ import annotations

import pytest
from resilience import drills
from resilience.scenarios import record_types
from resilience.stack import Stack

pytestmark = [pytest.mark.resilience, pytest.mark.disruptive]


@pytest.mark.drill("D2", "RabbitMQ stopped and started; an outbox event survives")
def test_d2_event_written_while_the_broker_is_down_is_delivered_once(
    stack: Stack, scratch_registry: list[str]
) -> None:
    """Injection: ``docker stop`` of RabbitMQ; a PO writes a case while it is
    down; ``docker start``. Observable: the write answers 201; the harness and
    api report not ready while the broker is gone; every work queue has a
    consumer again; the case's chain gets ``case.created`` and
    ``workflow.started`` exactly once each (the event was neither lost nor
    duplicated); a case raised after recovery goes the same way; chains verify;
    no dead letter.
    """
    outcome = drills.outage_of_rabbitmq(stack)
    scratch_registry.extend(filter(None, (outcome.outage_case, outcome.after_case)))
    assert outcome.write.status == 201, outcome.write
    assert outcome.harness_ready_during == 503, "readiness hid the broker outage"
    types = record_types(outcome.outage_records)
    assert types.count("case.created") == 1, types
    assert types.count("workflow.started") == 1, types
    assert record_types(outcome.after_records).count("workflow.started") == 1
    assert outcome.problems == [], outcome.problems
    assert all(v["ok"] for v in outcome.verifies), outcome.verifies
    assert outcome.new_dead_letters == {}, outcome.new_dead_letters
