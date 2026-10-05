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


def test_every_declared_queue_name_is_unique(definitions: dict[str, Any]) -> None:
    names = [q["name"] for q in definitions["queues"]]
    assert len(names) == len(set(names))
