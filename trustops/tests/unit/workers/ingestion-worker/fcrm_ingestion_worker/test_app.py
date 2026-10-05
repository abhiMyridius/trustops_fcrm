"""H3 as a harness unit (:func:`fcrm_ingestion_worker.app.build_unit`) and its
environment contract (:class:`~fcrm_ingestion_worker.settings.IngestionSettings`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import pytest
from conftest import FakeConsumed, FakeS3Client, RecordingPublisher
from faststream.rabbit import RabbitBroker
from fcrm_common.broker import FastStreamPublisher
from fcrm_common.mongo import get_client
from fcrm_common.objectstore import ObjectStore
from fcrm_common.settings import MissingSettingsError
from fcrm_ingestion_worker.app import build_app, build_unit, wire_subscriber
from fcrm_ingestion_worker.consumer import IngestionConsumer
from fcrm_ingestion_worker.guard1 import load_terms
from fcrm_ingestion_worker.settings import IngestionSettings

ENV_NAMES = (
    "MONGODB_URI",
    "MONGODB_DB",
    "RABBITMQ_URL",
    "S3_REGION",
    "S3_BUCKET_UPLOADS",
    "UPLOADS_PER_FILE_TIMEOUT_SECONDS",
    "PDF_ENGINE",
    "PDF_MIN_CHARS_PER_PAGE",
    "GUARD1_ALLOWLIST_FILE",
    "PREFETCH",
    "OPA_URL",
    "SERVICE_TOKEN_SECRET",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def _settings(**overrides: Any) -> IngestionSettings:
    values: dict[str, Any] = {
        "mongodb_uri": "mongodb://localhost:27017/?replicaSet=rs0",
        "rabbitmq_url": "amqp://guest:guest@localhost:5672/",
        **overrides,
    }
    return IngestionSettings(**values)


# --- settings -----------------------------------------------------------------------


def test_the_defaults_are_the_ones_the_design_gives() -> None:
    settings = _settings()
    assert settings.app_name == "fcrm-ingestion-worker"
    assert settings.mongodb_db == "fcrm_harness"
    assert settings.health_port == 9102
    assert settings.prefetch == 2
    assert settings.uploads_per_file_timeout_seconds == 120.0
    assert settings.pdf_engine == "pdfplumber"
    assert settings.pdf_min_chars_per_page is None
    assert settings.guard1_allowlist_file is None
    assert settings.s3_bucket_uploads == "uploads"
    assert settings.s3_bucket_canonical == "canonical"


def test_it_needs_mongo_the_broker_and_the_store_but_no_auth_settings() -> None:
    """H3 calls no peer service and verifies no token: it does not need
    ``OPA_URL``, an identity URL or the service-token secret.
    """
    settings = IngestionSettings.load(
        mongodb_uri="mongodb://localhost:27017/?replicaSet=rs0",
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
    )
    assert settings.opa_url is None


def test_a_missing_broker_url_fails_fast_naming_it() -> None:
    with pytest.raises(MissingSettingsError) as info:
        IngestionSettings.load(mongodb_uri="mongodb://localhost:27017/?replicaSet=rs0")
    assert info.value.missing == ["RABBITMQ_URL"]


def test_every_s3_variable_is_required_and_defaults_so_the_stack_runs_bare() -> None:
    required = set(IngestionSettings.required_settings)
    assert {"S3_BUCKET_UPLOADS", "S3_BUCKET_CANONICAL", "S3_REGION"} <= required
    assert IngestionSettings.required_any_of == ()


def test_the_values_come_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MONGODB_URI", "mongodb://m:27017/?replicaSet=rs0")
    monkeypatch.setenv("RABBITMQ_URL", "amqp://r:5672/")
    monkeypatch.setenv("UPLOADS_PER_FILE_TIMEOUT_SECONDS", "30")
    monkeypatch.setenv("PDF_ENGINE", "pypdf")
    monkeypatch.setenv("PDF_MIN_CHARS_PER_PAGE", "12")
    monkeypatch.setenv("S3_BUCKET_UPLOADS", "uploads-eu")

    settings = IngestionSettings.load()

    assert settings.uploads_per_file_timeout_seconds == 30
    assert (settings.pdf_engine, settings.pdf_min_chars_per_page) == ("pypdf", 12)
    assert settings.s3_bucket_uploads == "uploads-eu"


@pytest.mark.parametrize(
    "overrides",
    [
        {"pdf_engine": "tesseract"},
        {"uploads_per_file_timeout_seconds": 0},
        {"prefetch": 0},
        {"pdf_min_chars_per_page": -1},
    ],
)
def test_an_unusable_value_is_refused(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValueError):  # noqa: PT011 - pydantic's ValidationError
        _settings(**overrides)


# --- the unit -----------------------------------------------------------------


def _unit(
    settings: IngestionSettings, store: Optional[ObjectStore] = None
) -> tuple[Any, RabbitBroker]:
    broker = RabbitBroker("amqp://guest:guest@localhost:5672/")
    unit = build_unit(
        settings,
        mongo=get_client(str(settings.mongodb_uri)),
        publisher=FastStreamPublisher(broker),
        object_store=store,
    )
    return unit, broker


def test_the_unit_is_h3_and_wires_the_ingestion_queue_with_its_prefetch() -> None:
    unit, broker = _unit(_settings(prefetch=3), ObjectStore(FakeS3Client()))

    unit.wire(broker)

    assert unit.component_id == "H3"
    [subscriber] = broker.subscribers
    assert subscriber.queue.name == "ingestion-worker.run"
    assert subscriber.channel.prefetch_count == 3


def test_the_unit_starts_one_hook_and_holds_no_client_to_close() -> None:
    unit, _ = _unit(_settings(), ObjectStore(FakeS3Client()))
    assert len(unit.after_startup) == 1
    assert unit.on_shutdown == ()


async def test_the_index_hook_creates_the_dedupe_indexes(
    consumed: FakeConsumed, publisher: RecordingPublisher
) -> None:
    mongo = {"fcrm_harness": {"consumed_messages": consumed}}
    unit = build_unit(
        _settings(),
        mongo=mongo,  # type: ignore[arg-type]
        publisher=publisher,
        object_store=ObjectStore(FakeS3Client()),
    )

    await unit.after_startup[0]()

    assert [i.document["name"] for i in consumed.indexes] == [
        "message_id_consumer_unique",
        "consumed_at_ttl",
    ]


async def test_the_uploads_check_probes_the_bucket_and_is_not_critical() -> None:
    s3 = FakeS3Client()
    unit, _ = _unit(_settings(s3_bucket_uploads="uploads-eu"), ObjectStore(s3))
    [check] = unit.checks

    await check.fn()

    assert (check.name, check.critical) == ("uploads_bucket", False)
    assert s3.heads == ["uploads-eu"]


async def test_the_uploads_check_fails_when_the_store_is_down() -> None:
    s3 = FakeS3Client()
    s3.unavailable = True
    unit, _ = _unit(_settings(), ObjectStore(s3))
    [check] = unit.checks
    with pytest.raises(Exception, match="object store"):  # noqa: PT011
        await check.fn()


def test_without_an_injected_store_the_real_one_is_built_from_the_settings() -> None:
    unit, _ = _unit(_settings(s3_endpoint_url="http://localhost:9000"))
    assert unit.component_id == "H3"  # built without opening a connection


def test_the_allowlist_file_is_read_at_startup(tmp_path: Path) -> None:
    allowlist = tmp_path / "keep.txt"
    allowlist.write_text(
        "# seeded users\nPriya Nair\n\n  Rahul Iyer  \n", encoding="utf-8"
    )
    assert load_terms(str(allowlist)) == ("Priya Nair", "Rahul Iyer")

    unit, _ = _unit(
        _settings(guard1_allowlist_file=str(allowlist)), ObjectStore(FakeS3Client())
    )
    assert unit.component_id == "H3"


def test_an_unreadable_allowlist_stops_the_unit_naming_the_variable(
    tmp_path: Path,
) -> None:
    with pytest.raises(MissingSettingsError, match="GUARD1_ALLOWLIST_FILE"):
        _unit(
            _settings(guard1_allowlist_file=str(tmp_path / "missing.txt")),
            ObjectStore(FakeS3Client()),
        )


def test_wire_subscriber_binds_only_the_ingest_run_key() -> None:
    broker = RabbitBroker("amqp://guest:guest@localhost:5672/")
    consumer = IngestionConsumer(
        None,  # type: ignore[arg-type]
        RecordingPublisher(),
        store=ObjectStore(FakeS3Client()),
        timeout_s=1.0,
    )

    wire_subscriber(broker, consumer, prefetch=None)

    [subscriber] = broker.subscribers
    assert subscriber.queue.routing_key == "ingest.run"


def test_build_app_returns_one_asgi_app_with_health_routes() -> None:
    app = build_app(_settings())
    assert {sub.queue.name for sub in app.broker.subscribers} == {
        "ingestion-worker.run"
    }
