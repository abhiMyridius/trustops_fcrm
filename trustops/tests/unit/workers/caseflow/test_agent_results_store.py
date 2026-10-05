""":class:`fcrm_caseflow.store.AgentResultStore`: every ``agent.done`` is kept
as it arrives (the proposals exist nowhere else), once per job, readable back
per run and agent in a fixed order.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from conftest import FakeCollection, FakeSession
from fcrm_caseflow.store import AgentResultStore
from fcrm_contracts.enums import AgentName
from pydantic import ValidationError


def done_payload(
    job_id: str, agent: str = "intake", **overrides: Any
) -> dict[str, Any]:
    """An ``agent.done`` body as H2 publishes it."""
    return {
        "job_id": job_id,
        "case_id": "FCRM-2026-0151",
        "run_id": "run_1",
        "agent": agent,
        "output": {
            "accepted": [{"question_id": f"q_{job_id}", "text": "?", "blocking": True}],
            "dropped": [],
            "notes_for_analyst": [],
            "guard_summary": {"claims_total": 0, "claims_with_source": 0, "dropped": 0},
            "attempts": 1,
        },
        "usage": {"input_tokens": 1200, "output_tokens": 300},
        "model": "anthropic:claude-haiku",
        "attempt": 1,
        **overrides,
    }


async def test_a_saved_result_reads_back_as_the_agent_produced_it(
    agent_results: AgentResultStore, session: FakeSession
) -> None:
    await agent_results.save(done_payload("job_a"), session=session)  # type: ignore[arg-type]

    [result] = await agent_results.list_for("run_1", AgentName.INTAKE)

    assert (result.job_id, result.run_id, result.case_id) == (
        "job_a",
        "run_1",
        "FCRM-2026-0151",
    )
    assert result.agent is AgentName.INTAKE
    assert result.usage == {"input_tokens": 1200, "output_tokens": 300}
    assert result.model == "anthropic:claude-haiku"
    assert result.accepted() == [
        {"question_id": "q_job_a", "text": "?", "blocking": True}
    ]


async def test_saving_the_same_job_twice_keeps_one_result_and_its_first_time(
    agent_results: AgentResultStore,
    agent_results_collection: FakeCollection,
    session: FakeSession,
) -> None:
    await agent_results.save(done_payload("job_a"), session=session)  # type: ignore[arg-type]
    first = (await agent_results.list_for("run_1", AgentName.INTAKE))[0]

    await agent_results.save(done_payload("job_a"), session=session)  # type: ignore[arg-type]

    assert len(agent_results_collection.docs) == 1
    [again] = await agent_results.list_for("run_1", AgentName.INTAKE)
    assert again.finished_at == first.finished_at


async def test_the_write_goes_through_the_callers_session(
    agent_results: AgentResultStore,
    agent_results_collection: FakeCollection,
    session: FakeSession,
) -> None:
    await agent_results.save(done_payload("job_a"), session=session)  # type: ignore[arg-type]
    assert agent_results_collection.sessions == [session]


async def test_results_are_listed_per_run_and_agent_oldest_first(
    agent_results: AgentResultStore,
    agent_results_collection: FakeCollection,
    session: FakeSession,
) -> None:
    for job in ("job_b", "job_a"):
        await agent_results.save(done_payload(job), session=session)  # type: ignore[arg-type]
    await agent_results.save(done_payload("job_c", agent="critic"), session=session)  # type: ignore[arg-type]
    await agent_results.save(done_payload("job_d", run_id="run_2"), session=session)  # type: ignore[arg-type]
    # job_b finished first, whatever its id sorts like.
    stored = {d["_id"]: d for d in agent_results_collection.docs}
    stored["job_b"]["finished_at"] = stored["job_a"]["finished_at"] - timedelta(
        seconds=5
    )

    intake = await agent_results.list_for("run_1", AgentName.INTAKE)
    critic = await agent_results.list_for("run_1", AgentName.CRITIC)

    assert [r.job_id for r in intake] == ["job_b", "job_a"]
    assert [r.job_id for r in critic] == ["job_c"]
    assert await agent_results.list_for("run_3", AgentName.INTAKE) == []


async def test_results_finishing_together_come_back_in_job_id_order(
    agent_results: AgentResultStore,
    agent_results_collection: FakeCollection,
    session: FakeSession,
) -> None:
    for job in ("job_b", "job_a"):
        await agent_results.save(done_payload(job), session=session)  # type: ignore[arg-type]
    for doc in agent_results_collection.docs:
        doc["finished_at"] = agent_results_collection.docs[0]["finished_at"]

    intake = await agent_results.list_for("run_1", AgentName.INTAKE)

    assert [r.job_id for r in intake] == ["job_a", "job_b"]


async def test_a_body_that_is_not_an_agent_done_is_refused(
    agent_results: AgentResultStore, session: FakeSession
) -> None:
    with pytest.raises(ValidationError):
        await agent_results.save({"job_id": "job_a"}, session=session)  # type: ignore[arg-type]


async def test_keys_a_producer_adds_are_ignored(
    agent_results: AgentResultStore, session: FakeSession
) -> None:
    await agent_results.save(done_payload("job_a", band=2), session=session)  # type: ignore[arg-type]
    assert len(await agent_results.list_for("run_1", AgentName.INTAKE)) == 1


async def test_it_asks_for_the_index_its_query_uses(
    agent_results: AgentResultStore, agent_results_collection: FakeCollection
) -> None:
    [name] = await agent_results.ensure_indexes()
    assert name == "run_agent_finished"
