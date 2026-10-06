"""``resilience.broker``: queue statistics and the calls into the in-container
script (the script itself is tested in ``test_resilience_broker_agent.py``).
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from typing import Any

import pytest
from resilience.broker import (
    AGENT_SOURCE_PATH,
    CONSUMED_QUEUES,
    Broker,
    QueueStat,
    parse_list_queues,
)
from resilience.docker import DockerCli
from resilience.errors import BrokerAgentError, CommandError

LISTING = (
    "name\tmessages_ready\tmessages_unacknowledged\tconsumers\n"
    "caseflow.steps\t0\t1\t1\n"
    "caseflow.steps.dlq\t2\t0\t0\n"
    "agent-worker.run.dlq\t0\t0\t0\n"
)


@pytest.fixture
def broker(docker: DockerCli) -> Broker:
    """A broker over the scripted docker runner."""
    return Broker(docker)


def test_parse_list_queues_reads_every_row_and_skips_the_header() -> None:
    stats = parse_list_queues(LISTING)
    assert stats["caseflow.steps"] == QueueStat("caseflow.steps", 0, 1, 1)
    assert set(stats) == {
        "caseflow.steps",
        "caseflow.steps.dlq",
        "agent-worker.run.dlq",
    }


def test_queue_depth_counts_ready_and_in_flight() -> None:
    assert QueueStat("q", 2, 3, 0).depth == 5


@pytest.mark.parametrize("text", ["q\tx\t0\t0\n", "q\t1\t2\n"])
def test_parse_list_queues_rejects_a_malformed_row(text: str) -> None:
    with pytest.raises(BrokerAgentError, match="unexpected list_queues row"):
        parse_list_queues(text)


def test_queues_asks_rabbitmqctl_inside_the_broker_container(
    broker: Broker, runner: Any
) -> None:
    runner.reply(LISTING)
    broker.queues()
    argv = runner.calls[0]["argv"]
    assert argv[:4] == ["docker", "exec", "trustops-rabbitmq-1", "rabbitmqctl"]
    assert "messages_unacknowledged" in argv


def test_dead_letter_depths_keeps_only_the_dlqs(broker: Broker, runner: Any) -> None:
    runner.reply(LISTING)
    assert broker.dead_letter_depths() == {
        "caseflow.steps.dlq": 2,
        "agent-worker.run.dlq": 0,
    }


def test_depth_of_an_unknown_queue_is_an_error(broker: Broker, runner: Any) -> None:
    runner.reply(LISTING)
    with pytest.raises(BrokerAgentError, match="no queue named"):
        broker.depth("nope")


def test_consumerless_lists_every_consumed_queue_without_a_consumer(
    broker: Broker, runner: Any
) -> None:
    runner.reply(LISTING)
    missing = broker.consumerless()
    assert "caseflow.steps" not in missing
    assert set(missing) == set(CONSUMED_QUEUES) - {"caseflow.steps"}


def test_publish_sends_the_script_on_stdin_and_the_body_as_base64(
    broker: Broker, runner: Any, json_line: Callable[..., str]
) -> None:
    runner.reply(json_line(ok=True, published=1))
    broker.publish(
        "caseflow.steps", b"{bad", headers={"x-attempt": 0}, message_id="evt_1"
    )
    call = runner.calls[0]
    assert call["argv"][:5] == ["docker", "exec", "-i", "trustops-harness-1", "python"]
    assert call["argv"][5:7] == ["-", "publish"]
    assert call["input_text"] == AGENT_SOURCE_PATH.read_text(encoding="utf-8")
    params = json.loads(call["argv"][7])
    assert base64.b64decode(params["body_b64"]) == b"{bad"
    assert (params["exchange"], params["routing_key"]) == ("", "caseflow.steps")
    assert params["message_id"] == "evt_1"


def test_a_failing_script_is_a_broker_agent_error(broker: Broker, runner: Any) -> None:
    runner.fail(CommandError("exited 1"))
    with pytest.raises(BrokerAgentError, match="broker publish failed"):
        broker.publish("caseflow.steps", b"x")


def test_a_script_that_reports_not_ok_is_a_broker_agent_error(
    broker: Broker, runner: Any, json_line: Callable[..., str]
) -> None:
    runner.reply(json_line(ok=False, error="AgentError: nope"))
    with pytest.raises(BrokerAgentError, match="nope"):
        broker.replay("caseflow.steps")


def test_a_script_that_prints_nothing_is_a_broker_agent_error(
    broker: Broker, runner: Any
) -> None:
    runner.reply("")
    with pytest.raises(BrokerAgentError, match="printed nothing"):
        broker.peek("caseflow.steps.dlq")


def test_a_script_with_unreadable_output_is_a_broker_agent_error(
    broker: Broker, runner: Any
) -> None:
    runner.reply("Traceback (most recent call last)\n")
    with pytest.raises(BrokerAgentError, match="unreadable"):
        broker.peek("caseflow.steps.dlq")


def test_peek_returns_the_described_messages(
    broker: Broker, runner: Any, json_line: Callable[..., str]
) -> None:
    runner.reply(json_line(ok=True, messages=[{"message_id": "evt_1"}]))
    assert broker.peek("caseflow.steps.dlq", limit=5) == [{"message_id": "evt_1"}]
    assert json.loads(runner.calls[0]["argv"][7]) == {
        "queue": "caseflow.steps.dlq",
        "limit": 5,
    }


def test_replay_returns_how_many_moved(
    broker: Broker, runner: Any, json_line: Callable[..., str]
) -> None:
    runner.reply(json_line(ok=True, replayed=3, left=0, to="caseflow.steps"))
    assert broker.replay("caseflow.steps", message_id="evt_9") == 3
    params = json.loads(runner.calls[0]["argv"][7])
    assert params == {"queue": "caseflow.steps", "limit": None, "message_id": "evt_9"}


def test_purge_refuses_a_queue_that_is_not_a_dlq_without_calling_docker(
    broker: Broker, runner: Any
) -> None:
    with pytest.raises(BrokerAgentError, match="only \\*.dlq"):
        broker.purge("caseflow.steps")
    assert runner.calls == []


def test_purge_returns_how_many_went(
    broker: Broker, runner: Any, json_line: Callable[..., str]
) -> None:
    runner.reply(json_line(ok=True, purged=2))
    assert broker.purge("caseflow.steps.dlq") == 2
