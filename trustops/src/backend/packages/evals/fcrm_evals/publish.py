"""Publishing a run to Mongo: ``fcrm_evals.eval_runs`` and ``eval_dashboards``.

S4's ``GET audit/agent-quality`` and ``.../dashboard`` read these two
collections; the document shapes are specified in ``docs/architecture/
data-model.md``.

- ``eval_runs``: one document per run *and agent*, ``_id = "<run_id>:<agent>"``,
  indexed ``{agent: 1, ts: -1}`` so "the newest run of this agent" and "the
  runs of this week" are index scans.
- ``eval_dashboards``: the dashboard HTML, as ``_id = "latest"`` and again as
  ``_id = "run:<run_id>"``.

Publishing is idempotent per run id: every write is a ``replace_one`` upsert on
a deterministic ``_id``, so publishing a run twice leaves one document each and
publishing a newer run moves ``latest``. Every call carries the client's
timeouts (``fcrm_common.mongo``'s defaults).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from fcrm_common.mongo import mongo_client
from fcrm_contracts.enums import AgentName
from pymongo import ASCENDING, DESCENDING
from pymongo.asynchronous.database import AsyncDatabase

from fcrm_evals.results import RunIndex
from fcrm_evals.runner import EvalResult, GateResult

logger = logging.getLogger(__name__)

DEFAULT_DB: Final[str] = "fcrm_evals"
RUNS: Final[str] = "eval_runs"
DASHBOARDS: Final[str] = "eval_dashboards"
LATEST_ID: Final[str] = "latest"
FLAGS_KEPT: Final[int] = 5


@dataclass(frozen=True)
class PublishOutcome:
    """What a publish wrote."""

    run_id: str
    runs_written: int
    dashboards_written: int


def iso_week(moment: datetime) -> str:
    """``2026-W41``: the week key ``audit/agent-quality?week=`` filters on."""
    year, week, _ = moment.isocalendar()
    return f"{year}-W{week:02d}"


def run_document(
    index: RunIndex, result: EvalResult, gate: GateResult
) -> dict[str, Any]:
    """The ``eval_runs`` document of one agent in one run."""
    sampled = result.sampled
    return {
        "_id": f"{index.run_id}:{result.agent.value}",
        "run_id": index.run_id,
        "agent": result.agent.value,
        "ts": result.timestamp,
        "week": iso_week(result.timestamp),
        "git_sha": index.git_sha,
        "producer": result.producer,
        "judge": result.judge,
        "judge_votes": index.judge_votes,
        "dataset": result.dataset,
        "dataset_sha256": result.dataset_sha256,
        "n": result.n,
        "sample": sampled.model_dump(mode="json") if sampled else None,
        "primary_metric": result.primary_metric,
        "gate": {
            "status": gate.status.value,
            "candidate": gate.candidate,
            "baseline": gate.baseline,
            "threshold": gate.threshold,
            "tolerance": gate.tolerance,
            "reasons": list(gate.reasons),
        },
        "metrics": [
            {
                "name": s.name,
                "kind": s.kind,
                "score": s.score,
                "threshold": s.threshold,
                "passed": s.passed,
                "rows_scored": s.rows_scored,
                "primary": s.primary,
                "flags": s.flags[:FLAGS_KEPT],
            }
            for s in result.summaries
        ],
        "usage": result.usage.model_dump(mode="json") if result.usage else None,
        "judge_usage": (
            result.judge_usage.model_dump(mode="json") if result.judge_usage else None
        ),
        "cases": [
            {
                "row_id": c.row_id,
                "status": c.run.status.value if c.run else None,
                "tokens": c.run.total_tokens if c.run else 0,
                "cost_usd": c.run.cost_usd if c.run else 0.0,
                "latency_s": c.run.latency_s if c.run else None,
                "metrics": c.metrics,
            }
            for c in result.per_case
        ],
    }


def dashboard_documents(index: RunIndex, html: str) -> list[dict[str, Any]]:
    """The two ``eval_dashboards`` documents: ``latest`` and ``run:<id>``."""
    body = {
        "run_id": index.run_id,
        "ts": index.timestamp,
        "git_sha": index.git_sha,
        "html": html,
        "bytes": len(html.encode("utf-8")),
    }
    return [{"_id": LATEST_ID, **body}, {"_id": f"run:{index.run_id}", **body}]


async def publish_run(
    db: AsyncDatabase[Any],
    index: RunIndex,
    results: Sequence[EvalResult],
    gates: Mapping[AgentName, GateResult],
    html: str,
) -> PublishOutcome:
    """Upsert the run (see the module docstring).

    Raises:
        pymongo.errors.PyMongoError: Mongo is unreachable or refused the write
            (after the client's own timeouts); nothing is half-published,
            because each document is written whole.
    """
    runs = db[RUNS]
    await runs.create_index([("agent", ASCENDING), ("ts", DESCENDING)], name="agent_ts")
    for result in results:
        document = run_document(index, result, gates[result.agent])
        await runs.replace_one({"_id": document["_id"]}, document, upsert=True)
    dashboards = db[DASHBOARDS]
    documents = dashboard_documents(index, html)
    for document in documents:
        await dashboards.replace_one({"_id": document["_id"]}, document, upsert=True)
    logger.info(
        "published run %s: %d eval_runs, %d dashboards",
        index.run_id,
        len(results),
        len(documents),
    )
    return PublishOutcome(index.run_id, len(results), len(documents))


async def publish_to_uri(
    uri: str,
    index: RunIndex,
    results: Sequence[EvalResult],
    gates: Mapping[AgentName, GateResult],
    html: str,
    *,
    db_name: str = DEFAULT_DB,
) -> PublishOutcome:
    """:func:`publish_run` on the database ``db_name`` of ``uri``, with the
    shared client (and its server-selection and socket timeouts)."""
    async with mongo_client(uri) as client:
        return await publish_run(client[db_name], index, results, gates, html)
