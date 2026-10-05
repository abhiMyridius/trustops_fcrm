"""The names H4 and S3 bind to, checked against ``ops/rabbitmq/definitions.json``
(ops owns the topology; clients only connect to it passively): H4 consumes
``context-worker.run`` and publishes ``index.built`` / ``index.failed`` on
``case.events``, and S3 hears them on ``policy-service.events``.
"""

from __future__ import annotations

import fnmatch
import json
from pathlib import Path
from typing import Any

import pytest
from faststream.rabbit import ExchangeType
from fcrm_common.events import default_exchange
from fcrm_context_worker.topology import (
    CASE_EVENTS_EXCHANGE,
    CONTEXT_JOBS_EXCHANGE,
    CONTEXT_RUN_QUEUE,
    EXCHANGE_TYPES,
    ROUTING_KEY_INDEX_BUILD,
    ROUTING_KEY_INDEX_BUILT,
    ROUTING_KEY_INDEX_FAILED,
    passive_exchange,
    passive_queue,
)
from fcrm_policy.events import (
    POLICY_EVENTS_EXCHANGE,
    POLICY_EVENTS_QUEUE,
    POLICY_EVENTS_ROUTING_KEY,
)

DEFINITIONS = Path(__file__).resolve().parents[5] / "ops/rabbitmq/definitions.json"


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


def test_the_context_queue_is_a_quorum_queue_with_a_retry_and_a_dlq(
    definitions: dict[str, Any],
) -> None:
    work = _queue(definitions, CONTEXT_RUN_QUEUE)
    retry = _queue(definitions, f"{CONTEXT_RUN_QUEUE}.retry")
    dlq = _queue(definitions, f"{CONTEXT_RUN_QUEUE}.dlq")

    assert work["arguments"]["x-queue-type"] == "quorum"
    assert work["arguments"]["x-dead-letter-exchange"] == "dlx"
    assert work["arguments"]["x-dead-letter-routing-key"] == CONTEXT_RUN_QUEUE
    assert retry["arguments"]["x-message-ttl"] == 30000
    assert retry["arguments"]["x-dead-letter-routing-key"] == CONTEXT_RUN_QUEUE
    assert "x-message-ttl" not in dlq["arguments"]
    assert ("retry", CONTEXT_RUN_QUEUE) in _bindings(
        definitions, f"{CONTEXT_RUN_QUEUE}.retry"
    )
    assert ("dlx", CONTEXT_RUN_QUEUE) in _bindings(
        definitions, f"{CONTEXT_RUN_QUEUE}.dlq"
    )


def test_the_context_queue_is_bound_to_context_jobs_on_index_build_only(
    definitions: dict[str, Any],
) -> None:
    assert _bindings(definitions, CONTEXT_RUN_QUEUE) == [
        (CONTEXT_JOBS_EXCHANGE, ROUTING_KEY_INDEX_BUILD)
    ]
    assert (CONTEXT_JOBS_EXCHANGE, ROUTING_KEY_INDEX_BUILD) == (
        "context.jobs",
        "index.build",
    )


def test_index_build_is_published_to_the_exchange_h4_listens_on() -> None:
    assert default_exchange(ROUTING_KEY_INDEX_BUILD) == CONTEXT_JOBS_EXCHANGE


@pytest.mark.parametrize("key", [ROUTING_KEY_INDEX_BUILT, ROUTING_KEY_INDEX_FAILED])
def test_h4_publishes_its_results_on_the_topic_exchange_s3_listens_to(
    definitions: dict[str, Any], key: str
) -> None:
    assert default_exchange(key) == CASE_EVENTS_EXCHANGE
    bindings = _bindings(definitions, POLICY_EVENTS_QUEUE)
    assert (POLICY_EVENTS_EXCHANGE, POLICY_EVENTS_ROUTING_KEY) in bindings
    # RabbitMQ topic matching: ``*`` stands for exactly one word.
    assert fnmatch.fnmatch(key, POLICY_EVENTS_ROUTING_KEY)


def test_s3_hears_every_index_event_on_its_own_queue(
    definitions: dict[str, Any],
) -> None:
    work = _queue(definitions, POLICY_EVENTS_QUEUE)
    assert work["arguments"]["x-queue-type"] == "quorum"
    assert work["arguments"]["x-dead-letter-routing-key"] == POLICY_EVENTS_QUEUE
    assert _queue(definitions, f"{POLICY_EVENTS_QUEUE}.retry")
    assert _queue(definitions, f"{POLICY_EVENTS_QUEUE}.dlq")
    assert (POLICY_EVENTS_QUEUE, POLICY_EVENTS_EXCHANGE, POLICY_EVENTS_ROUTING_KEY) == (
        "policy-service.events",
        "case.events",
        "index.*",
    )


def test_the_exchange_types_match_the_definitions(definitions: dict[str, Any]) -> None:
    declared = {e["name"]: e["type"] for e in definitions["exchanges"]}
    assert declared[CONTEXT_JOBS_EXCHANGE] == "direct"
    assert declared[CASE_EVENTS_EXCHANGE] == "topic"
    assert EXCHANGE_TYPES[CONTEXT_JOBS_EXCHANGE] == ExchangeType.DIRECT
    assert EXCHANGE_TYPES[CASE_EVENTS_EXCHANGE] == ExchangeType.TOPIC


def test_h4_only_ever_connects_passively() -> None:
    queue = passive_queue(CONTEXT_RUN_QUEUE, routing_key=ROUTING_KEY_INDEX_BUILD)
    assert queue.declare is False
    assert passive_exchange(CONTEXT_JOBS_EXCHANGE).declare is False
