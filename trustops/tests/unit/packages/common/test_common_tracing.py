"""fcrm_common.tracing: exporter selection honours any endpoint (the AEO
``svc.cluster.local`` bug), AMQP propagation, case spans.
"""

from __future__ import annotations

import pytest
from fcrm_common import tracing
from fcrm_common.logging import get_context
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://otel-collector:4318",
        "http://otel-collector.observability.svc.cluster.local:4318",
        "https://adot.example.com:4318/",
        "http://localhost:4318",
    ],
)
def test_otlp_signal_endpoints_honours_any_endpoint(endpoint):
    traces, metrics = tracing.otlp_signal_endpoints(endpoint)  # type: ignore[misc]
    base = endpoint.rstrip("/")
    assert traces == f"{base}/v1/traces"
    assert metrics == f"{base}/v1/metrics"


@pytest.mark.parametrize("endpoint", ["", "   "])
def test_otlp_signal_endpoints_empty_disables_export(endpoint):
    assert tracing.otlp_signal_endpoints(endpoint) is None


class RecordingExporter:
    created: list[str] = []

    def __init__(self, endpoint: str) -> None:
        RecordingExporter.created.append(endpoint)

    def export(self, *_args, **_kwargs):  # pragma: no cover - never flushed
        return None

    def shutdown(self, *_args, **_kwargs) -> None:
        return None

    def force_flush(self, *_args, **_kwargs) -> bool:
        return True


class NoopInstrumentor:
    calls: list[str] = []

    def instrument(self, **_kwargs) -> None:
        NoopInstrumentor.calls.append("instrument")

    @staticmethod
    def instrument_app(_app, **_kwargs) -> None:
        NoopInstrumentor.calls.append("instrument_app")


@pytest.fixture
def isolated_otel(monkeypatch):
    """init_otel against fakes: no global provider or class patching leaks."""
    RecordingExporter.created = []
    NoopInstrumentor.calls = []
    installed: dict[str, object] = {}
    monkeypatch.setattr(tracing, "_STATE", tracing._OtelState())
    monkeypatch.setattr(tracing, "OTLPSpanExporter", RecordingExporter)
    monkeypatch.setattr(tracing, "OTLPMetricExporter", RecordingExporter)
    monkeypatch.setattr(tracing, "PeriodicExportingMetricReader", lambda exp, **_: None)
    monkeypatch.setattr(tracing, "MeterProvider", lambda **kw: ("meter", kw))
    monkeypatch.setattr(
        tracing.trace, "set_tracer_provider", lambda p: installed.update(tp=p)
    )
    monkeypatch.setattr(
        tracing.metrics, "set_meter_provider", lambda p: installed.update(mp=p)
    )
    for name in (
        "FastAPIInstrumentor",
        "HTTPXClientInstrumentor",
        "PymongoInstrumentor",
    ):
        monkeypatch.setattr(tracing, name, NoopInstrumentor)
    return installed


def test_init_otel_uses_given_non_cluster_endpoint(isolated_otel):
    tracing.init_otel(
        "case-service", "0.1.0", endpoint="http://collector.example.net:4318"
    )
    assert RecordingExporter.created == [
        "http://collector.example.net:4318/v1/traces",
        "http://collector.example.net:4318/v1/metrics",
    ]
    provider = isolated_otel["tp"]
    assert provider.resource.attributes["service.name"] == "case-service"
    assert provider.resource.attributes["service.version"] == "0.1.0"
    assert NoopInstrumentor.calls == ["instrument", "instrument", "instrument"]


def test_init_otel_empty_endpoint_installs_providers_without_exporter(isolated_otel):
    tracing.init_otel("case-service", "0.1.0", endpoint="")
    assert RecordingExporter.created == []
    assert "tp" in isolated_otel
    assert isolated_otel["mp"][1]["metric_readers"] == []


def test_init_otel_reads_environment_when_endpoint_not_passed(
    isolated_otel, monkeypatch
):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://env-collector:4318")
    tracing.init_otel("svc", "1")
    assert RecordingExporter.created[0] == "http://env-collector:4318/v1/traces"


def test_init_otel_second_call_is_noop(isolated_otel):
    tracing.init_otel("svc", "1", endpoint="")
    first = isolated_otel["tp"]
    tracing.init_otel("svc", "1", endpoint="http://other:4318")
    assert isolated_otel["tp"] is first
    assert RecordingExporter.created == []


@pytest.fixture
def tracer():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider.get_tracer("test"), exporter


def test_inject_then_extract_amqp_round_trips_trace_context(tracer):
    tr, _ = tracer
    with tr.start_as_current_span("producer") as span:
        headers = tracing.inject_amqp({"x-attempt": 0})
        sent = span.get_span_context()
    assert headers["x-attempt"] == 0
    assert headers["traceparent"].startswith("00-")
    bytes_headers = {
        k: v.encode() if isinstance(v, str) else v for k, v in headers.items()
    }
    ctx = tracing.extract_amqp(bytes_headers)
    received = trace.get_current_span(ctx).get_span_context()
    assert received.trace_id == sent.trace_id
    assert received.span_id == sent.span_id
    assert received.is_remote


def test_extract_amqp_without_headers_is_empty_context():
    ctx = tracing.extract_amqp(None)
    assert not trace.get_current_span(ctx).get_span_context().is_valid


def test_inject_amqp_does_not_mutate_input():
    original = {"a": 1}
    tracing.inject_amqp(original)
    assert original == {"a": 1}


def test_case_span_sets_attributes_and_binds_log_context(tracer):
    tr, exporter = tracer
    with tracing.case_span(
        tr, "FCRM-2026-0151", "run_1", attributes={"agent": "critic"}
    ):
        assert get_context()["case_id"] == "FCRM-2026-0151"
        trace_id, span_id = tracing.current_trace_ids()
        assert trace_id is not None
        assert span_id is not None
    assert "case_id" not in get_context()
    [span] = exporter.get_finished_spans()
    assert span.name == "case.run"
    assert span.attributes["case_id"] == "FCRM-2026-0151"
    assert span.attributes["run_id"] == "run_1"
    assert span.attributes["agent"] == "critic"


def test_case_span_continues_extracted_parent(tracer):
    tr, exporter = tracer
    with tr.start_as_current_span("producer"):
        headers = tracing.inject_amqp()
    with tracing.case_span(tr, "FCRM-2026-0151", context=tracing.extract_amqp(headers)):
        pass
    producer, consumer = exporter.get_finished_spans()
    assert consumer.context.trace_id == producer.context.trace_id
    assert consumer.parent.span_id == producer.context.span_id


def test_current_trace_ids_outside_span_is_none():
    assert tracing.current_trace_ids() == (None, None)
