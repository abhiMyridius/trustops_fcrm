"""``fcrm_agent_worker.job_metrics``: each finished agent job is recorded as
tokens, cost, latency, guard verdicts and budget use (ADR-72), from the worker
only, labelled with the model that answered, and never at the job's expense.

Driven through :class:`AgentJobConsumer` with the fake model factories and an
in-memory metric reader, so nothing calls Bedrock or exports anywhere.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fcrm_agent_worker.handler import AgentJobConsumer
from fcrm_agent_worker.job_metrics import (
    OUTCOME_CONTEXT_PACK_ERROR,
    JobUsage,
    job_outcome,
    record_job_metrics,
)
from fcrm_agent_worker.live_runtime import ModelFactory
from fcrm_agents.pricing import PriceTable
from fcrm_agents.runtime import AgentRunResult, RunVerdict
from fcrm_common.events import build_envelope
from fcrm_common.metrics import FcrmMetrics, get_metrics
from fcrm_contracts.enums import AgentName, Tier
from fcrm_contracts.events.envelope import Envelope
from fcrm_contracts.events.payloads import JobPayload
from fcrm_guardrails import GuardName, GuardResult
from fcrm_guardrails.base import GuardStatus
from fcrm_guardrails.opa_client import OPAClient
from fcrm_tools import ToolRegistry
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

PRICED_TIERS: dict[str, Any] = {
    "tiers": {"strong": {"primary": "priced-model", "fallbacks": []}},
    "models": {"priced-model": {"label": "Priced Model"}},
    "prices_usd_per_mtok": {
        "rows": {
            "priced-model": {
                "input": 2.0,
                "output": 10.0,
                "cache_read": 0.2,
                "cache_write": 2.5,
                "status": "verified",
            }
        }
    },
}


@pytest.fixture
def reader() -> InMemoryMetricReader:
    return InMemoryMetricReader()


@pytest.fixture
def metrics(reader: InMemoryMetricReader) -> FcrmMetrics:
    return get_metrics(MeterProvider(metric_readers=[reader]))


def _points(reader: InMemoryMetricReader) -> dict[str, list[Any]]:
    data = reader.get_metrics_data()
    out: dict[str, list[Any]] = {}
    if data is None:
        return out
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                out[metric.name] = list(metric.data.data_points)
    return out


def _consumer(
    metrics: FcrmMetrics,
    *,
    registry: ToolRegistry,
    permit_client: OPAClient,
    audit: Any,
    publisher: Any,
    tiers_config: dict[str, Any],
    model_factory: ModelFactory,
    consumed: Any,
) -> AgentJobConsumer:
    return AgentJobConsumer(
        consumed,
        publisher,
        registry=registry,
        permit_client=permit_client,
        audit=audit,
        tiers_config=tiers_config,
        model_factory=model_factory,
        metrics=metrics,
    )


async def test_a_finished_job_records_tokens_latency_guards_and_budget(
    metrics: FcrmMetrics,
    reader: InMemoryMetricReader,
    demo_job_envelope: Envelope,
    research_outputs: list[Any],
    seed_registry: ToolRegistry,
    allow_all_opa_client: OPAClient,
    fake_tiers_config: dict[str, Any],
    recording_publisher: Any,
    recording_audit: Any,
    fake_consumed: Any,
    make_model_factory_returning: Callable[..., ModelFactory],
) -> None:
    consumer = _consumer(
        metrics,
        registry=seed_registry,
        permit_client=allow_all_opa_client,
        audit=recording_audit,
        publisher=recording_publisher,
        tiers_config=fake_tiers_config,
        model_factory=make_model_factory_returning(research_outputs),
        consumed=fake_consumed,
    )
    await consumer.handle(demo_job_envelope, None)
    points = _points(reader)

    [latency] = points["fcrm_agent_latency_ms"]
    assert dict(latency.attributes) == {
        "agent": "research",
        "tier": "strong",
        "outcome": "done",
    }
    assert latency.count == 1

    tokens = points["fcrm_agent_tokens_total"]
    assert tokens, "the fake model reports usage, so tokens are counted"
    for point in tokens:
        attrs = dict(point.attributes)
        assert attrs["agent"] == "research"
        assert attrs["tier"] == "strong"
        assert attrs["direction"] in {"input", "output", "cache_read", "cache_write"}
        assert point.value > 0
        # Never a FallbackModel's joined name, never an unbounded id.
        assert not attrs["model"].startswith("fallback:")
        assert set(attrs) == {"agent", "model", "tier", "direction"}

    guards = {
        (p.attributes["guard"], p.attributes["result"]): p.value
        for p in points["fcrm_guard_results_total"]
    }
    assert guards[("permit", "pass")] >= 1
    assert all(result in {"pass", "block"} for _guard, result in guards)

    [ratio] = points["fcrm_budget_used_ratio"]
    assert dict(ratio.attributes) == {"agent": "research"}
    assert ratio.value > 0


async def test_a_permit_denial_is_recorded_as_a_blocked_job(
    metrics: FcrmMetrics,
    reader: InMemoryMetricReader,
    demo_job_envelope: Envelope,
    research_outputs: list[Any],
    seed_registry: ToolRegistry,
    deny_all_opa_client: OPAClient,
    fake_tiers_config: dict[str, Any],
    recording_publisher: Any,
    recording_audit: Any,
    fake_consumed: Any,
    make_model_factory_returning: Callable[..., ModelFactory],
) -> None:
    consumer = _consumer(
        metrics,
        registry=seed_registry,
        permit_client=deny_all_opa_client,
        audit=recording_audit,
        publisher=recording_publisher,
        tiers_config=fake_tiers_config,
        model_factory=make_model_factory_returning(research_outputs),
        consumed=fake_consumed,
    )
    await consumer.handle(demo_job_envelope, None)
    points = _points(reader)

    [latency] = points["fcrm_agent_latency_ms"]
    assert latency.attributes["outcome"] == "permit"
    blocks = [
        p
        for p in points["fcrm_guard_results_total"]
        if p.attributes["result"] == "block"
    ]
    assert [p.attributes["guard"] for p in blocks] == ["permit"]
    # No model was called, so no tokens and no cost.
    assert "fcrm_agent_tokens_total" not in points
    assert "fcrm_agent_cost_usd_total" not in points


async def test_a_context_pack_failure_records_only_its_latency(
    metrics: FcrmMetrics,
    reader: InMemoryMetricReader,
    seed_registry: ToolRegistry,
    allow_all_opa_client: OPAClient,
    fake_tiers_config: dict[str, Any],
    recording_publisher: Any,
    recording_audit: Any,
    fake_consumed: Any,
    make_model_factory_returning: Callable[..., ModelFactory],
) -> None:
    from fcrm_seed.loader import load_raw
    from fcrm_tools import seed_pins

    payload = JobPayload(
        job_id="job-intake-1",
        case_id="FCRM-2026-0151",
        run_id="run-intake-1",
        agent=AgentName.INTAKE,
        tier=Tier.CHEAP,
        step_key="intake",
        attempt=1,
        pins=seed_pins(load_raw()),
    )
    envelope = build_envelope(
        "agent.run",
        payload.model_dump(mode="json"),
        case_id=payload.case_id,
        run_id=payload.run_id,
    )
    consumer = _consumer(
        metrics,
        registry=seed_registry,
        permit_client=allow_all_opa_client,
        audit=recording_audit,
        publisher=recording_publisher,
        tiers_config=fake_tiers_config,
        model_factory=make_model_factory_returning([]),
        consumed=fake_consumed,
    )
    await consumer.handle(envelope, None)
    points = _points(reader)

    [latency] = points["fcrm_agent_latency_ms"]
    assert dict(latency.attributes) == {
        "agent": "intake",
        "tier": "cheap",
        "outcome": OUTCOME_CONTEXT_PACK_ERROR,
    }
    assert set(points) == {"fcrm_agent_latency_ms"}


def test_cost_and_tokens_use_the_answering_models_label_and_price(
    metrics: FcrmMetrics, reader: InMemoryMetricReader
) -> None:
    usage = JobUsage()
    usage.add(
        "priced-model",
        input_tokens=1_000,
        output_tokens=200,
        cache_read_tokens=300,
        cache_write_tokens=100,
    )
    record_job_metrics(
        metrics,
        PriceTable.from_tiers(PRICED_TIERS),
        agent="research",
        tier="strong",
        elapsed_ms=1234.0,
        outcome="done",
        usage=usage,
    )
    points = _points(reader)

    tokens = {
        p.attributes["direction"]: p.value for p in points["fcrm_agent_tokens_total"]
    }
    # input is the uncached part, so a plain sum is the billed total.
    assert tokens == {
        "input": 600,
        "output": 200,
        "cache_read": 300,
        "cache_write": 100,
    }
    assert sum(tokens.values()) == 1_200
    assert {p.attributes["model"] for p in points["fcrm_agent_tokens_total"]} == {
        "Priced Model"
    }

    [cost] = points["fcrm_agent_cost_usd_total"]
    expected = (600 * 2.0 + 200 * 10.0 + 300 * 0.2 + 100 * 2.5) / 1_000_000
    assert cost.value == pytest.approx(expected)
    assert dict(cost.attributes) == {
        "agent": "research",
        "model": "Priced Model",
        "tier": "strong",
    }


def _result(verdict: RunVerdict, **extra: Any) -> AgentRunResult:
    return AgentRunResult(
        agent="research",
        case_id="FCRM-2026-0151",
        run_id="run-1",
        verdict=verdict,
        pack_hash="h",
        pack_token_estimate=0,
        **extra,
    )


def test_job_outcome_is_a_small_closed_set() -> None:
    assert job_outcome(_result(RunVerdict.PASS)) == "done"
    assert job_outcome(_result(RunVerdict.PASS_WITH_DROPS)) == "done_with_drops"
    assert (
        job_outcome(_result(RunVerdict.BLOCKED, stopped_at=GuardName.BUDGET))
        == "budget"
    )
    assert job_outcome(_result(RunVerdict.BLOCKED)) == "blocked"


def test_a_metric_that_cannot_be_recorded_never_breaks_the_job(
    reader: InMemoryMetricReader,
) -> None:
    class Broken:
        def __getattr__(self, _name: str) -> Any:
            raise AttributeError("no instruments")

    result = _result(
        RunVerdict.PASS,
        pre_guards=[
            GuardResult(
                guard=GuardName.PERMIT,
                status=GuardStatus.PASSED,
                summary="permitted",
                audit_type="guard.permit",
            )
        ],
    )
    record_job_metrics(
        Broken(),  # type: ignore[arg-type]
        PriceTable.from_tiers({}),
        agent="research",
        tier="strong",
        elapsed_ms=1.0,
        outcome="done",
        result=result,
    )
