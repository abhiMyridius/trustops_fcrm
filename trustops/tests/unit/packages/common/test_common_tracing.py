"""fcrm_common.tracing: exporter selection honours any endpoint (the AEO
``svc.cluster.local`` bug), AMQP propagation, case spans.
"""

from __future__ import annotations

import logging

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


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://otel-collector:4318",
        "https://adot.example.com:4318/",
        "http://localhost:4318",
    ],
)
def test_otlp_logs_endpoint_honours_any_endpoint(endpoint):
    assert tracing.otlp_logs_endpoint(endpoint) == f"{endpoint.rstrip('/')}/v1/logs"


@pytest.mark.parametrize("endpoint", ["", "   "])
def test_otlp_logs_endpoint_empty_disables_export(endpoint):
    assert tracing.otlp_logs_endpoint(endpoint) is None


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


class FakeLoggerProvider:
    """Stands in for the SDK LoggerProvider: no thread, no network."""

    instances: list[FakeLoggerProvider] = []

    def __init__(self, **_kwargs) -> None:
        self.processors: list[object] = []
        self.flushed = False
        self.stopped = False
        FakeLoggerProvider.instances.append(self)

    def add_log_record_processor(self, processor) -> None:
        self.processors.append(processor)

    def force_flush(self, *_args, **_kwargs) -> bool:
        self.flushed = True
        return True

    def shutdown(self, *_args, **_kwargs) -> None:
        self.stopped = True


def _otel_log_handlers() -> list[logging.Handler]:
    return [
        h
        for h in logging.getLogger().handlers
        if isinstance(h, tracing._OtelLogHandler)
    ]


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
    FakeLoggerProvider.instances = []
    installed: dict[str, object] = {}
    monkeypatch.setattr(tracing, "_STATE", tracing._OtelState())
    monkeypatch.setattr(tracing, "OTLPSpanExporter", RecordingExporter)
    monkeypatch.setattr(tracing, "OTLPMetricExporter", RecordingExporter)
    monkeypatch.setattr(tracing, "OTLPLogExporter", RecordingExporter)
    monkeypatch.setattr(tracing, "LoggerProvider", FakeLoggerProvider)
    monkeypatch.setattr(tracing, "BatchLogRecordProcessor", lambda exp: ("batch", exp))
    monkeypatch.setattr(
        tracing, "set_logger_provider", lambda p: installed.update(lp=p)
    )
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
    yield installed
    # init_otel puts a handler on the process-wide root logger; never leak it.
    for handler in _otel_log_handlers():
        logging.getLogger().removeHandler(handler)


def test_init_otel_uses_given_non_cluster_endpoint(isolated_otel):
    tracing.init_otel(
        "case-service", "0.1.0", endpoint="http://collector.example.net:4318"
    )
    assert RecordingExporter.created == [
        "http://collector.example.net:4318/v1/traces",
        "http://collector.example.net:4318/v1/metrics",
        "http://collector.example.net:4318/v1/logs",
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
    # Export off means no log provider and nothing added to the root logger.
    assert "lp" not in isolated_otel
    assert FakeLoggerProvider.instances == []
    assert _otel_log_handlers() == []


def test_init_otel_with_endpoint_installs_one_log_handler(isolated_otel):
    tracing.init_otel("svc", "1", endpoint="http://collector:4318")
    [provider] = FakeLoggerProvider.instances
    assert isolated_otel["lp"] is provider
    [(kind, exporter)] = provider.processors
    assert kind == "batch"
    assert isinstance(exporter, RecordingExporter)
    assert RecordingExporter.created[-1] == "http://collector:4318/v1/logs"
    [handler] = _otel_log_handlers()
    assert handler is tracing._STATE.log_handler


def test_attach_log_handler_twice_leaves_one_handler(isolated_otel):
    provider = FakeLoggerProvider()
    tracing._attach_log_handler(provider)
    tracing._attach_log_handler(provider)
    assert len(_otel_log_handlers()) == 1


def test_shutdown_otel_flushes_log_provider_and_removes_handler(isolated_otel):
    tracing.init_otel("svc", "1", endpoint="http://collector:4318")
    [provider] = FakeLoggerProvider.instances
    assert len(_otel_log_handlers()) == 1
    # The fixture fakes the meter provider as a tuple; this test is about logs.
    tracing._STATE.meter_provider = None
    tracing.shutdown_otel()
    assert provider.flushed
    assert provider.stopped
    assert _otel_log_handlers() == []
    assert tracing._STATE.log_handler is None


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
