"""``ops/rabbitmq/definitions.json``: the topology Caseflow's constants name.

Every work queue has a ``.retry`` (30 s TTL, dead-letters back to the work
queue through the default exchange) and a ``.dlq`` (parked, no TTL), bound
on the ``retry`` and ``dlx`` exchanges with the work queue's name as routing
key. ``caseflow.steps`` (ADR-28) is bound to ``case.events`` on
``harness.step``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from fcrm_case.consumer import QUEUE as CASE_QUEUE
from fcrm_case.consumer import ROUTING_KEYS as CASE_ROUTING_KEYS
from fcrm_caseflow.topology import (
    CASE_EVENTS_EXCHANGE,
    CASEFLOW_AGENT_RESULTS_QUEUE,
    CASEFLOW_EVENTS_QUEUE,
    CASEFLOW_STEPS_QUEUE,
    ROUTING_KEY_HARNESS_STEP,
)

DEFINITIONS = Path(__file__).resolve().parents[3] / "ops/rabbitmq/definitions.json"


@pytest.fixture(scope="module")
def definitions() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(DEFINITIONS.read_text(encoding="utf-8"))
    return loaded


def _queue(definitions: dict[str, Any], name: str) -> dict[str, Any]:
    matches = [q for q in definitions["queues"] if q["name"] == name]
    assert len(matches) == 1, f"queue {name} should be declared exactly once"
    found: dict[str, Any] = matches[0]
    return found


def _bindings(definitions: dict[str, Any], destination: str) -> list[tuple[str, str]]:
    return [
        (b["source"], b["routing_key"])
        for b in definitions["bindings"]
        if b["destination"] == destination
    ]


@pytest.mark.parametrize(
    "queue",
    [CASEFLOW_EVENTS_QUEUE, CASEFLOW_AGENT_RESULTS_QUEUE, CASEFLOW_STEPS_QUEUE],
)
def test_each_caseflow_queue_has_a_retry_and_a_dlq_like_every_work_queue(
    definitions: dict[str, Any], queue: str
) -> None:
    work = _queue(definitions, queue)
    retry = _queue(definitions, f"{queue}.retry")
    dlq = _queue(definitions, f"{queue}.dlq")

    assert work["arguments"]["x-queue-type"] == "quorum"
    assert work["arguments"]["x-dead-letter-exchange"] == "dlx"
    assert work["arguments"]["x-dead-letter-routing-key"] == queue
    assert retry["arguments"]["x-message-ttl"] == 30000
    assert retry["arguments"]["x-dead-letter-exchange"] == ""
    assert retry["arguments"]["x-dead-letter-routing-key"] == queue
    assert "x-message-ttl" not in dlq["arguments"]
    assert ("retry", queue) in _bindings(definitions, f"{queue}.retry")
    assert ("dlx", queue) in _bindings(definitions, f"{queue}.dlq")


def test_caseflow_steps_is_bound_to_case_events_on_harness_step_only(
    definitions: dict[str, Any],
) -> None:
    assert _bindings(definitions, CASEFLOW_STEPS_QUEUE) == [
        (CASE_EVENTS_EXCHANGE, ROUTING_KEY_HARNESS_STEP)
    ]
    assert ROUTING_KEY_HARNESS_STEP == "harness.step"


def test_the_quorum_policy_covers_the_new_queue(definitions: dict[str, Any]) -> None:
    policy = next(
        p for p in definitions["policies"] if p["name"] == "fcrm-quorum-safety"
    )
    assert re.match(policy["pattern"], CASEFLOW_STEPS_QUEUE)


def _policy(definitions: dict[str, Any], name: str) -> dict[str, Any]:
    found: dict[str, Any] = next(
        p for p in definitions["policies"] if p["name"] == name
    )
    return found


def test_a_parked_message_is_never_dropped_for_having_been_looked_at(
    definitions: dict[str, Any],
) -> None:
    """The quorum policy's ``delivery-limit: 5`` also matches every ``*.dlq``;
    a dead-letter queue has no dead-letter exchange of its own, so the sixth
    delivery of a parked message (an inspect, a failed replay) dropped it
    silently: measured on a broker with this topology (the seventh look found
    an empty queue). A higher-priority policy switches the limit off for the
    dead-letter queues only.
    """
    keep = _policy(definitions, "fcrm-dlq-keep")
    safety = _policy(definitions, "fcrm-quorum-safety")
    assert keep["priority"] > safety["priority"]
    assert keep["definition"] == {"delivery-limit": -1}
    assert keep["apply-to"] == "queues"
    for queue in definitions["queues"]:
        name = queue["name"]
        assert bool(re.search(keep["pattern"], name)) == name.endswith(".dlq"), name


def test_the_dlq_policy_leaves_work_and_retry_queues_to_the_quorum_policy(
    definitions: dict[str, Any],
) -> None:
    keep = _policy(definitions, "fcrm-dlq-keep")
    safety = _policy(definitions, "fcrm-quorum-safety")
    work_and_retry = [
        q["name"] for q in definitions["queues"] if not q["name"].endswith(".dlq")
    ]
    assert work_and_retry
    for name in work_and_retry:
        assert not re.search(keep["pattern"], name)
        assert re.match(safety["pattern"], name)


def test_every_declared_queue_name_is_unique(definitions: dict[str, Any]) -> None:
    names = [q["name"] for q in definitions["queues"]]
    assert len(names) == len(set(names))


# evals.events has no consumer: it must not grow without bound (I6-C).

UNCONSUMED_QUEUES = ("evals.events", "evals.events.retry", "evals.events.dlq")


def _effective_policy(definitions: dict[str, Any], queue: str) -> dict[str, Any]:
    """The one policy RabbitMQ applies to ``queue``: the matching policy with the
    highest priority. Policies never merge, so its definition is the whole story."""
    matching = [
        p for p in definitions["policies"] if re.search(p["pattern"], queue) is not None
    ]
    assert matching, f"no policy matches {queue}"
    top = max(matching, key=lambda p: p["priority"])
    priorities = [p["priority"] for p in matching]
    assert priorities.count(top["priority"]) == 1, (
        f"{queue}: two policies tie at priority {top['priority']}"
    )
    found: dict[str, Any] = top
    return found


@pytest.mark.parametrize("queue", UNCONSUMED_QUEUES)
def test_every_queue_without_a_consumer_is_capped_by_length_with_drop_head(
    definitions: dict[str, Any], queue: str
) -> None:
    effective = _effective_policy(definitions, queue)["definition"]

    assert isinstance(effective["max-length"], int)
    assert 0 < effective["max-length"] <= 10000
    assert effective["overflow"] == "drop-head"


def test_the_evals_work_queue_expires_messages_and_caps_bytes(
    definitions: dict[str, Any],
) -> None:
    effective = _effective_policy(definitions, "evals.events")["definition"]

    assert effective["message-ttl"] == 24 * 3600 * 1000
    assert effective["max-length"] == 10000
    assert effective["max-length-bytes"] == 64 * 1024 * 1024
    assert effective["delivery-limit"] == 5, "a future consumer keeps the limit"


def test_the_bounded_policies_outrank_the_quorum_safety_policy(
    definitions: dict[str, Any],
) -> None:
    safety = next(
        p for p in definitions["policies"] if p["name"] == "fcrm-quorum-safety"
    )
    for queue in UNCONSUMED_QUEUES:
        winner = _effective_policy(definitions, queue)
        assert winner["name"] != "fcrm-quorum-safety"
        assert winner["priority"] > safety["priority"]
        assert winner["apply-to"] == "queues"


def test_a_full_evals_queue_never_back_pressures_the_publisher(
    definitions: dict[str, Any],
) -> None:
    """reject-publish on a queue bound to ``case.events`` would nack the
    publisher of every event routed to it, so the evals queue must not use it."""
    for queue in UNCONSUMED_QUEUES:
        effective = _effective_policy(definitions, queue)["definition"]
        assert effective["overflow"] != "reject-publish"


def test_the_queues_with_a_consumer_keep_the_safety_policy_and_stay_unbounded(
    definitions: dict[str, Any],
) -> None:
    """The cap is for the queue nobody reads. A queue with a consumer keeps
    reject-publish: dropping its head would lose a trigger silently."""
    for queue in (CASEFLOW_EVENTS_QUEUE, CASEFLOW_STEPS_QUEUE, "agent-worker.run"):
        effective = _effective_policy(definitions, queue)
        assert effective["name"] == "fcrm-quorum-safety"
        assert effective["definition"]["overflow"] == "reject-publish"
        assert "max-length" not in effective["definition"]


def test_the_evals_queues_keep_their_declared_arguments(
    definitions: dict[str, Any],
) -> None:
    """The policy bounds the queues; the declared arguments stay as they were,
    because a declared queue cannot change its arguments on an existing volume."""
    work = _queue(definitions, "evals.events")
    assert work["arguments"]["x-dead-letter-exchange"] == "dlx"
    assert ("dlx", "evals.events") in _bindings(definitions, "evals.events.dlq")
    for queue in UNCONSUMED_QUEUES:
        arguments = _queue(definitions, queue)["arguments"]
        assert not any(key.startswith("x-max-length") for key in arguments)


def test_s1_consumes_every_key_ops_bound_to_its_queue_and_no_other(
    definitions: dict[str, Any],
) -> None:
    """The keys S1's consumer subscribes with are the bindings of its queue: a key
    the consumer wants that ops did not bind would never arrive (``gate.opened``
    is how S1 learns of a handoff gate), and a binding with no subscriber would
    fill the queue with nothing to read it."""
    bound_from_events = {
        b["routing_key"]
        for b in definitions["bindings"]
        if b["destination"] == CASE_QUEUE and b["source"] == CASE_EVENTS_EXCHANGE
    }

    assert bound_from_events == set(CASE_ROUTING_KEYS)
    assert "gate.opened" in bound_from_events
