"""The documents a run publishes (no Mongo: the live-database test is
``tests/integration/packages/evals/test_publish.py``)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from fcrm_contracts.enums import AgentName
from fcrm_evals.pipeline import evaluate_agent
from fcrm_evals.producers import EchoProducer
from fcrm_evals.publish import (
    LATEST_ID,
    dashboard_documents,
    iso_week,
    run_document,
)
from fcrm_evals.results import build_index, gate_all


def test_the_week_key_is_the_iso_week() -> None:
    assert iso_week(datetime(2026, 10, 5, tzinfo=UTC)) == "2026-W41"
    assert iso_week(datetime(2026, 1, 1, tzinfo=UTC)) == "2026-W01"
    assert iso_week(datetime(2027, 1, 1, tzinfo=UTC)) == "2026-W53"


@pytest.fixture
async def published(stamp: datetime, tiers_config: Any) -> Callable[[], Any]:
    result = await evaluate_agent(AgentName.SCREENING, EchoProducer(), timestamp=stamp)
    gates = gate_all([result], {})
    index = build_index(
        results=[result],
        gates=gates,
        tiers=tiers_config,
        sha="abc1234",
        timestamp=stamp,
        producer="live",
        judge="fake",
        judge_votes=3,
        sample_n=None,
        seed=None,
        concurrency=1,
    )
    return lambda: (index, result, gates[AgentName.SCREENING])


async def test_a_run_document_is_keyed_by_run_and_agent_and_carries_the_gate(
    published: Callable[[], Any],
) -> None:
    index, result, gate = published()
    doc = run_document(index, result, gate)
    assert doc["_id"] == f"{index.run_id}:screening"
    assert doc["run_id"] == index.run_id
    assert doc["agent"] == "screening"
    assert doc["ts"] == result.timestamp
    assert doc["week"] == "2026-W41"
    assert doc["gate"]["status"] == "PASS"
    assert doc["gate"]["candidate"] == 1.0
    assert doc["primary_metric"] == "fast_lane_precision"
    assert {m["name"] for m in doc["metrics"]} == {
        "fast_lane_precision",
        "fast_lane_recall",
        "citation_validity",
    }
    assert doc["n"] == len(doc["cases"]) == 100
    assert doc["judge"] is None


async def test_the_dashboard_is_stored_as_latest_and_by_run_id(
    published: Callable[[], Any],
) -> None:
    index, _result, _gate = published()
    latest, by_run = dashboard_documents(index, "<html>é</html>")
    assert latest["_id"] == LATEST_ID
    assert by_run["_id"] == f"run:{index.run_id}"
    assert latest["html"] == by_run["html"] == "<html>é</html>"
    assert latest["bytes"] == len("<html>é</html>".encode())
    assert latest["run_id"] == index.run_id
