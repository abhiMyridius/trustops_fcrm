"""S1's relay wiring: with a publisher in ``ServiceDeps`` the outbox relay runs
for the life of the service under the ``outbox-relay:case`` lease and the
``outbox`` readiness check exists; without one S1 runs without publishing and
says so. Against the in-memory fakes in ``tests/conftest.py``; the same wiring on
a real replica set is in ``tests/integration/live_mongo/test_case_foundation_live.py``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import AsyncExitStack
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from fcrm_case import lifespan as case_lifespan
from fcrm_case.lifespan import OUTBOX_RELAY_LEASE_NAME, start_relay
from fcrm_common.health import ReadinessProbe
from fcrm_common.outbox import Outbox

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

    monkeypatch.setattr(case_lifespan, "MongoLease", fake_lease)
    return made


def deps(publisher: Any, lease_holder: str = "relay_host_1") -> Any:
    return SimpleNamespace(publisher=publisher, lease_holder=lease_holder)


async def wait_until(condition: Callable[[], bool], timeout_s: float = 2.0) -> None:
    async with asyncio.timeout(timeout_s):
        while not condition():
            await asyncio.sleep(0.005)


async def test_a_publisher_starts_the_relay_under_the_case_lease(
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
    assert [e.type for e in recording_publisher.sent] == [
        "case.created",
        "case.created",
    ]
    assert leases_made == [(OUTBOX_RELAY_LEASE_NAME, "relay_host_1")]
    assert OUTBOX_RELAY_LEASE_NAME == "outbox-relay:case"


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


async def test_without_a_publisher_s1_runs_unrelayed_and_says_so(
    fake_outbox: FakeOutboxCollection,
    queue_events: Callable[..., Any],
    leases_made: list[tuple[str, str]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    probe = ReadinessProbe()
    await queue_events(1)
    with caplog.at_level(logging.WARNING, logger="fcrm_case.lifespan"):
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
    status, body = await probe.run()
    assert status == 200
    assert "outbox" not in body["checks"]


# ── the object store the uploads go to ──────────────────────────────────────


class PingedStore:
    """An object store whose bucket answers (or does not)."""

    def __init__(self, up: bool) -> None:
        self.up = up
        self.pinged: list[str] = []

    async def ping(self, bucket: str) -> None:
        self.pinged.append(bucket)
        if not self.up:
            raise ConnectionError("down")


async def test_the_uploads_bucket_check_is_not_critical() -> None:
    """A MinIO outage fails an upload, not the whole service: the check reports
    ``degraded`` (still 200), never ``fail``.
    """
    store = PingedStore(up=False)
    probe = ReadinessProbe()
    probe.add(case_lifespan._uploads_check(store, "uploads"))  # type: ignore[arg-type]
    status, body = await probe.run()
    assert (status, body["status"]) == (200, "degraded")
    assert body["checks"]["uploads_bucket"]["ok"] is False
    assert store.pinged == ["uploads"]
    store.up = True
    assert (await probe.run())[1]["status"] == "ok"


def test_demo_documents_are_read_from_a_named_directory(tmp_path: Any) -> None:
    from fcrm_case.po_documents import DirectoryFixtures
    from fcrm_case.settings import CaseSettings

    settings = CaseSettings(demo_documents_dir=str(tmp_path))
    fixtures = case_lifespan._build_fixtures(settings, object())  # type: ignore[arg-type]
    assert isinstance(fixtures, DirectoryFixtures)


def test_without_a_directory_they_come_from_the_staged_prefix() -> None:
    from fcrm_case.po_documents import ObjectFixtures
    from fcrm_case.settings import CaseSettings

    fixtures = case_lifespan._build_fixtures(CaseSettings(), object())  # type: ignore[arg-type]
    assert isinstance(fixtures, ObjectFixtures)


def test_a_directory_that_does_not_exist_falls_back_and_says_so(
    caplog: pytest.LogCaptureFixture, tmp_path: Any
) -> None:
    from fcrm_case.po_documents import ObjectFixtures
    from fcrm_case.settings import CaseSettings

    settings = CaseSettings(demo_documents_dir=str(tmp_path / "missing"))
    with caplog.at_level(logging.WARNING, logger="fcrm_case.lifespan"):
        fixtures = case_lifespan._build_fixtures(settings, object())  # type: ignore[arg-type]
    assert isinstance(fixtures, ObjectFixtures)
    assert "DEMO_DOCUMENTS_DIR is not a directory" in caplog.text


def test_the_case_settings_require_the_object_store_variables() -> None:
    from fcrm_case.settings import CaseSettings
    from fcrm_common.settings import S3Settings

    assert set(S3Settings.s3_required_settings) <= set(CaseSettings.required_settings)
