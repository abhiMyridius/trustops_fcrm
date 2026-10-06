"""``resilience.drills``: the outage, stream and poison flows, run over the fake
stack (the harness-restart, handoff and replay flows are in
``test_resilience_drills.py``). They prove each flow injects its failure in the
right order, always puts the container back, and reports what the drill asserts.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from resilience import drills
from resilience.gateway import Reply

C1, C2 = "FCRM-2026-0401", "FCRM-2026-0402"


def _record(seq: int, kind: str, **data: Any) -> dict[str, Any]:
    return {"seq": seq, "type": kind, "actor": "system", "summary": kind, "data": data}


def _chain() -> list[dict[str, Any]]:
    return [
        _record(1, "case.created", run_id="run_1", step_id="step_1"),
        _record(2, "workflow.started", run_id="run_1", step_id="step_1", pins={}),
    ]


def _serve_cases(stack: Any, reply: Callable[..., Reply], *cases: str) -> None:
    """Route each case's chain and verification."""
    stack.world.inflight = False
    for case in cases:
        stack.gateway.route_fn(
            "GET", f"/audit/records/{case}", lambda: reply({"items": _chain()})
        )
        stack.gateway.route(
            "GET",
            f"/audit/verify/{case}",
            reply({"case_id": case, "record_count": 2, "ok": True}),
        )


# ------------------------------------------------------------ D2: RabbitMQ


def test_the_broker_outage_writes_while_down_and_restarts_it(
    stack: Any, reply: Callable[..., Reply]
) -> None:
    _serve_cases(stack, reply, C1, C2)
    stack.gateway.route(
        "POST", "/cases", reply({"id": C1}, 201), reply({"id": C2}, 201)
    )
    stack.docker.ready_codes = {"api": 503, "harness": 503}
    outcome = drills.outage_of_rabbitmq(stack, hold_s=0)
    assert stack.world.log[0] == "stop rabbitmq"
    assert "start rabbitmq" in stack.world.log
    assert stack.world.log.index("stop rabbitmq") < stack.world.log.index(
        "start rabbitmq"
    )
    assert (outcome.write.status, outcome.outage_case, outcome.after_case) == (
        201,
        C1,
        C2,
    )
    assert (outcome.api_ready_during, outcome.harness_ready_during) == (503, 503)
    assert outcome.problems == []
    assert [v["ok"] for v in outcome.verifies] == [True, True]


def test_the_broker_is_started_again_even_if_the_write_blows_up(
    stack: Any, reply: Callable[..., Reply]
) -> None:
    _serve_cases(stack, reply, C1, C2)

    def refuse(*args: Any, **kwargs: Any) -> Reply:
        raise RuntimeError("the gateway client broke")

    stack.gateway.post = refuse
    with pytest.raises(RuntimeError):
        drills.outage_of_rabbitmq(stack, hold_s=0)
    assert "start rabbitmq" in stack.world.log


def test_a_refused_write_while_the_broker_is_down_is_reported_not_raised(
    stack: Any, reply: Callable[..., Reply]
) -> None:
    _serve_cases(stack, reply, C2)
    stack.gateway.route(
        "POST",
        "/cases",
        reply({"code": "upstream_unavailable"}, 503),
        reply({"id": C2}, 201),
    )
    outcome = drills.outage_of_rabbitmq(stack, hold_s=0)
    assert (outcome.write.status, outcome.outage_case) == (503, None)
    assert outcome.outage_records == []


# --------------------------------------------------------------- D3: Mongo


def test_the_mongo_outage_probes_while_down_and_checks_for_half_applied_state(
    stack: Any, reply: Callable[..., Reply]
) -> None:
    _serve_cases(stack, reply, C1, C2)
    down = reply({"code": "upstream_unavailable"}, 503, "application/problem+json")
    stack.gateway.route(
        "POST", "/cases", reply({"id": C1}, 201), down, reply({"id": C2}, 201)
    )
    stack.gateway.route("GET", "/cases", down, reply({"items": [{"title": "kept"}]}))
    stack.gateway.route("GET", "/auth/me", down)
    stack.gateway.route("GET", "/queue", down)
    stack.gateway.route("GET", f"/cases/{C1}", reply({"id": C1}))
    stack.docker.ready_codes = {"api": 503, "harness": 503}
    outcome = drills.outage_of_mongo(stack, hold_s=0)
    assert stack.world.log.index("stop mongo") < stack.world.log.index("start mongo")
    assert [p.status for p in outcome.probes] == [503, 503, 503, 503]
    assert all(drills.is_clean_failure(p) for p in outcome.probes)
    assert outcome.failed_write_visible is False
    assert outcome.survivor_status == 200
    assert outcome.after_case == C2
    assert outcome.problems == []


def test_a_case_the_failed_write_left_behind_is_reported(
    stack: Any, reply: Callable[..., Reply]
) -> None:
    _serve_cases(stack, reply, C1, C2)
    down = reply({"code": "upstream_unavailable"}, 503, "application/problem+json")
    stack.gateway.route(
        "POST", "/cases", reply({"id": C1}, 201), down, reply({"id": C2}, 201)
    )
    stack.gateway.route("GET", "/auth/me", down)
    stack.gateway.route("GET", "/queue", down)
    stack.gateway.route("GET", f"/cases/{C1}", reply({"id": C1}))

    def listing() -> Reply:
        reads = [
            c
            for c in stack.gateway.calls
            if c["path"] == "/cases" and c["method"] in ("GET", "PAGES")
        ]
        if len(reads) == 1:  # the probe made while Mongo was down
            return down
        posts = [
            c
            for c in stack.gateway.calls
            if c["method"] == "POST" and c["path"] == "/cases"
        ]
        return reply({"items": [{"title": posts[1]["json"]["title"]}]})

    stack.gateway.route_fn("GET", "/cases", listing)
    outcome = drills.outage_of_mongo(stack, hold_s=0)
    assert outcome.failed_write_visible is True


def test_mongo_is_put_back_when_a_probe_blows_up(
    stack: Any, reply: Callable[..., Reply]
) -> None:
    _serve_cases(stack, reply, C1)
    stack.gateway.route("POST", "/cases", reply({"id": C1}, 201))

    def explode(*args: Any, **kwargs: Any) -> Reply:
        raise RuntimeError("probe failed")

    original = stack.gateway.call

    def call(persona: Any, method: str, path: str, **kwargs: Any) -> Reply:
        if path == "/cases" and method == "GET":
            explode()
        return original(persona, method, path, **kwargs)

    stack.gateway.call = call
    with pytest.raises(RuntimeError):
        drills.outage_of_mongo(stack, hold_s=0)
    assert "start mongo" in stack.world.log


# -------------------------------------------------------------- D4: stream


def _stream_stack(
    stack: Any,
    reply: Callable[..., Reply],
    reader: Callable[..., Any],
    *,
    resumed: list[int],
) -> None:
    stack.world.inflight = False
    stack.gateway.streams = [
        reader([1, 2, 3]),
        reader(resumed),
        reader([1, 2, 3, 4, 5]),
    ]
    stack.gateway.route("POST", f"/cases/{C1}/discard", reply(None, 204))


def test_a_stream_that_resumes_without_a_gap_has_no_problem(
    stack: Any, reply: Callable[..., Reply], reader: Callable[..., Any]
) -> None:
    _stream_stack(stack, reply, reader, resumed=[4, 5])
    outcome = drills.stream_across_api_restart(stack, C1)
    assert "restart api 30" in stack.world.log
    assert (outcome.before, outcome.after, outcome.full) == (
        [1, 2, 3],
        [4, 5],
        [1, 2, 3, 4, 5],
    )
    assert outcome.statuses == [200, 200, 200]
    assert outcome.first_ended == "closed by the server"
    assert outcome.problems == []
    calls = [c for c in stack.gateway.calls if c["method"] == "STREAM"]
    assert [c["last_event_id"] for c in calls] == [0, 3, 0]


def test_a_resume_that_skipped_an_event_is_reported(
    stack: Any, reply: Callable[..., Reply], reader: Callable[..., Any]
) -> None:
    _stream_stack(stack, reply, reader, resumed=[5])
    outcome = drills.stream_across_api_restart(stack, C1)
    assert outcome.problems
    assert any("never delivered" in p for p in outcome.problems)


def test_a_resume_that_replayed_an_event_twice_is_reported(
    stack: Any, reply: Callable[..., Reply], reader: Callable[..., Any]
) -> None:
    _stream_stack(stack, reply, reader, resumed=[3, 4, 5])
    outcome = drills.stream_across_api_restart(stack, C1)
    assert any("delivered twice" in p for p in outcome.problems)


def test_every_stream_reader_is_stopped_at_the_end(
    stack: Any, reply: Callable[..., Reply], reader: Callable[..., Any]
) -> None:
    _stream_stack(stack, reply, reader, resumed=[4, 5])
    readers = list(stack.gateway.streams)
    drills.stream_across_api_restart(stack, C1)
    assert all(reader.stopped for reader in readers)


# ---------------------------------------------------- D5b: poison messages


def _poison_world(stack: Any) -> dict[str, Any]:
    """Make the fake world react like the broker: the unparseable message goes
    straight to the DLQ, the invalid one waits in the retry queue for one poll
    and then parks; a confirmed purge removes a message.
    """
    dlq = drills.STEPS_DLQ
    state: dict[str, Any] = {"base": 0}

    def park_after_one_poll() -> None:
        stack.world.script.append(release)

    def release() -> None:
        stack.world.retry_depth = 0
        stack.world.dlq_depth[dlq] = state["base"] + 2

    def on_publish(entry: dict[str, Any]) -> None:
        if entry["routing_key"] != drills.STEPS_QUEUE or entry.get("exchange"):
            return
        mid = entry["message_id"]
        if "bad" in mid:
            stack.world.dlq_depth[dlq] = state["base"] + 1
            stack.broker.shown.append({"message_id": mid, "attempt": 0})
        else:
            stack.world.retry_depth = 1
            stack.broker.shown.append({"message_id": mid, "attempt": 3})
            stack.world.script.append(lambda: None)  # one poll sees the retry queue
            park_after_one_poll()

    def on_make(argv: list[str]) -> None:
        if "CONFIRM=1" in argv:
            stack.world.dlq_depth[dlq] -= 1

    stack.broker.on_publish = on_publish
    stack.runner.on_run = on_make
    return state


def test_poison_messages_are_followed_to_the_dlq_and_purged_by_id(
    stack: Any, reply: Callable[..., Reply]
) -> None:
    _serve_cases(stack, reply, C1, C2)
    stack.gateway.route("POST", "/cases", reply({"id": C2}, 201))
    _poison_world(stack)
    for status in (2, 0, 0):
        stack.runner.reply("", returncode=status)
    outcome = drills.poison_the_steps_queue(stack, C1)
    assert outcome.malformed_retry_peak == 0
    assert outcome.invalid_retry_peak == 1
    assert outcome.other_case == C2
    assert sorted(m["attempt"] for m in outcome.shown.values()) == [0, 3]
    assert outcome.purge_refused_status == 2
    assert outcome.depth_after_refusal == 2
    assert outcome.purge_statuses == [0, 0]
    assert outcome.drained is True
    assert outcome.consumers_after is True


def test_the_purge_is_by_message_id_and_the_refusal_comes_first(
    stack: Any, reply: Callable[..., Reply]
) -> None:
    _serve_cases(stack, reply, C1, C2)
    stack.gateway.route("POST", "/cases", reply({"id": C2}, 201))
    _poison_world(stack)
    for status in (2, 0, 0):
        stack.runner.reply("", returncode=status)
    drills.poison_the_steps_queue(stack, C1)
    refused, first, second = (c["argv"] for c in stack.runner.calls)
    assert "CONFIRM=1" not in refused
    assert refused[-1].startswith("id=evt_drill_bad_")
    assert "CONFIRM=1" in first
    assert "CONFIRM=1" in second
    assert any(a.startswith("id=evt_drill_invalid_") for a in second)


def test_the_two_bad_messages_are_purged_even_if_the_drill_breaks(
    stack: Any, reply: Callable[..., Reply]
) -> None:
    _serve_cases(stack, reply, C1)
    _poison_world(stack)

    def explode(*args: Any, **kwargs: Any) -> Reply:
        raise RuntimeError("gateway client broke")

    stack.gateway.post = explode
    with pytest.raises(RuntimeError):
        drills.poison_the_steps_queue(stack, C1)
    assert len(stack.broker.purged) == 2
    assert {dlq for dlq, _ in stack.broker.purged} == {drills.STEPS_DLQ}


# ------------------------------------------------------------ D7: identity


def test_the_identity_outage_uses_the_token_issued_before_it(
    stack: Any, reply: Callable[..., Reply]
) -> None:
    down = reply({"code": "upstream_unavailable"}, 503, "application/problem+json")
    stack.gateway.route(
        "GET",
        "/auth/me",
        reply({"id": "u-riyer"}),  # before
        down,  # during
        reply({"id": "u-riyer"}),  # the poll that sees it back
        reply({"code": "unauthorized"}, 401),  # the forged token
    )
    stack.gateway.route("GET", "/queue", down, reply({"items": []}))
    stack.docker.ready_codes = {"harness": 200}
    outcome = drills.outage_of_identity(stack, hold_s=0)
    assert stack.world.log.index("stop api") < stack.world.log.index("start api")
    assert outcome.before_status == 200
    assert [p.status for p in outcome.during] == [503, 503]
    assert (outcome.after_status, outcome.after_user) == (200, "u-riyer")
    assert (outcome.queue_status, outcome.forged_status) == (200, 401)
    assert outcome.harness_ready is True
    tokens = {c.get("token") for c in stack.gateway.calls if c["path"] == "/auth/me"}
    assert "token-analyst" in tokens
    assert len(tokens) == 2, "the forged call must carry a different token"


def test_the_malformed_message_is_json_typed_only_where_the_adapter_fix_is_deployed(
    stack: Any, reply: Callable[..., Reply]
) -> None:
    _serve_cases(stack, reply, C1, C2)
    stack.gateway.route("POST", "/cases", reply({"id": C2}, 201))
    _poison_world(stack)
    for status in (2, 0, 0):
        stack.runner.reply("", returncode=status)
    drills.poison_the_steps_queue(stack, C1)
    malformed = stack.broker.published[0]
    assert malformed["content_type"] == "application/json"
    assert b'"event_id": "evt_drill_bad_' in malformed["body"]


def test_without_the_adapter_fix_the_malformed_message_is_plain_text(
    stack: Any, reply: Callable[..., Reply]
) -> None:
    _serve_cases(stack, reply, C1, C2)
    stack.gateway.route("POST", "/cases", reply({"id": C2}, 201))
    stack.docker.adapter_fixed = False
    _poison_world(stack)
    for status in (2, 0, 0):
        stack.runner.reply("", returncode=status)
    drills.poison_the_steps_queue(stack, C1)
    assert stack.broker.published[0]["content_type"] == "text/plain"


def test_a_harness_that_cannot_be_asked_counts_as_not_fixed(stack: Any) -> None:
    stack.docker.exec_fails = True
    assert drills.adapter_fix_deployed(stack) is False


def test_a_peeked_message_is_named_by_its_amqp_id_or_its_event_id() -> None:
    assert drills.names_message({"message_id": "evt_1"}, "evt_1")
    assert drills.names_message({"message_id": "uuid", "event_id": "evt_1"}, "evt_1")
    assert not drills.names_message(
        {"message_id": "uuid", "event_id": "evt_2"}, "evt_1"
    )
    assert not drills.names_message({}, "evt_1")
