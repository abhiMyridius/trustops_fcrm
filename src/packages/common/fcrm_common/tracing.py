"""OpenTelemetry wiring: one call per process sets up traces and metrics,
auto-instruments FastAPI, HTTPX and PyMongo, and carries W3C trace context
across RabbitMQ (BUILD-PLAN §3.8, design A §1.4, §11.5).

Exporter selection honours whatever ``OTEL_EXPORTER_OTLP_ENDPOINT`` says,
full stop. The cloned AEO ``apptracer`` swapped in a no-op exporter for
any endpoint whose host did not contain ``svc.cluster.local``, which
silently dropped every span outside its cluster; that string match is
gone. A non-empty endpoint gets OTLP/HTTP exporters at
``<endpoint>/v1/traces`` and ``<endpoint>/v1/metrics`` (the OTLP base-URL
convention); an empty one installs real SDK providers with no exporter, so
spans still exist, trace ids still reach logs and still propagate over
HTTP and AMQP, but nothing is shipped (no console exporter: it would
interleave non-JSON text with the JSON log stream on stdout).

There is no native AMQP instrumentation, so producers call
:func:`inject_amqp` on the message headers and consumers continue the
trace with :func:`extract_amqp` (design A §4.2: ``traceparent`` and
``tracestate`` headers on every message).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Optional

from fastapi import FastAPI
from opentelemetry import metrics, propagate, trace
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.pymongo import PymongoInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricReader, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Link, Span, Tracer

from fcrm_common._otel import current_trace_ids
from fcrm_common.logging import bound_context

__all__ = [
    "case_span",
    "current_trace_ids",
    "extract_amqp",
    "init_otel",
    "inject_amqp",
    "otlp_signal_endpoints",
    "shutdown_otel",
]

logger = logging.getLogger(__name__)

# OTel's own ``AttributeValue`` is a chained assignment mypy cannot use as a
# type; scalar attributes are all this module sets.
AttributeValue = str | bool | int | float

# Probes are scraped every few seconds; tracing them is noise.
EXCLUDED_URLS = "healthz,readyz"
METRIC_EXPORT_INTERVAL_MS = 15_000


@dataclass
class _OtelState:
    """What :func:`init_otel` installed, so it can be flushed on shutdown
    and so a second call is a no-op instead of a provider-override warning.
    """

    tracer_provider: Optional[TracerProvider] = None
    meter_provider: Optional[MeterProvider] = None
    exporting: bool = False
    libraries_instrumented: bool = False


_STATE = _OtelState()


def otlp_signal_endpoints(endpoint: str) -> Optional[tuple[str, str]]:
    """``(traces_url, metrics_url)`` for an OTLP/HTTP base endpoint, or
    ``None`` when the endpoint is empty (export disabled). Any non-empty
    endpoint is honoured as given: there is no host allow-list.
    """
    base = endpoint.strip().rstrip("/")
    if not base:
        return None
    return f"{base}/v1/traces", f"{base}/v1/metrics"


def init_otel(
    service_name: str,
    version: str,
    *,
    endpoint: Optional[str] = None,
    environment: Optional[str] = None,
    app: Optional[FastAPI] = None,
    instrument_libraries: bool = True,
) -> None:
    """Install the process-wide tracer and meter providers.

    Call once at startup, before the FastAPI app or any client is used.

    Args:
        service_name: ``service.name`` resource attribute.
        version: ``service.version`` resource attribute.
        endpoint: OTLP/HTTP base URL (``settings.otel_exporter_otlp_endpoint``,
            e.g. ``http://otel-collector:4318``). ``None`` reads
            ``OTEL_EXPORTER_OTLP_ENDPOINT`` from the environment at call time;
            empty disables export but keeps tracing live in-process.
        environment: ``deployment.environment`` resource attribute (``ENV``).
        app: When given, that app is instrumented directly; otherwise the
            FastAPI class is patched so apps created afterwards are traced.
        instrument_libraries: Turn on HTTPX and PyMongo auto-instrumentation.
    """
    if _STATE.tracer_provider is not None:
        logger.debug("init_otel called again; keeping the existing providers")
        if app is not None:
            _instrument_app(app)
        return
    resolved = (
        endpoint
        if endpoint is not None
        else os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    )
    attributes: dict[str, AttributeValue] = {
        "service.name": service_name,
        "service.version": version,
    }
    if environment:
        attributes["deployment.environment"] = environment
    resource = Resource.create(attributes)
    tracer_provider = TracerProvider(resource=resource)
    readers: list[MetricReader] = []
    urls = otlp_signal_endpoints(resolved)
    if urls is not None:
        traces_url, metrics_url = urls
        tracer_provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=traces_url))
        )
        readers.append(
            PeriodicExportingMetricReader(
                OTLPMetricExporter(endpoint=metrics_url),
                export_interval_millis=METRIC_EXPORT_INTERVAL_MS,
            )
        )
        logger.info(
            "otel export enabled",
            extra={"otlp_traces": traces_url, "otlp_metrics": metrics_url},
        )
    else:
        logger.info("otel export disabled: OTEL_EXPORTER_OTLP_ENDPOINT is empty")
    meter_provider = MeterProvider(resource=resource, metric_readers=readers)
    trace.set_tracer_provider(tracer_provider)
    metrics.set_meter_provider(meter_provider)
    _STATE.tracer_provider = tracer_provider
    _STATE.meter_provider = meter_provider
    _STATE.exporting = urls is not None
    if app is not None:
        _instrument_app(app)
    else:
        FastAPIInstrumentor().instrument(excluded_urls=EXCLUDED_URLS)
    if instrument_libraries and not _STATE.libraries_instrumented:
        HTTPXClientInstrumentor().instrument()
        PymongoInstrumentor().instrument()
        _STATE.libraries_instrumented = True


def _instrument_app(app: FastAPI) -> None:
    """Instrument one app instance (idempotent per app)."""
    if getattr(app, "_is_instrumented_by_opentelemetry", False):
        return
    FastAPIInstrumentor.instrument_app(app, excluded_urls=EXCLUDED_URLS)


def shutdown_otel(timeout_ms: int = 10_000) -> None:
    """Flush and shut down the providers (graceful shutdown, design A
    §10.4). Safe to call when :func:`init_otel` never ran.
    """
    if _STATE.tracer_provider is not None:
        _STATE.tracer_provider.force_flush(timeout_ms)
        _STATE.tracer_provider.shutdown()
    if _STATE.meter_provider is not None:
        _STATE.meter_provider.force_flush(timeout_ms)
        _STATE.meter_provider.shutdown()


def inject_amqp(
    headers: Optional[Mapping[str, Any]] = None, context: Optional[Context] = None
) -> dict[str, Any]:
    """Return a copy of ``headers`` with ``traceparent`` (and
    ``tracestate`` when present) for the current (or given) context,
    written by the globally configured propagator (W3C TraceContext by
    default). Existing trace headers are overwritten.
    """
    carrier: dict[str, Any] = dict(headers or {})
    propagate.inject(carrier, context=context)
    return carrier


def extract_amqp(headers: Optional[Mapping[str, Any]]) -> Context:
    """The OTel context carried in AMQP message headers.

    Broker clients may hand header values back as ``bytes``; they are
    decoded before extraction. Missing or malformed headers yield an empty
    context (the consumer then starts a new trace), never an exception.
    """
    carrier: dict[str, str] = {}
    for key, value in (headers or {}).items():
        if isinstance(value, bytes):
            carrier[key] = value.decode("utf-8", errors="replace")
        elif isinstance(value, str):
            carrier[key] = value
    return propagate.extract(carrier)


@contextmanager
def case_span(
    tracer: Tracer,
    case_id: str,
    run_id: Optional[str] = None,
    name: str = "case.run",
    *,
    attributes: Optional[Mapping[str, AttributeValue]] = None,
    context: Optional[Context] = None,
    links: Optional[Sequence[Link]] = None,
) -> Iterator[Span]:
    """Start a span for work on one case and make it current.

    The span carries ``case_id`` / ``run_id`` attributes (design A §11.5,
    H2 adds ``job_id, agent, prompt_v, tier, attempt`` via ``attributes``),
    and the same ids are bound into the logging context for the duration,
    so every log line inside the block is correlated with the trace.

    Args:
        tracer: From ``trace.get_tracer(__name__)``.
        case_id: ``FCRM-YYYY-NNNN``.
        run_id: The case run (``run_...``), when known.
        name: Span name.
        attributes: Extra span attributes.
        context: Parent context, e.g. from :func:`extract_amqp`.
        links: Span links, e.g. to the run's root span after a human gate.
    """
    span_attributes: dict[str, AttributeValue] = {"case_id": case_id}
    if run_id is not None:
        span_attributes["run_id"] = run_id
    span_attributes.update(attributes or {})
    with (
        tracer.start_as_current_span(
            name, context=context, attributes=span_attributes, links=links
        ) as span,
        bound_context(case_id=case_id, run_id=run_id),
    ):
        yield span
