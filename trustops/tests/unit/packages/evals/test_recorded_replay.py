"""Replay of the real Bedrock answers of the 6 October run through today's
guards (ADR-59: no model is called; ADR-80).

``evals/results/2026-10-06/`` (where ``latest`` points) is the newest full
``make evals`` run: 41 rows, all nine agents, every row ``ok`` with verdict
``pass``. It ran on the 6 October chain; no run on the committed chain exists
(``evals/README.md``), so it is the newest set of real answers there is. The
other kept runs are older (``2026-10-05*``, ``2026-10-06-run1``) or one agent
over a whole dataset with a prompt that is not shipped.

Each row is produced through :class:`fcrm_evals.live.LiveProducer`, so the pack
is today's ``RowPackBuilder`` pack and the guard chain is H2's real
``run_agent_live`` (guards 1 to 5); only the model is replaced, by one that
sends the recorded answer as a single output-tool call and makes no tool call.

The oracle is the recorded run itself: every recorded row has
``items_dropped == 0`` and ``items_accepted == len(items)`` in its
``outputs.jsonl`` ``run`` record (the same record ``<agent>.live.json``
holds), so the replay must end ``ok`` with exactly the recorded item count.
These answers passed Guard 4 live; a refusal or a dropped item now is a
regression in a guard, the JSON-text mending or the pack builder, never a
reason to skip or loosen this test.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fcrm_agent_worker.live_runtime import ModelFactory
from fcrm_contracts.enums import AgentName
from fcrm_evals.datasets import load
from fcrm_evals.live import LiveProducer
from fcrm_evals.outputs import load_outputs
from fcrm_evals.rows import EvalRow
from fcrm_evals.usage import RowStatus

RUN = Path(__file__).parents[4] / "evals" / "results" / "2026-10-06"


def _recorded() -> list[tuple[AgentName, dict[str, Any]]]:
    cases = []
    for agent in AgentName:
        path = RUN / f"{agent.value}.outputs.jsonl"
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                cases.append((agent, json.loads(line)))
    return cases


RECORDED = _recorded()


def test_the_replayed_run_is_the_full_recorded_sample() -> None:
    """41 rows over the nine agents, each accepted whole on the live run."""
    assert len(RECORDED) == 41
    assert {agent for agent, _ in RECORDED} == set(AgentName)
    for agent, record in RECORDED:
        run = record["run"]
        assert run["status"] == RowStatus.OK.value, record["row_id"]
        assert run["items_dropped"] == 0, record["row_id"]
        assert run["items_accepted"] == len(record["items"]), record["row_id"]
        outputs = load_outputs(agent, RUN / f"{agent.value}.outputs.jsonl")
        assert record["row_id"] in {o.row_id for o in outputs}


@pytest.mark.parametrize(
    ("agent", "record"),
    RECORDED,
    ids=[f"{agent.value}:{record['row_id']}" for agent, record in RECORDED],
)
async def test_recorded_answer_passes_the_guards_today(
    agent: AgentName,
    record: dict[str, Any],
    repo_ai: Path,
    recorded_factory: Callable[..., ModelFactory],
) -> None:
    rows: dict[str, EvalRow] = {row.row_id: row for row in load(agent)}
    row = rows[record["row_id"]]
    answer = [item["data"] for item in record["items"]]
    producer = LiveProducer.for_agent(
        agent,
        ai_root=repo_ai,
        model_factory=recorded_factory([row], {row.row_id: answer}),
        instructions="rendered",
    )

    output = await producer.produce(row)

    assert output.status is RowStatus.OK, output.reason
    assert output.run is not None
    assert output.run.items_dropped == 0, output.run.feedback
    assert len(output.items) == record["run"]["items_accepted"]
