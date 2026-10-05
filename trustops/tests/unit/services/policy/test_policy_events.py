"""S3 hearing H4's ``index.built``: the clause vectors it searches are swapped
for the ones H4 built, a fixture that cannot be used leaves the old ones
serving, and the answer is recorded either way.

H4's side is real here: the fixture is dumped from a real in-memory vector
store built by H4's own ``seedload`` over the 197-clause corpus.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import pytest
from evoq_context.core.config import ContextConfig
from evoq_context.llm.client import LLMClient
from evoq_context.vector.factory import create_vector_store
from fcrm_common.errors import NotFoundError
from fcrm_common.events import build_envelope
from fcrm_context_worker.config import FCRM_PROJECT, context_config_from_env
from fcrm_context_worker.fixture import encode_fixture, fixture_key
from fcrm_context_worker.seedload import seeded_store
from fcrm_contracts.events.envelope import Envelope
from fcrm_contracts.events.payloads import IndexBuilt, IndexFailed
from fcrm_policy.events import (
    POLICY_EVENTS_QUEUE,
    PolicyEventsConsumer,
    VectorFixtureReloader,
)
from fcrm_policy.search.vector import VectorClauseSource
from pydantic import ValidationError

DIMENSION = 64


class _Objects:
    """The slice of ``ObjectStore`` the reloader reads: ``get_bytes``."""

    def __init__(self, objects: Optional[dict[tuple[str, str], bytes]] = None) -> None:
        self.objects = objects or {}
        self.gets: list[tuple[str, str]] = []

    async def get_bytes(self, bucket: str, key: str) -> bytes:
        self.gets.append((bucket, key))
        if (bucket, key) not in self.objects:
            raise NotFoundError(f"no object {bucket}/{key}")
        return self.objects[(bucket, key)]


class _Status:
    """Records what ``IndexStatusStore`` would be told, and answers
    ``latest_built`` with ``latest``.
    """

    def __init__(self, latest: Optional[dict[str, Any]] = None) -> None:
        self.built: list[IndexBuilt] = []
        self.failed: list[IndexFailed] = []
        self.latest = latest

    async def latest_built(self, index: str) -> Optional[dict[str, Any]]:
        return self.latest

    async def mark_built(self, event: IndexBuilt, at: datetime) -> None:
        self.built.append(event)

    async def mark_failed(self, event: IndexFailed, at: datetime) -> None:
        self.failed.append(event)


class _Reloader:
    def __init__(self) -> None:
        self.events: list[IndexBuilt] = []

    async def reload(self, event: IndexBuilt) -> Optional[int]:
        self.events.append(event)
        return 1


def _config(tmp_path: Path, dimension: int = DIMENSION) -> ContextConfig:
    return context_config_from_env(
        {"VECTOR_BACKEND": "memory", "EMBEDDING_DIMENSION": str(dimension)},
        projects_root=tmp_path,
    )


@pytest.fixture
async def built_fixture(tmp_path: Path) -> bytes:
    """H4's fixture of the full corpus, as H4 would put it in the bucket."""
    store, _llm = await seeded_store(_config(tmp_path))
    return encode_fixture(store)  # type: ignore[arg-type]


@pytest.fixture
def empty_source(tmp_path: Path) -> tuple[VectorClauseSource, ContextConfig]:
    """S3's semantic source over an empty store, and the config behind it."""
    config = _config(tmp_path)
    store = create_vector_store(config, FCRM_PROJECT)
    assert store is not None
    return VectorClauseSource(store, LLMClient.from_config(config), config), config


def _built(index: str = "policy_clauses", **overrides: Any) -> IndexBuilt:
    fields: dict[str, Any] = {
        "job_id": "job_x",
        "index": index,
        "version": 14,
        "vector_count": 197,
        "fixture_key": fixture_key(index, 14),
    }
    fields.update(overrides)
    return IndexBuilt(**fields)


async def _ranked(source: VectorClauseSource) -> list[str]:
    hits = await source.rank("cash pickup agent", filters=None, top_k=5)
    return [h.clause_id for h in hits]


async def test_reload_swaps_in_the_vectors_h4_built(
    built_fixture: bytes, empty_source: tuple[VectorClauseSource, ContextConfig]
) -> None:
    source, config = empty_source
    assert await _ranked(source) == []
    objects = _Objects({("vectors", "policy_clauses/14.json"): built_fixture})
    reloader = VectorFixtureReloader(
        store=objects,  # type: ignore[arg-type]
        bucket="vectors",
        source=source,
        config=config,
    )

    served = await reloader.reload(_built())

    assert served == 197
    assert len(await _ranked(source)) == 5
    assert objects.gets == [("vectors", "policy_clauses/14.json")]


async def test_a_fixture_at_another_dimension_is_refused_and_the_old_index_serves(
    built_fixture: bytes, tmp_path: Path
) -> None:
    config = _config(tmp_path, dimension=32)
    store = create_vector_store(config, FCRM_PROJECT)
    assert store is not None
    source = VectorClauseSource(store, LLMClient.from_config(config), config)
    reloader = VectorFixtureReloader(
        store=_Objects({("vectors", "policy_clauses/14.json"): built_fixture}),  # type: ignore[arg-type]
        bucket="vectors",
        source=source,
        config=config,
    )

    assert await reloader.reload(_built()) is None
    assert await _ranked(source) == []


async def test_a_fixture_that_is_not_json_is_refused(
    empty_source: tuple[VectorClauseSource, ContextConfig],
) -> None:
    source, config = empty_source
    reloader = VectorFixtureReloader(
        store=_Objects({("vectors", "policy_clauses/14.json"): b"{not json"}),  # type: ignore[arg-type]
        bucket="vectors",
        source=source,
        config=config,
    )
    assert await reloader.reload(_built()) is None


async def test_an_event_without_a_fixture_reads_nothing(
    empty_source: tuple[VectorClauseSource, ContextConfig],
) -> None:
    source, config = empty_source
    objects = _Objects()
    reloader = VectorFixtureReloader(
        store=objects,  # type: ignore[arg-type]
        bucket="vectors",
        source=source,
        config=config,
    )
    assert await reloader.reload(_built(fixture_key=None)) is None
    assert objects.gets == []


async def test_a_fixture_missing_from_the_bucket_is_an_error_to_retry(
    empty_source: tuple[VectorClauseSource, ContextConfig],
) -> None:
    source, config = empty_source
    reloader = VectorFixtureReloader(
        store=_Objects(),  # type: ignore[arg-type]
        bucket="vectors",
        source=source,
        config=config,
    )
    with pytest.raises(NotFoundError):
        await reloader.reload(_built())


# ── starting from the newest fixture ──────────────────────────────────────


def _row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "_id": "policy_clauses:14",
        "index": "policy_clauses",
        "version": 14,
        "status": "built",
        "vector_count": 197,
        "fixture_key": "policy_clauses/14.json",
        "job_id": "job_x",
    }
    row.update(overrides)
    return row


async def test_startup_loads_the_newest_fixture_h4_left(
    built_fixture: bytes, empty_source: tuple[VectorClauseSource, ContextConfig]
) -> None:
    source, config = empty_source
    reloader = VectorFixtureReloader(
        store=_Objects({("vectors", "policy_clauses/14.json"): built_fixture}),  # type: ignore[arg-type]
        bucket="vectors",
        source=source,
        config=config,
    )

    served = await reloader.reload_latest(_Status(_row()))  # type: ignore[arg-type]

    assert served == 197
    assert len(await _ranked(source)) == 5


async def test_startup_with_no_build_yet_leaves_the_seeded_vectors(
    empty_source: tuple[VectorClauseSource, ContextConfig],
) -> None:
    source, config = empty_source
    objects = _Objects()
    reloader = VectorFixtureReloader(
        store=objects,  # type: ignore[arg-type]
        bucket="vectors",
        source=source,
        config=config,
    )

    assert await reloader.reload_latest(_Status(None)) is None  # type: ignore[arg-type]
    assert objects.gets == []


async def test_startup_survives_a_fixture_that_is_gone_from_the_bucket(
    empty_source: tuple[VectorClauseSource, ContextConfig],
    caplog: pytest.LogCaptureFixture,
) -> None:
    source, config = empty_source
    reloader = VectorFixtureReloader(
        store=_Objects(),  # type: ignore[arg-type]
        bucket="vectors",
        source=source,
        config=config,
    )

    assert await reloader.reload_latest(_Status(_row())) is None  # type: ignore[arg-type]
    assert "serving the seeded vectors" in caplog.text


# ── the consumer ──────────────────────────────────────────────────────────


@pytest.fixture
def consumer_parts(
    recording_publisher: Any,
) -> tuple[PolicyEventsConsumer, _Status, _Reloader]:
    status, reloader = _Status(), _Reloader()
    consumer = PolicyEventsConsumer(
        None,  # type: ignore[arg-type]
        recording_publisher,
        status=status,  # type: ignore[arg-type]
        reloader=reloader,  # type: ignore[arg-type]
    )
    return consumer, status, reloader


def _envelope(event_type: str, payload: dict[str, Any]) -> Envelope:
    return build_envelope(event_type, payload)


def test_the_consumer_reads_the_policy_events_queue(
    consumer_parts: tuple[PolicyEventsConsumer, _Status, _Reloader],
) -> None:
    assert consumer_parts[0].queue == POLICY_EVENTS_QUEUE == "policy-service.events"


async def test_built_clauses_are_recorded_and_reloaded(
    consumer_parts: tuple[PolicyEventsConsumer, _Status, _Reloader],
) -> None:
    consumer, status, reloader = consumer_parts
    built = _built()

    await consumer.handle(_envelope("index.built", built.model_dump(mode="json")), None)

    assert status.built == [built]
    assert reloader.events == [built]


@pytest.mark.parametrize("index", ["precedent_cases", "controls"])
async def test_built_precedents_and_controls_are_recorded_but_nothing_is_reloaded(
    consumer_parts: tuple[PolicyEventsConsumer, _Status, _Reloader], index: str
) -> None:
    consumer, status, reloader = consumer_parts
    built = _built(index)

    await consumer.handle(_envelope("index.built", built.model_dump(mode="json")), None)

    assert status.built == [built]
    assert reloader.events == []


async def test_a_failed_build_is_recorded_and_reloads_nothing(
    consumer_parts: tuple[PolicyEventsConsumer, _Status, _Reloader],
) -> None:
    consumer, status, reloader = consumer_parts
    failed = IndexFailed(job_id="job_x", index="policy_clauses", code="no_records")

    await consumer.handle(
        _envelope("index.failed", failed.model_dump(mode="json")), None
    )

    assert status.failed == [failed]
    assert status.built == []
    assert reloader.events == []


async def test_an_event_that_is_not_an_index_answer_is_ignored(
    consumer_parts: tuple[PolicyEventsConsumer, _Status, _Reloader],
) -> None:
    consumer, status, reloader = consumer_parts
    await consumer.handle(_envelope("index.build", {"job_id": "job_x"}), None)
    assert (status.built, status.failed, reloader.events) == ([], [], [])


async def test_a_malformed_answer_is_an_error_so_it_is_retried_then_parked(
    consumer_parts: tuple[PolicyEventsConsumer, _Status, _Reloader],
) -> None:
    consumer, _status, _reloader = consumer_parts
    with pytest.raises(ValidationError):
        await consumer.handle(_envelope("index.built", {"index": "nope"}), None)


async def test_a_consumer_with_no_reloader_still_records_the_build(
    recording_publisher: Any,
) -> None:
    status = _Status()
    consumer = PolicyEventsConsumer(
        None,  # type: ignore[arg-type]
        recording_publisher,
        status=status,  # type: ignore[arg-type]
        reloader=None,
    )
    built = _built()
    await consumer.handle(_envelope("index.built", built.model_dump(mode="json")), None)
    assert status.built == [built]
