"""The named metric instruments every unit records into (BUILD-PLAN §3.8,
design A §1.4).

Metrics go through the OTel metrics API and leave the process on the same
OTLP exporter as traces (:func:`fcrm_common.tracing.init_otel`); the
collector's ``prometheus`` exporter exposes them to Prometheus and Grafana.
There is no separate Prometheus client in the process.

Instrument names are exactly the dashboard names. Units are carried in the
descriptions rather than the OTel ``unit`` field on purpose: the
collector's Prometheus translation appends a unit suffix
(``fcrm_http_latency_ms`` would become ``..._ms_milliseconds``), which
would break the dashboard queries.

Label (attribute) keys per instrument:

=============================  =========================================
``fcrm_http_requests_total``   ``service, method, route, status``
``fcrm_http_latency_ms``       ``service, method, route, status``
``fcrm_agent_tokens_total``    ``agent, model, tier`` (+ ``direction``)
``fcrm_agent_cost_usd_total``  ``agent, model, tier``
``fcrm_agent_latency_ms``      ``agent, model, tier, outcome``
``fcrm_gate_wait_seconds``     ``gate_type``
``fcrm_override_total``        ``agent``
``fcrm_outbox_lag_seconds``    ``service``
``fcrm_queue_depth``           ``queue``
``fcrm_budget_used_ratio``     ``agent`` (never ``case_id``: cardinality)
=============================  =========================================
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

from opentelemetry import metrics
from opentelemetry.metrics import Counter, Histogram, Meter, MeterProvider

# The synchronous gauge is exported only under its provisional private name
# (``Meter.create_gauge`` is annotated ``-> Gauge``, which is this class).
from opentelemetry.metrics import _Gauge as Gauge

METER_NAME = "fcrm"


@dataclass(frozen=True)
class FcrmMetrics:
    """The instrument set; obtain it with :func:`get_metrics`."""

    http_requests_total: Counter
    http_latency_ms: Histogram
    agent_tokens_total: Counter
    agent_cost_usd_total: Counter
    agent_latency_ms: Histogram
    gate_wait_seconds: Histogram
    override_total: Counter
    outbox_lag_seconds: Gauge
    queue_depth: Gauge
    budget_used_ratio: Gauge


def build_metrics(meter: Meter) -> FcrmMetrics:
    """Create every named instrument on ``meter``."""
    return FcrmMetrics(
        http_requests_total=meter.create_counter(
            "fcrm_http_requests_total", description="HTTP requests served."
        ),
        http_latency_ms=meter.create_histogram(
            "fcrm_http_latency_ms", description="HTTP request latency (ms)."
        ),
        agent_tokens_total=meter.create_counter(
            "fcrm_agent_tokens_total",
            description="Model tokens consumed by agent calls (tokens).",
        ),
        agent_cost_usd_total=meter.create_counter(
            "fcrm_agent_cost_usd_total",
            description="Model cost of agent calls (USD, from ai/models/tiers.yaml).",
        ),
        agent_latency_ms=meter.create_histogram(
            "fcrm_agent_latency_ms", description="Agent job wall time (ms)."
        ),
        gate_wait_seconds=meter.create_histogram(
            "fcrm_gate_wait_seconds",
            description="Time a human gate stayed open before a decision (s).",
        ),
        override_total=meter.create_counter(
            "fcrm_override_total",
            description="Human overrides of agent proposals.",
        ),
        outbox_lag_seconds=meter.create_gauge(
            "fcrm_outbox_lag_seconds",
            description="Age of the oldest unpublished outbox row (s).",
        ),
        queue_depth=meter.create_gauge(
            "fcrm_queue_depth", description="Messages ready in a queue."
        ),
        budget_used_ratio=meter.create_gauge(
            "fcrm_budget_used_ratio",
            description="Used / cap tokens for the case run being dispatched.",
        ),
    )


@lru_cache(maxsize=1)
def _default_metrics() -> FcrmMetrics:
    """The process-wide instrument set on the global meter provider.

    Safe to call before :func:`fcrm_common.tracing.init_otel`: the OTel API
    hands out proxy instruments that bind to the real provider once it is
    installed.
    """
    return build_metrics(metrics.get_meter(METER_NAME))


def get_metrics(provider: Optional[MeterProvider] = None) -> FcrmMetrics:
    """The instrument set: the cached process-wide one, or a fresh one on
    ``provider`` (tests pass an SDK ``MeterProvider`` with an in-memory
    reader).
    """
    if provider is None:
        return _default_metrics()
    return build_metrics(provider.get_meter(METER_NAME))
