"""fcrm_api.middleware.HttpMetricsMiddleware: each request of a mounted service
is counted and timed by route template and status, probes are left out, and
recording never changes the response.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fcrm_api import middleware
from fcrm_api.main import create_app
from fcrm_api.middleware import HttpMetricsMiddleware
from fcrm_common.metrics import FcrmMetrics, get_metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader


@pytest.fixture
def http_reader() -> InMemoryMetricReader:
    """An in-memory reader for one test's HTTP metrics."""
    return InMemoryMetricReader()


def _app(metrics: FcrmMetrics) -> FastAPI:
    app = FastAPI()

    @app.get("/cases/{case_id}/items/{item_id}")
    async def item(case_id: str, item_id: str) -> dict[str, str]:
        return {"case_id": case_id, "item_id": item_id}

    @app.get("/boom")
    async def boom() -> dict[str, str]:
        raise RuntimeError("handler failed")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> dict[str, str]:
        return {"status": "ok"}

    app.add_middleware(HttpMetricsMiddleware, component_id="S1", metrics=metrics)
    return app


def _client(reader: InMemoryMetricReader) -> TestClient:
    metrics = get_metrics(MeterProvider(metric_readers=[reader]))
    return TestClient(_app(metrics), raise_server_exceptions=False)


def _points(reader: InMemoryMetricReader, name: str) -> list[Any]:
    data = reader.get_metrics_data()
    if data is None:
        return []
    return [
        point
        for rm in data.resource_metrics
        for sm in rm.scope_metrics
        for metric in sm.metrics
        if metric.name == name
        for point in metric.data.data_points
    ]


def _requests(reader: InMemoryMetricReader) -> dict[tuple[str, str], int]:
    return {
        (str(p.attributes["route"]), str(p.attributes["status"])): int(p.value)
        for p in _points(reader, "fcrm_http_requests_total")
    }


def test_the_route_label_is_the_template_not_the_raw_path(
    http_reader: InMemoryMetricReader,
) -> None:
    client = _client(http_reader)

    client.get("/cases/FCRM-2026-0151/items/itm_1")
    client.get("/cases/FCRM-2026-0152/items/itm_2")

    [point] = _points(http_reader, "fcrm_http_requests_total")
    assert dict(point.attributes) == {
        "service": "S1",
        "method": "GET",
        "route": "/cases/{case_id}/items/{item_id}",
        "status": "200",
    }
    assert point.value == 2


def test_an_unmatched_path_is_one_unmatched_404_series(
    http_reader: InMemoryMetricReader,
) -> None:
    client = _client(http_reader)

    assert client.get("/nowhere/1").status_code == 404
    assert client.get("/nowhere/2").status_code == 404

    assert _requests(http_reader) == {("unmatched", "404"): 2}


def test_a_raising_route_is_recorded_as_500(
    http_reader: InMemoryMetricReader,
) -> None:
    response = _client(http_reader).get("/boom")

    assert response.status_code == 500
    assert _requests(http_reader) == {("/boom", "500"): 1}


def test_the_exception_still_reaches_the_server(
    http_reader: InMemoryMetricReader,
) -> None:
    metrics = get_metrics(MeterProvider(metric_readers=[http_reader]))

    with pytest.raises(RuntimeError, match="handler failed"):
        TestClient(_app(metrics)).get("/boom")
    assert _requests(http_reader) == {("/boom", "500"): 1}


def test_health_and_readiness_probes_are_not_recorded(
    http_reader: InMemoryMetricReader,
) -> None:
    client = _client(http_reader)

    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz").status_code == 200

    assert _points(http_reader, "fcrm_http_requests_total") == []
    assert _points(http_reader, "fcrm_http_latency_ms") == []


def test_one_latency_sample_per_recorded_request(
    http_reader: InMemoryMetricReader,
) -> None:
    client = _client(http_reader)

    client.get("/cases/A/items/1")
    client.get("/cases/B/items/2")
    client.get("/nowhere")

    latency = {
        str(p.attributes["route"]): p
        for p in _points(http_reader, "fcrm_http_latency_ms")
    }
    assert latency["/cases/{case_id}/items/{item_id}"].count == 2
    assert latency["unmatched"].count == 1
    assert sum(p.count for p in latency.values()) == sum(
        _requests(http_reader).values()
    )
    assert all(p.sum >= 0 for p in latency.values())


class _BrokenCounter:
    """A counter whose ``add`` fails the way a bad attribute value would."""

    def add(self, amount: int, attributes: Any = None) -> None:
        raise TypeError("bad attribute")


def test_a_metric_failure_never_changes_the_response() -> None:
    broken = dataclasses.replace(
        get_metrics(MeterProvider()), http_requests_total=_BrokenCounter()
    )

    response = TestClient(_app(broken)).get("/cases/A/items/1")

    assert response.status_code == 200
    assert response.json() == {"case_id": "A", "item_id": "1"}


def test_lifespan_scopes_pass_through_untouched(
    http_reader: InMemoryMetricReader,
) -> None:
    started: list[bool] = []

    async def lifespan(_: FastAPI) -> Any:
        started.append(True)
        yield

    app = FastAPI(lifespan=lifespan)
    app.add_middleware(
        HttpMetricsMiddleware,
        component_id="S1",
        metrics=get_metrics(MeterProvider(metric_readers=[http_reader])),
    )
    with TestClient(app):
        pass

    assert started == [True]
    assert _points(http_reader, "fcrm_http_requests_total") == []


def test_every_mounted_service_carries_the_metrics_middleware() -> None:
    app = create_app()

    for component_id, sub in app.state.mounted.items():
        installed = [
            m.kwargs.get("component_id")
            for m in sub.user_middleware
            if m.cls is HttpMetricsMiddleware
        ]
        assert installed == [component_id]


def test_a_mounted_service_records_its_unprefixed_template(
    http_reader: InMemoryMetricReader, monkeypatch: pytest.MonkeyPatch
) -> None:
    metrics = get_metrics(MeterProvider(metric_readers=[http_reader]))
    monkeypatch.setattr(middleware, "get_metrics", lambda: metrics)
    app = create_app()
    scoring = app.state.mounted["S2"]

    @scoring.get("/_probe/{item}")
    async def probe(item: str) -> dict[str, str]:
        return {"item": item}

    response = TestClient(app).get("/api/fcrm/scoring/_probe/abc")

    assert response.status_code == 200
    [point] = _points(http_reader, "fcrm_http_requests_total")
    assert dict(point.attributes) == {
        "service": "S2",
        "method": "GET",
        "route": "/_probe/{item}",
        "status": "200",
    }
