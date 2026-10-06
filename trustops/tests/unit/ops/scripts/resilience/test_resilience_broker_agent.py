"""``resilience.broker_agent``: the script that runs inside the harness
container, driven here against a fake ``aio_pika``.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from datetime import datetime
from typing import Any

import pytest
from resilience import broker_agent
from resilience.broker_agent import AgentError, reset_headers, run_command

URL = "amqp://example.invalid/"


def _dead_lettered(
    incoming: Callable[..., Any], message_id: str = "evt_1", **fields: Any
) -> Any:
    """A message as the dead-letter queue holds it: attempts used up."""
    return incoming(
        b'{"type":"harness.step"}',
        message_id,
        headers={
            "x-attempt": 3,
            "x-last-error": "NotFoundError",
            "x-death": [{"reason": "rejected"}],
            "x-first-death-queue": "caseflow.steps",
            "traceparent": "00-abc-def-01",
        },
        **fields,
    )


def test_reset_headers_drops_the_retry_bookkeeping_and_stamps_the_replay(
    frozen_now: datetime,
) -> None:
    headers = reset_headers(
        {
            "x-attempt": 3,
            "x-last-error": "boom",
            "x-death": [],
            "x-last-death-reason": "rejected",
            "traceparent": "00-abc",
        },
        source_queue="caseflow.steps.dlq",
        now=frozen_now,
    )
    assert headers == {
        "traceparent": "00-abc",
        "x-attempt": 0,
        "x-replayed-from": "caseflow.steps.dlq",
        "x-replayed-at": "2026-10-06T12:00:00+00:00",
    }


def test_reset_headers_accepts_a_message_without_headers(frozen_now: datetime) -> None:
    headers = reset_headers(None, source_queue="q.dlq", now=frozen_now)
    assert headers["x-attempt"] == 0


def test_describe_message_decodes_bytes_and_previews_the_body(
    incoming: Callable[..., Any],
) -> None:
    message = incoming(
        b"x" * 1000,
        "evt_1",
        headers={"x-attempt": 3, "x-last-error": b"ValidationError"},
        redelivered=True,
    )
    described = broker_agent.describe_message(message)
    assert described["attempt"] == 3
    assert described["last_error"] == "ValidationError"
    assert described["redelivered"] is True
    assert described["body_bytes"] == 1000
    assert len(described["body_preview"]) == broker_agent.PREVIEW_CHARS


def test_queue_name_helpers_round_trip() -> None:
    assert broker_agent.dead_letter_queue("caseflow.steps") == "caseflow.steps.dlq"
    assert broker_agent.dead_letter_queue("caseflow.steps.dlq") == "caseflow.steps.dlq"
    assert broker_agent.work_queue("caseflow.steps.dlq") == "caseflow.steps"
    assert broker_agent.work_queue("caseflow.steps") == "caseflow.steps"


async def test_replay_moves_every_message_back_with_a_fresh_retry_budget(
    make_pika: Callable[..., Any], incoming: Callable[..., Any], frozen_now: datetime
) -> None:
    first, second = _dead_lettered(incoming, "evt_1"), _dead_lettered(incoming, "evt_2")
    pika = make_pika({"caseflow.steps.dlq": [first, second]})
    outcome = await run_command(
        "replay", {"queue": "caseflow.steps"}, url=URL, aio_pika=pika, now=frozen_now
    )
    assert outcome == {"replayed": 2, "left": 0, "to": "caseflow.steps"}
    published = pika.connection._channel.default_exchange.published
    assert [(m.properties["message_id"], rk) for m, rk in published] == [
        ("evt_1", "caseflow.steps"),
        ("evt_2", "caseflow.steps"),
    ]
    headers = published[0][0].properties["headers"]
    assert headers["x-attempt"] == 0
    assert "x-last-error" not in headers
    assert headers["traceparent"] == "00-abc-def-01"
    assert (first.state, second.state) == ("acked", "acked")
    assert pika.connection.closed


async def test_replay_of_one_message_leaves_the_others_parked(
    make_pika: Callable[..., Any], incoming: Callable[..., Any], frozen_now: datetime
) -> None:
    first, second = _dead_lettered(incoming, "evt_1"), _dead_lettered(incoming, "evt_2")
    pika = make_pika({"caseflow.steps.dlq": [first, second]})
    outcome = await run_command(
        "replay",
        {"queue": "caseflow.steps", "message_id": "evt_2"},
        url=URL,
        aio_pika=pika,
        now=frozen_now,
    )
    assert (outcome["replayed"], outcome["left"]) == (1, 1)
    assert (first.state, second.state) == ("requeued", "acked")


async def test_replay_with_a_limit_stops_after_that_many(
    make_pika: Callable[..., Any], incoming: Callable[..., Any], frozen_now: datetime
) -> None:
    messages = [_dead_lettered(incoming, f"evt_{n}") for n in range(3)]
    pika = make_pika({"caseflow.steps.dlq": messages})
    outcome = await run_command(
        "replay",
        {"queue": "caseflow.steps", "limit": 2},
        url=URL,
        aio_pika=pika,
        now=frozen_now,
    )
    assert outcome["replayed"] == 2
    assert [m.state for m in messages] == ["acked", "acked", "waiting"]


async def test_a_republish_the_broker_refuses_keeps_the_message_parked(
    make_pika: Callable[..., Any], incoming: Callable[..., Any], frozen_now: datetime
) -> None:
    parked = _dead_lettered(incoming)
    pika = make_pika({"caseflow.steps.dlq": [parked]})
    pika.connection._channel.default_exchange.refuse = ConnectionError("nacked")
    with pytest.raises(AgentError, match="replayed 0, then failed: evt_1"):
        await run_command(
            "replay",
            {"queue": "caseflow.steps"},
            url=URL,
            aio_pika=pika,
            now=frozen_now,
        )
    assert parked.state == "requeued"


async def test_purge_deletes_everything_of_a_dlq(
    make_pika: Callable[..., Any], incoming: Callable[..., Any]
) -> None:
    pika = make_pika({"caseflow.steps.dlq": [_dead_lettered(incoming)]})
    outcome = await run_command(
        "purge", {"queue": "caseflow.steps.dlq"}, url=URL, aio_pika=pika
    )
    assert outcome == {"purged": 1}


async def test_purge_of_one_message_acks_only_that_one(
    make_pika: Callable[..., Any], incoming: Callable[..., Any]
) -> None:
    first, second = _dead_lettered(incoming, "evt_1"), _dead_lettered(incoming, "evt_2")
    pika = make_pika({"caseflow.steps.dlq": [first, second]})
    outcome = await run_command(
        "purge",
        {"queue": "caseflow.steps.dlq", "message_id": "evt_1"},
        url=URL,
        aio_pika=pika,
    )
    assert outcome == {"purged": 1}
    assert (first.state, second.state) == ("acked", "requeued")


async def test_purge_refuses_a_queue_that_is_not_a_dlq(
    make_pika: Callable[..., Any],
) -> None:
    pika = make_pika({"caseflow.steps": []})
    with pytest.raises(AgentError, match="only \\*.dlq"):
        await run_command("purge", {"queue": "caseflow.steps"}, url=URL, aio_pika=pika)
    assert pika.connection.closed


async def test_peek_describes_at_most_limit_messages_and_returns_them(
    make_pika: Callable[..., Any], incoming: Callable[..., Any]
) -> None:
    messages = [_dead_lettered(incoming, f"evt_{n}") for n in range(3)]
    pika = make_pika({"caseflow.steps.dlq": messages})
    outcome = await run_command(
        "peek", {"queue": "caseflow.steps.dlq", "limit": 2}, url=URL, aio_pika=pika
    )
    assert [m["message_id"] for m in outcome["messages"]] == ["evt_0", "evt_1"]
    assert [m.state for m in messages] == ["requeued", "requeued", "waiting"]


async def test_publish_decodes_the_body_and_uses_the_default_exchange(
    make_pika: Callable[..., Any],
) -> None:
    pika = make_pika({})
    outcome = await run_command(
        "publish",
        {
            "routing_key": "caseflow.steps",
            "body_b64": base64.b64encode(b"{poison").decode(),
            "headers": {"x-attempt": 0},
            "message_id": "evt_p",
            "type": "harness.step",
        },
        url=URL,
        aio_pika=pika,
    )
    assert outcome == {"published": 1}
    message, routing_key = pika.connection._channel.default_exchange.published[0]
    assert (message.body, routing_key) == (b"{poison", "caseflow.steps")
    assert message.properties["delivery_mode"] == 2


async def test_publish_rejects_a_body_that_is_not_base64(
    make_pika: Callable[..., Any],
) -> None:
    with pytest.raises(AgentError, match="body_b64"):
        await run_command(
            "publish",
            {"routing_key": "q", "body_b64": "!!!"},
            url=URL,
            aio_pika=make_pika({}),
        )


async def test_an_unknown_command_is_refused_and_the_connection_closed(
    make_pika: Callable[..., Any],
) -> None:
    pika = make_pika({})
    with pytest.raises(AgentError, match="unknown command"):
        await run_command("drop-everything", {}, url=URL, aio_pika=pika)
    assert pika.connection.closed


def test_main_reports_a_missing_broker_url_as_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("RABBITMQ_URL", raising=False)
    status = broker_agent.main(["-", "peek", json.dumps({"queue": "q"})])
    outcome = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert status == 1
    assert outcome["ok"] is False
    assert "RABBITMQ_URL" in outcome["error"]


def test_main_reports_bad_usage_as_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert broker_agent.main(["-"]) == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False
