"""S5's relay wiring: with a publisher in ``ServiceDeps`` the outbox relay runs for
the life of the service under the ``outbox-relay:decision`` lease and the
``outbox`` readiness check exists; without one S5 runs without publishing and
says so (its rows wait in the outbox for a host with a broker). Against the
in-memory fakes in ``tests/conftest.py``; the same wiring on a real replica set
is in ``tests/integration/live_mongo/test_decision_outbox_live.py``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import AsyncExitStack
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from fcrm_common.health import ReadinessProbe
from fcrm_common.outbox import Outbox
from fcrm_decision import lifespan as decision_lifespan
from fcrm_decision.lifespan import OUTBOX_RELAY_LEASE_NAME, start_relay
from fcrm_decision.settings import DecisionSettings

if TYPE_CHECKING:
    from conftest import FakeOutboxCollection, FakeRelayLease, RecordingPublisher


@pytest.fixture
def leases_made(
    monkeypatch: pytest.MonkeyPatch, make_relay_lease: Callable[..., FakeRelayLease]
) -> list[tuple[str, str]]:
    """Replace ``MongoLease`` with a held fake and record ``(name, holder)``."""
    made: list[tuple[str, str]] = []

    def fake_lease(collection: Any, name: str, holder: str) -> FakeRelayLease:
        made.append((name, holder))
        return make_relay_lease()

    monkeypatch.setattr(decision_lifespan, "MongoLease", fake_lease)
    return made


def deps(publisher: Any, lease_holder: str = "relay_host_1") -> Any:
    return SimpleNamespace(publisher=publisher, lease_holder=lease_holder)


async def wait_until(condition: Callable[[], bool], timeout_s: float = 2.0) -> None:
    async with asyncio.timeout(timeout_s):
        while not condition():
            await asyncio.sleep(0.005)


def test_the_relay_lease_is_named_for_s5() -> None:
    assert OUTBOX_RELAY_LEASE_NAME == "outbox-relay:decision"


async def test_a_publisher_starts_the_relay_under_the_decision_lease(
    fake_outbox: FakeOutboxCollection,
    recording_publisher: RecordingPublisher,
    queue_events: Callable[..., Any],
    leases_made: list[tuple[str, str]],
) -> None:
    probe = ReadinessProbe()
    queued = await queue_events(2)
    async with AsyncExitStack() as stack:
        start_relay(
            stack,
            probe,
            Outbox(fake_outbox),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            deps(recording_publisher),
        )
        await wait_until(lambda: len(recording_publisher.sent) == 2)
        status, body = await probe.run()
        assert status == 200
        assert body["checks"]["outbox"]["ok"] is True
    assert [e.event_id for e in recording_publisher.sent] == [
        e.event_id for e in queued
    ]
    assert leases_made == [(OUTBOX_RELAY_LEASE_NAME, "relay_host_1")]


async def test_leaving_the_service_stops_the_relay_and_its_check_then_fails(
    fake_outbox: FakeOutboxCollection,
    recording_publisher: RecordingPublisher,
    leases_made: list[tuple[str, str]],
) -> None:
    probe = ReadinessProbe()
    async with AsyncExitStack() as stack:
        start_relay(
            stack,
            probe,
            Outbox(fake_outbox),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            deps(recording_publisher),
        )
    status, body = await probe.run()
    assert status == 503
    assert body["checks"]["outbox"]["ok"] is False


async def test_a_host_without_a_lease_holder_still_gets_a_unique_one(
    fake_outbox: FakeOutboxCollection,
    recording_publisher: RecordingPublisher,
    leases_made: list[tuple[str, str]],
) -> None:
    async with AsyncExitStack() as stack:
        start_relay(
            stack,
            ReadinessProbe(),
            Outbox(fake_outbox),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            deps(recording_publisher, lease_holder=""),
        )
    ((name, holder),) = leases_made
    assert name == OUTBOX_RELAY_LEASE_NAME
    assert holder.startswith("relay_")


async def test_without_a_publisher_s5_runs_unrelayed_and_says_so(
    fake_outbox: FakeOutboxCollection,
    queue_events: Callable[..., Any],
    leases_made: list[tuple[str, str]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    probe = ReadinessProbe()
    await queue_events(1)
    with caplog.at_level(logging.WARNING, logger="fcrm_decision.lifespan"):
        async with AsyncExitStack() as stack:
            start_relay(
                stack,
                probe,
                Outbox(fake_outbox),  # type: ignore[arg-type]
                object(),  # type: ignore[arg-type]
                deps(None, lease_holder=""),
            )
            await asyncio.sleep(0.05)
    assert "no publisher" in caplog.text
    assert leases_made == []
    assert fake_outbox.docs[0]["published_at"] is None  # waits for a host with a broker


def test_the_directory_is_unconfigured_when_nothing_says_where_x1_is(
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = DecisionSettings(mongodb_uri="mongodb://x")  # type: ignore[arg-type]
    with caplog.at_level(logging.WARNING, logger="fcrm_decision.lifespan"):
        directory, closable = decision_lifespan._build_directory(settings)
    assert closable is None
    assert type(directory).__name__ == "UnconfiguredCommitteeDirectory"
    assert "no identity URL" in caplog.text


def test_the_directory_reads_x1_when_the_url_is_known() -> None:
    settings = DecisionSettings(
        mongodb_uri="mongodb://x",  # type: ignore[arg-type]
        identity_service_url="http://api:8000/api/fcrm/auth",  # type: ignore[arg-type]
        service_token_secret="s" * 32,  # type: ignore[arg-type]
    )
    directory, closable = decision_lifespan._build_directory(settings)
    assert closable is directory
    assert type(directory).__name__ == "HttpCommitteeDirectory"
