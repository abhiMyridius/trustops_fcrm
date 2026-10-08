"""The named metric instruments every unit records into (BUILD-PLAN §3.8,
design A §1.4).

Metrics go through the OTel metrics API and leave the process on the same
OTLP/HTTP exporter as traces and logs (:func:`fcrm_common.tracing.init_otel`);
the collector forwards them to OpenObserve, where they are searched and
charted under these exact names (ADR-68, ADR-72). There is no Prometheus
client, exporter or server anywhere.

Instrument names are the names a person types into OpenObserve. Units are
carried in the descriptions rather than the OTel ``unit`` field on purpose,
so a store that appends unit suffixes cannot rename them.

Label (attribute) keys per instrument:

=============================  =========================================
``fcrm_http_requests_total``   ``service, method, route, status``
``fcrm_http_latency_ms``       ``service, method, route, status``
``fcrm_agent_tokens_total``    ``agent, model, tier, direction`` (input is the
                               uncached part; cache_read/cache_write separate,
                               so a plain sum is the billed total)
``fcrm_agent_cost_usd_total``  ``agent, model, tier``
``fcrm_agent_latency_ms``      ``agent, tier, outcome``
``fcrm_guard_results_total``   ``guard, result`` (``pass`` or ``block``)
``fcrm_gate_wait_seconds``     ``gate_type``
``fcrm_case_stage_transitions_total`` ``to`` (the state entered; moves only)
``fcrm_case_submit_to_decide_seconds`` (no labels)
``fcrm_override_total``        ``agent``
``fcrm_outbox_lag_seconds``    ``service``
``fcrm_queue_depth``           ``queue``
``fcrm_budget_used_ratio``     ``agent`` (never ``case_id``: cardinality)
=============================  =========================================

``model`` is always the id of the model that answered (one of the few in
``tiers.yaml``), never a ``FallbackModel``'s joined name; ``case_id``,
``job_id`` and ``run_id`` are never labels on any instrument (they belong on
traces and logs). Keeping every label set small and closed is what keeps the
metric series countable.
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
    case_stage_transitions_total: Counter
    case_submit_to_decide_seconds: Histogram
    guard_results_total: Counter
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
        case_stage_transitions_total=meter.create_counter(
            "fcrm_case_stage_transitions_total",
            description=(
                "Case runs entering a state (to=<state>); a self-loop that "
                "stays in its state is not counted."
            ),
        ),
        case_submit_to_decide_seconds=meter.create_histogram(
            "fcrm_case_submit_to_decide_seconds",
            description="From case.submitted to the case closing (s).",
        ),
        guard_results_total=meter.create_counter(
            "fcrm_guard_results_total",
            description=(
                "Guard verdicts on agent jobs: result=pass (passed, masked or "
                "annotated) or block (blocked, or a retry that Guard 4 asked for)."
            ),
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
