"""``fcrm_common.outbox.RelayRunner``: the relay as a background task a service
starts, stops and reports on, against the in-memory fakes in ``tests/conftest.py``.
(The relay's own ordering and leadership behaviour is in
``test_common_outbox.py``; the same paths on a real replica set are in
``test_common_mongo_live.py``.)
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Optional

import pytest
from fcrm_common.outbox import (
    OutboxRelay,
    RelayLagError,
    RelayRunner,
    RelayStoppedError,
)
from fcrm_common.time import utcnow

if TYPE_CHECKING:
    from conftest import FakeOutboxCollection, FakeRelayLease, RecordingPublisher


def runner_for(
    collection: FakeOutboxCollection,
    lease: FakeRelayLease,
    publisher: RecordingPublisher,
    *,
    max_lag_s: float = 60.0,
) -> RelayRunner:
    relay = OutboxRelay(
        collection,  # type: ignore[arg-type]
        lease,
        publisher,
        interval_s=0.01,
        renew_every_s=0.01,
    )
    return RelayRunner(relay, name="test", max_lag_s=max_lag_s)


async def wait_for(condition: Callable[[], bool], timeout_s: float = 2.0) -> None:
    async with asyncio.timeout(timeout_s):
        while not condition():
            await asyncio.sleep(0.005)


async def test_the_runner_publishes_what_is_queued_and_releases_the_lease_on_stop(
    fake_outbox: FakeOutboxCollection,
    make_relay_lease: Callable[..., FakeRelayLease],
    recording_publisher: RecordingPublisher,
    queue_events: Callable[..., Any],
) -> None:
    queued = await queue_events(3)
    lease = make_relay_lease()
    runner = runner_for(fake_outbox, lease, recording_publisher)
    runner.start()
    await wait_for(lambda: len(recording_publisher.sent) == 3)
    assert [e.event_id for e in recording_publisher.sent] == [
        e.event_id for e in queued
    ]
    assert all(d["published_at"] is not None for d in fake_outbox.docs)
    await runner.stop()
    assert lease.released is True
    assert runner.running is False


async def test_events_queued_after_the_runner_started_are_published_too(
    fake_outbox: FakeOutboxCollection,
    make_relay_lease: Callable[..., FakeRelayLease],
    recording_publisher: RecordingPublisher,
    queue_events: Callable[..., Any],
) -> None:
    runner = runner_for(fake_outbox, make_relay_lease(), recording_publisher)
    runner.start()
    await asyncio.sleep(0.03)
    await queue_events(1)
    await wait_for(lambda: len(recording_publisher.sent) == 1)
    await runner.stop()


async def test_stop_drains_what_is_pending_before_it_returns(
    fake_outbox: FakeOutboxCollection,
    make_relay_lease: Callable[..., FakeRelayLease],
    recording_publisher: RecordingPublisher,
    queue_events: Callable[..., Any],
) -> None:
    runner = runner_for(fake_outbox, make_relay_lease(), recording_publisher)
    runner.start()
    await queue_events(2)
    await runner.stop()
    assert len(recording_publisher.sent) == 2


async def test_check_is_healthy_while_the_relay_runs_and_fails_outside_that(
    fake_outbox: FakeOutboxCollection,
    make_relay_lease: Callable[..., FakeRelayLease],
    recording_publisher: RecordingPublisher,
) -> None:
    runner = runner_for(fake_outbox, make_relay_lease(), recording_publisher)
    with pytest.raises(RelayStoppedError):
        await runner.check()
    runner.start()
    await runner.check()
    await runner.stop()
    with pytest.raises(RelayStoppedError):
        await runner.check()


async def test_check_fails_when_the_oldest_row_is_older_than_the_allowed_lag(
    fake_outbox: FakeOutboxCollection,
    make_relay_lease: Callable[..., FakeRelayLease],
    recording_publisher: RecordingPublisher,
    queue_events: Callable[..., Any],
) -> None:
    await queue_events(1)
    fake_outbox.docs[0]["created_at"] = utcnow() - timedelta(minutes=5)
    # A standby replica never publishes, so the row stays and the lag grows.
    runner = runner_for(fake_outbox, make_relay_lease(held=False), recording_publisher)
    runner.start()
    try:
        with pytest.raises(RelayLagError, match="behind"):
            await runner.check()
    finally:
        await runner.stop()


async def test_a_relay_that_dies_is_logged_and_reported_unhealthy(
    fake_outbox: FakeOutboxCollection,
    make_relay_lease: Callable[..., FakeRelayLease],
    recording_publisher: RecordingPublisher,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class DyingRelay(OutboxRelay):
        async def run(self, stop: asyncio.Event) -> None:
            raise RuntimeError("relay crashed")

    relay = DyingRelay(
        fake_outbox,  # type: ignore[arg-type]
        make_relay_lease(),
        recording_publisher,
    )
    runner = RelayRunner(relay, name="dying")
    with caplog.at_level(logging.ERROR, logger="fcrm_common.outbox"):
        runner.start()
        await wait_for(lambda: not runner.running)
        await asyncio.sleep(0)
    assert "stopped unexpectedly" in caplog.text
    with pytest.raises(RelayStoppedError):
        await runner.check()
    await runner.stop()


async def test_a_relay_that_will_not_stop_is_cancelled_after_the_timeout(
    fake_outbox: FakeOutboxCollection,
    make_relay_lease: Callable[..., FakeRelayLease],
    recording_publisher: RecordingPublisher,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class StubbornRelay(OutboxRelay):
        async def run(self, stop: asyncio.Event) -> None:
            await asyncio.sleep(60)

    relay = StubbornRelay(
        fake_outbox,  # type: ignore[arg-type]
        make_relay_lease(),
        recording_publisher,
    )
    runner = RelayRunner(relay, name="stubborn")
    runner.start()
    with caplog.at_level(logging.WARNING, logger="fcrm_common.outbox"):
        await runner.stop(timeout_s=0.05)
    assert "cancelling" in caplog.text
    assert runner.running is False


async def test_start_is_idempotent_stop_without_start_is_a_no_op_and_it_restarts(
    fake_outbox: FakeOutboxCollection,
    make_relay_lease: Callable[..., FakeRelayLease],
    recording_publisher: RecordingPublisher,
) -> None:
    runner = runner_for(fake_outbox, make_relay_lease(), recording_publisher)
    await runner.stop()
    runner.start()
    task: Optional[asyncio.Task[None]] = runner._task
    runner.start()
    assert runner._task is task
    await runner.stop()
    runner.start()
    assert runner.running is True
    await runner.stop()
