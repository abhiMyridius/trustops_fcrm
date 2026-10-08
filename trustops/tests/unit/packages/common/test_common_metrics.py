"""fcrm_common.metrics: the named instruments exist and record."""

from __future__ import annotations

from fcrm_common.metrics import get_metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

EXPECTED = {
    "fcrm_http_requests_total",
    "fcrm_http_latency_ms",
    "fcrm_agent_tokens_total",
    "fcrm_agent_cost_usd_total",
    "fcrm_agent_latency_ms",
    "fcrm_gate_wait_seconds",
    "fcrm_guard_results_total",
    "fcrm_case_stage_transitions_total",
    "fcrm_case_submit_to_decide_seconds",
    "fcrm_override_total",
    "fcrm_outbox_lag_seconds",
    "fcrm_queue_depth",
    "fcrm_budget_used_ratio",
}


def collected(reader: InMemoryMetricReader) -> dict[str, list]:
    data = reader.get_metrics_data()
    out: dict[str, list] = {}
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                out[metric.name] = list(metric.data.data_points)
    return out


def test_get_metrics_records_every_named_instrument():
    reader = InMemoryMetricReader()
    m = get_metrics(MeterProvider(metric_readers=[reader]))
    labels = {"agent": "critic", "model": "gpt-4.1", "tier": "strong"}
    m.http_requests_total.add(
        1, {"service": "case", "method": "GET", "route": "/cases", "status": "200"}
    )
    m.http_latency_ms.record(12.5, {"service": "case"})
    m.agent_tokens_total.add(1500, labels)
    m.agent_cost_usd_total.add(0.012, labels)
    m.agent_latency_ms.record(3400, labels)
    m.gate_wait_seconds.record(42, {"gate_type": "review_item"})
    m.guard_results_total.add(1, {"guard": "output_shape", "result": "block"})
    m.case_stage_transitions_total.add(1, {"to": "closed"})
    m.case_submit_to_decide_seconds.record(86400.0)
    m.override_total.add(1, {"agent": "assessment"})
    m.outbox_lag_seconds.set(0.4, {"outbox": "fcrm_case.outbox"})
    m.queue_depth.set(3, {"queue": "agent-worker.run"})
    m.budget_used_ratio.set(0.35, {"agent": "critic"})
    points = collected(reader)
    assert set(points) == EXPECTED
    assert points["fcrm_agent_tokens_total"][0].value == 1500
    assert dict(points["fcrm_agent_tokens_total"][0].attributes) == labels
    assert points["fcrm_outbox_lag_seconds"][0].value == 0.4


def test_get_metrics_without_provider_is_cached():
    assert get_metrics() is get_metrics()
