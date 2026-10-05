"""``fcrm_case.sse``: frames, the in-process hub and the stream generator,
driven directly as an async generator (no HTTP transport, no threads).

The stream is unnamed frames (``id:`` and ``data:``), replays what a client
missed from the activity log, reconciles live entries by ``seq`` (no duplicate,
no gap), sends a heartbeat, and shows the PO only what the PO may see.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Any, Optional

import pytest
from fcrm_case.activity_log import ActivityLog
from fcrm_case.domain.activity import ActivityEntry, Audience
from fcrm_case.domain.record import CaseRecord
from fcrm_case.sse import (
    HEARTBEAT_COMMENT,
    OPEN_COMMENT,
    ActivityWriter,
    EventHub,
    StreamParams,
    case_event_stream,
    format_frame,
)
from fcrm_case.store import InMemoryCaseStore
from fcrm_contracts.api.case_internal import UiActor, UiEventIn
from pymongo.errors import ConnectionFailure

CASE = "FCRM-2026-0151"


class FakeRequest:
    """Just the one method the generator uses."""

    def __init__(self, disconnect_after: Optional[int] = None) -> None:
        self._calls = 0
        self._disconnect_after = disconnect_after

    async def is_disconnected(self) -> bool:
        self._calls += 1
        return (
            self._disconnect_after is not None and self._calls > self._disconnect_after
        )


def entry(
    seq: int,
    *,
    type: str = "document.extracted",
    visibility: str = "po",
    detail: Optional[dict[str, Any]] = None,
    case_id: str = CASE,
) -> ActivityEntry:
    return ActivityEntry(
        case_id=case_id,
        seq=seq,
        at=datetime(2026, 10, 5, 10, 0, seq % 60, tzinfo=UTC),
        type=type,
        visibility=visibility,  # type: ignore[arg-type]
        summary=f"summary {seq}",
        detail=detail or {},
        actor=UiActor(id="svc:caseflow", role="fcrm_service", component="caseflow"),
        record_seq=seq + 100,
    )


def params(
    audience: Audience = "staff",
    start_seq: int = 0,
    heartbeat_s: float = 0.01,
    max_lifetime_s: float = 5.0,
) -> StreamParams:
    return StreamParams(
        case_id=CASE,
        audience=audience,
        start_seq=start_seq,
        heartbeat_s=heartbeat_s,
        max_lifetime_s=max_lifetime_s,
    )


def data_of(frame: str) -> dict[str, Any]:
    line = next(ln for ln in frame.splitlines() if ln.startswith("data: "))
    body: dict[str, Any] = json.loads(line[len("data: ") :])
    return body


def seqs(frames: list[str]) -> list[int]:
    return [data_of(f)["seq"] for f in frames if f.startswith("id: ")]


async def collect(
    stream: AsyncIterator[str],
    until: Callable[[list[str]], bool],
    timeout_s: float = 2.0,
) -> list[str]:
    """Frames from ``stream`` until ``until`` says stop; always closes it."""
    frames: list[str] = []
    try:
        async with asyncio.timeout(timeout_s):
            async for frame in stream:
                frames.append(frame)
                if until(frames):
                    break
    finally:
        await stream.aclose()  # type: ignore[attr-defined]
    return frames


@pytest.fixture
def log(case_store: InMemoryCaseStore, stored_case: CaseRecord) -> ActivityLog:
    """The in-memory activity log of a store that holds the demo case."""
    return case_store.activity


async def seed_log(log: ActivityLog, count: int, **kwargs: Any) -> list[ActivityEntry]:
    rows = []
    for number in range(count):
        event = UiEventIn(
            type=kwargs.get("type", "document.extracted"),
            visibility=kwargs.get("visibility", "po"),
            summary=f"entry {number + 1}",
            actor=UiActor(id="svc:caseflow", role="fcrm_service", component="caseflow"),
        )
        row = await log.append(CASE, event)
        assert row is not None
        rows.append(row)
    return rows


# ── frames ──────────────────────────────────────────────────────────────────


def test_format_frame_is_an_unnamed_frame_with_the_envelope_the_web_validates() -> None:
    frame = format_frame(entry(7), "staff")
    assert frame is not None
    assert frame.startswith("id: 7\ndata: ")
    assert frame.endswith("\n\n")
    assert "event:" not in frame
    body = data_of(frame)
    assert body["type"] == "document.extracted"
    assert body["caseId"] == CASE
    assert body["seq"] == 7
    assert body["at"] == "2026-10-05T10:00:07Z"
    assert body["payload"]["summary"] == "summary 7"


def test_the_po_frame_leaves_out_the_actor_and_the_audit_sequence() -> None:
    po = format_frame(entry(1), "po")
    staff = format_frame(entry(1), "staff")
    assert po is not None
    assert staff is not None
    assert set(data_of(po)["payload"]) == {"summary", "detail"}
    assert {"actor", "record_seq"} <= set(data_of(staff)["payload"])


def test_an_analyst_visibility_entry_never_becomes_a_po_frame() -> None:
    assert format_frame(entry(1, visibility="analyst"), "po") is None
    assert format_frame(entry(1, visibility="analyst"), "staff") is not None


@pytest.mark.parametrize(
    "event_type",
    ["score.computed", "harness.step", "audit.record", "agent.done", "guard.permit"],
)
def test_a_blocked_type_is_withheld_from_the_po_even_when_written_as_all(
    event_type: str, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR, logger="fcrm_case.sse"):
        assert format_frame(entry(1, type=event_type, visibility="all"), "po") is None
    assert "withheld" in caplog.text
    assert (
        format_frame(entry(1, type=event_type, visibility="all"), "staff") is not None
    )


def test_a_restricted_payload_key_is_withheld_from_the_po() -> None:
    risky = entry(1, visibility="po", detail={"nested": [{"band": 3}]})
    assert format_frame(risky, "po") is None
    assert format_frame(entry(1, visibility="po", detail={"analyst_name": "R"}), "po")


# ── the hub ─────────────────────────────────────────────────────────────────


def test_publish_reaches_every_stream_of_the_case_and_no_other() -> None:
    hub = EventHub()
    with (
        hub.subscription(CASE) as first,
        hub.subscription(CASE) as second,
        hub.subscription("FCRM-2026-0001") as other,
    ):
        assert hub.publish(entry(1)) == 2
        assert first.queue.qsize() == second.queue.qsize() == 1
        assert other.queue.qsize() == 0
    assert hub.subscriber_count() == 0


def test_a_slow_consumer_is_dropped_and_the_writer_is_never_blocked() -> None:
    hub = EventHub(max_queue=2)
    with hub.subscription(CASE) as slow, hub.subscription(CASE) as quick:
        for number in range(1, 4):
            hub.publish(entry(number))
            quick.queue.get_nowait()
        assert slow.dropped is True
        assert quick.dropped is False
        assert hub.subscriber_count(CASE) == 1
        assert hub.publish(entry(4)) == 1


def test_publishing_to_a_case_with_no_stream_is_a_no_op() -> None:
    assert EventHub().publish(entry(1)) == 0


async def test_the_writer_persists_then_publishes_and_skips_an_unknown_case(
    log: ActivityLog, make_event: Callable[..., UiEventIn]
) -> None:
    hub = EventHub()
    writer = ActivityWriter(log, hub)
    with hub.subscription(CASE) as sub:
        written = await writer.append(CASE, make_event())
        assert written is not None
        assert sub.queue.get_nowait().seq == written.seq == 1
        assert await writer.append("FCRM-2026-9999", make_event()) is None
        assert sub.queue.empty()
        writer.publish(None)
        assert sub.queue.empty()


# ── the stream ──────────────────────────────────────────────────────────────


async def test_replay_after_a_gap_sends_exactly_what_the_client_missed(
    log: ActivityLog,
) -> None:
    await seed_log(log, 5)
    stream = case_event_stream(FakeRequest(), EventHub(), log, params(start_seq=2))
    frames = await collect(stream, lambda f: len(f) >= 4)
    assert frames[0] == OPEN_COMMENT
    assert seqs(frames) == [3, 4, 5]


async def test_a_fresh_connection_at_the_head_sends_nothing_old(
    log: ActivityLog,
) -> None:
    await seed_log(log, 3)
    stream = case_event_stream(FakeRequest(), EventHub(), log, params(start_seq=3))
    frames = await collect(stream, lambda f: HEARTBEAT_COMMENT in f)
    assert seqs(frames) == []


async def test_a_replay_pages_through_a_long_gap(log: ActivityLog) -> None:
    await seed_log(log, 7)
    paged = StreamParams(
        case_id=CASE,
        audience="staff",
        start_seq=0,
        heartbeat_s=0.01,
        max_lifetime_s=5.0,
        replay_page=3,
    )
    stream = case_event_stream(FakeRequest(), EventHub(), log, paged)
    frames = await collect(stream, lambda f: len(seqs(f)) >= 7)
    assert seqs(frames) == [1, 2, 3, 4, 5, 6, 7]


async def test_a_live_entry_is_delivered_once_even_when_the_replay_also_has_it(
    log: ActivityLog,
) -> None:
    hub = EventHub()
    stream = case_event_stream(FakeRequest(), hub, log, params(start_seq=0))
    task = asyncio.create_task(collect(stream, lambda f: HEARTBEAT_COMMENT in f[3:]))
    await asyncio.sleep(0)
    rows = await seed_log(log, 2)
    for row in rows:  # published after commit: replay or hub, never both on the wire
        hub.publish(row)
        hub.publish(row)
    frames = await task
    assert seqs(frames) == [1, 2]


async def test_a_live_entry_that_jumps_ahead_is_filled_from_the_log_in_order(
    log: ActivityLog,
) -> None:
    hub = EventHub()
    stream = case_event_stream(FakeRequest(), hub, log, params(start_seq=0))
    task = asyncio.create_task(collect(stream, lambda f: len(seqs(f)) >= 2))
    await asyncio.sleep(0.02)
    first, second = await seed_log(log, 2)
    hub.publish(second)  # entry 2 reaches the hub before entry 1
    hub.publish(first)
    assert seqs(await task) == [1, 2]


async def test_the_po_stream_never_carries_analyst_or_score_entries(
    log: ActivityLog, make_event: Callable[..., UiEventIn]
) -> None:
    for kind, visibility in (
        ("document.extracted", "po"),
        ("score.computed", "analyst"),
        ("score.computed", "all"),  # wrong visibility: the type fence still holds
        ("gate.opened", "analyst"),
        ("case.stage_changed", "all"),
    ):
        await log.append(CASE, make_event(type=kind, visibility=visibility))
    po = await collect(
        case_event_stream(FakeRequest(), EventHub(), log, params("po")),
        lambda f: HEARTBEAT_COMMENT in f,
    )
    staff = await collect(
        case_event_stream(FakeRequest(), EventHub(), log, params("staff")),
        lambda f: HEARTBEAT_COMMENT in f,
    )
    assert [data_of(f)["type"] for f in po if f.startswith("id: ")] == [
        "document.extracted",
        "case.stage_changed",
    ]
    assert seqs(staff) == [1, 2, 3, 4, 5]
    assert "score" not in "".join(po)


async def test_an_idle_stream_sends_heartbeat_comments(log: ActivityLog) -> None:
    stream = case_event_stream(FakeRequest(), EventHub(), log, params())
    frames = await collect(stream, lambda f: f.count(HEARTBEAT_COMMENT) >= 2)
    assert frames[0] == OPEN_COMMENT
    assert frames.count(HEARTBEAT_COMMENT) == 2


async def test_the_stream_stops_and_unsubscribes_when_the_client_disconnects(
    log: ActivityLog,
) -> None:
    hub = EventHub()
    frames = [
        frame
        async for frame in case_event_stream(
            FakeRequest(disconnect_after=1), hub, log, params()
        )
    ]
    assert frames == [OPEN_COMMENT, HEARTBEAT_COMMENT]
    assert hub.subscriber_count() == 0


async def test_closing_the_stream_early_unsubscribes(log: ActivityLog) -> None:
    hub = EventHub()
    stream = case_event_stream(FakeRequest(), hub, log, params())
    await collect(stream, lambda f: len(f) >= 1)
    assert hub.subscriber_count() == 0


async def test_the_stream_ends_when_its_lifetime_is_up(log: ActivityLog) -> None:
    hub = EventHub()
    frames = [
        frame
        async for frame in case_event_stream(
            FakeRequest(), hub, log, params(heartbeat_s=0.01, max_lifetime_s=0.05)
        )
    ]
    assert frames[0] == OPEN_COMMENT
    assert hub.subscriber_count() == 0


async def test_a_dropped_consumer_ends_its_stream_so_the_client_reconnects(
    log: ActivityLog,
) -> None:
    hub = EventHub(max_queue=1)
    stream = case_event_stream(FakeRequest(), hub, log, params(heartbeat_s=5.0))
    frames: list[str] = []

    async def read() -> None:
        async for frame in stream:
            frames.append(frame)

    task = asyncio.create_task(read())
    await asyncio.sleep(0.02)
    for number in (1, 2, 3):  # the one-slot queue overflows on the second
        hub.publish(entry(number))
    async with asyncio.timeout(2.0):
        await task
    assert hub.subscriber_count() == 0


class BrokenLog:
    """A log whose reads fail like a Mongo outage."""

    async def ensure_indexes(self) -> None:
        return None

    async def append(self, case_id: str, event: UiEventIn) -> Optional[ActivityEntry]:
        raise ConnectionFailure("down")

    async def since(
        self,
        case_id: str,
        after_seq: int,
        *,
        audience: Optional[Audience] = None,
        limit: int = 500,
    ) -> list[ActivityEntry]:
        raise ConnectionFailure("down")


async def test_the_stream_ends_cleanly_when_the_log_cannot_be_read(
    caplog: pytest.LogCaptureFixture,
) -> None:
    hub = EventHub()
    with caplog.at_level(logging.WARNING, logger="fcrm_case.sse"):
        frames = [
            frame
            async for frame in case_event_stream(
                FakeRequest(), hub, BrokenLog(), params()
            )
        ]
    assert frames == [OPEN_COMMENT]
    assert hub.subscriber_count() == 0
    assert "could not be read" in caplog.text
