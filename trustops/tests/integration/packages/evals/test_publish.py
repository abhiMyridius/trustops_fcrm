"""Publishing a run to a real MongoDB 8 replica set.

Skipped unless ``FCRM_TEST_MONGO_URI`` names a replica set, like every
live-Mongo test here (the ``live_db`` fixture in ``tests/conftest.py`` makes a
throw-away database and drops it)::

    docker run -d --name i5a-mongo -p 28217:28217 mongo:8.0 \\
        mongod --replSet rs0 --port 28217 --bind_ip_all
    FCRM_TEST_MONGO_URI='mongodb://127.0.0.1:28217/?replicaSet=rs0' \\
        uv run --project src/backend --all-packages pytest -m integration \\
        tests/integration/packages/evals
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fcrm_contracts.enums import AgentName
from fcrm_evals.pipeline import evaluate_agent
from fcrm_evals.producers import DegradedProducer, EchoProducer
from fcrm_evals.publish import DASHBOARDS, LATEST_ID, RUNS, publish_run
from fcrm_evals.results import RunIndex, build_index, gate_all

pytestmark = pytest.mark.integration

AGENTS = (AgentName.SCREENING, AgentName.POLICY_GAP)


async def _run(
    tiers: Any, stamp: datetime, *, degraded: bool = False
) -> tuple[RunIndex, list[Any], dict[AgentName, Any]]:
    producer = DegradedProducer() if degraded else EchoProducer()
    results = [
        await evaluate_agent(agent, producer, timestamp=stamp) for agent in AGENTS
    ]
    gates = gate_all(results, {})
    index = build_index(
        results=results,
        gates=gates,
        tiers=tiers,
        sha="abc1234",
        timestamp=stamp,
        producer="live",
        judge="fake",
        judge_votes=3,
        sample_n=None,
        seed=None,
        concurrency=1,
    )
    return index, results, gates


async def test_a_run_is_published_once_however_often_it_is_published(
    live_db: Any, tiers_config: Any, stamp: datetime
) -> None:
    index, results, gates = await _run(tiers_config, stamp)
    first = await publish_run(live_db, index, results, gates, "<html>one</html>")
    again = await publish_run(live_db, index, results, gates, "<html>one</html>")
    assert (first.runs_written, first.dashboards_written) == (2, 2)
    assert again == first
    assert await live_db[RUNS].count_documents({}) == 2
    assert await live_db[DASHBOARDS].count_documents({}) == 2
    doc = await live_db[RUNS].find_one({"_id": f"{index.run_id}:screening"})
    assert doc is not None
    assert doc["gate"]["status"] == "PASS"
    assert doc["ts"].replace(tzinfo=UTC) == stamp
    assert doc["n"] == 100


async def test_runs_are_indexed_by_agent_and_time(
    live_db: Any, tiers_config: Any, stamp: datetime
) -> None:
    index, results, gates = await _run(tiers_config, stamp)
    await publish_run(live_db, index, results, gates, "<html></html>")
    indexes = await live_db[RUNS].index_information()
    assert indexes["agent_ts"]["key"] == [("agent", 1), ("ts", -1)]


async def test_a_newer_run_moves_the_latest_dashboard_and_keeps_the_old_one(
    live_db: Any, tiers_config: Any, stamp: datetime
) -> None:
    old, old_results, old_gates = await _run(tiers_config, stamp)
    new_stamp = stamp + timedelta(hours=2)
    new, new_results, new_gates = await _run(tiers_config, new_stamp, degraded=True)
    await publish_run(live_db, old, old_results, old_gates, "<html>old</html>")
    await publish_run(live_db, new, new_results, new_gates, "<html>new</html>")
    latest = await live_db[DASHBOARDS].find_one({"_id": LATEST_ID})
    assert latest is not None
    assert (latest["run_id"], latest["html"]) == (new.run_id, "<html>new</html>")
    kept = await live_db[DASHBOARDS].find_one({"_id": f"run:{old.run_id}"})
    assert kept is not None
    assert kept["html"] == "<html>old</html>"
    newest = (
        await live_db[RUNS].find({"agent": "screening"}).sort("ts", -1).to_list(1)
    )[0]
    assert newest["run_id"] == new.run_id
    assert newest["gate"]["status"] == "FAIL"
    assert await live_db[RUNS].count_documents({}) == 4
