"""The agents' quality as the Assurance screen reads it, from ``fcrm_evals``.

``python -m fcrm_evals run --producer live --publish`` writes one ``eval_runs``
document per run and agent and a self-contained dashboard (``eval_dashboards``).
S4 reads them through its **own read-only credential** on **that** database
(``MONGODB_URI_EVALS_READER``, role ``fcrmEvalsReadOnly``): the insert-only audit
role has nothing to do with it, and nothing in ``fcrm_evals`` is ever written
here. No service reads it for a decision: it is assurance data about the agents,
never case data.

What the rows show:

* ``metric`` and ``pct``: the agent's primary metric in the week's latest run.
  An agent whose primary metric was not measured (a gate that failed for want of
  rows) is left out rather than shown as 0%.
* ``regressions``: agents whose gate against a baseline failed.
* ``drift``: the mean change of the primary metric against the previous week,
  in percentage points.

Model ids never appear: the documents hold labels only and the dashboard is
served as published (it passed the publisher's redactor), under a CSP that lets
it load nothing and run nothing.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any, Final, Optional, Protocol

from fcrm_contracts.api.audit_oversight import AgentQualityOut, AgentQualityRowOut
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.asynchronous.database import AsyncDatabase

logger = logging.getLogger(__name__)

RUNS: Final = "eval_runs"
DASHBOARDS: Final = "eval_dashboards"
LATEST_DASHBOARD_ID: Final = "latest"
#: The most runs of one week read (nine agents, a few runs a day).
MAX_RUNS_READ: Final = 500

#: What the dashboard is allowed to do: nothing but style itself. It is
#: self-contained HTML, so it needs no network, no script, no frame and no form.
DASHBOARD_CSP: Final = (
    "default-src 'none'; style-src 'unsafe-inline'; img-src data:; "
    "font-src data:; base-uri 'none'; form-action 'none'; frame-ancestors 'self'"
)


def candidate_of(document: Mapping[str, Any]) -> Optional[float]:
    """The primary metric's score (0 to 1) in an ``eval_runs`` document, or
    ``None`` when it was not measured.
    """
    gate = document.get("gate") or {}
    value = gate.get("candidate")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    for metric in document.get("metrics") or []:
        if metric.get("primary") and isinstance(metric.get("score"), (int, float)):
            return float(metric["score"])
    return None


def quality_from_runs(
    week: str,
    documents: Sequence[Mapping[str, Any]],
    previous: Mapping[str, float],
    previous_week: Optional[str],
) -> AgentQualityOut:
    """The Assurance screen's numbers from one week's latest run per agent.

    Args:
        week: The ISO week (``2026-W41``) the documents belong to.
        documents: One ``eval_runs`` document per agent (the week's latest).
        previous: Each agent's primary metric in the previous week.
        previous_week: That week, or ``None`` when there is none.
    """
    rows: list[AgentQualityRowOut] = []
    deltas: list[float] = []
    for document in sorted(documents, key=lambda d: str(d.get("agent"))):
        score = candidate_of(document)
        if score is None:
            continue
        agent = str(document["agent"])
        rows.append(
            AgentQualityRowOut(
                agent=agent,
                metric=str(document.get("primary_metric") or ""),
                n=int(document.get("n") or 0),
                pct=round(score * 100, 1),
            )
        )
        if agent in previous:
            deltas.append((score - previous[agent]) * 100)
    regressions = sum(
        1
        for d in documents
        if (d.get("gate") or {}).get("status") == "FAIL"
        and (d.get("gate") or {}).get("baseline") is not None
    )
    if deltas and previous_week:
        drift = f"{sum(deltas) / len(deltas):+.1f} pts vs {previous_week}"
    else:
        drift = "no earlier week to compare"
    return AgentQualityOut(
        week=week,
        golden_cases=sum(r.n for r in rows),
        regressions=regressions,
        drift=drift,
        rows=rows,
    )


class EvalsReader(Protocol):
    """What the quality routes read; see the module docstring."""

    async def latest_week(self) -> Optional[str]:
        """The week of the newest run, or ``None`` when no run was published."""
        ...

    async def week_runs(self, week: str) -> list[Mapping[str, Any]]:
        """The week's latest ``eval_runs`` document per agent (no ``cases``)."""
        ...

    async def previous_week(self, week: str) -> Optional[str]:
        """The newest week before ``week`` that has a run."""
        ...

    async def dashboard(self) -> Optional[str]:
        """The latest dashboard's HTML, or ``None``."""
        ...


def _latest_per_agent(
    documents: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    newest: dict[str, Mapping[str, Any]] = {}
    for document in documents:
        agent = str(document.get("agent"))
        current = newest.get(agent)
        if current is None or document["ts"] > current["ts"]:
            newest[agent] = document
    return [newest[agent] for agent in sorted(newest)]


class InMemoryEvalsReader:
    """The unit tests' reader over ``eval_runs`` documents and one dashboard."""

    def __init__(
        self,
        runs: Sequence[Mapping[str, Any]] = (),
        html: Optional[str] = None,
    ) -> None:
        self.runs = list(runs)
        self.html = html

    async def latest_week(self) -> Optional[str]:
        if not self.runs:
            return None
        return str(max(self.runs, key=lambda d: d["ts"])["week"])

    async def week_runs(self, week: str) -> list[Mapping[str, Any]]:
        return _latest_per_agent([d for d in self.runs if d["week"] == week])

    async def previous_week(self, week: str) -> Optional[str]:
        earlier = [d for d in self.runs if str(d["week"]) < week]
        if not earlier:
            return None
        return str(max(earlier, key=lambda d: d["ts"])["week"])

    async def dashboard(self) -> Optional[str]:
        return self.html


class MongoEvalsReader:
    """The real reader over ``fcrm_evals`` (``find`` only).

    Args:
        db: The ``fcrm_evals`` database, through the read-only client.
    """

    def __init__(self, db: AsyncDatabase[Any]) -> None:
        self._runs: AsyncCollection[Any] = db[RUNS]
        self._dashboards: AsyncCollection[Any] = db[DASHBOARDS]

    async def latest_week(self) -> Optional[str]:
        doc = await self._runs.find_one({}, projection={"week": 1}, sort=[("ts", -1)])
        return str(doc["week"]) if doc is not None else None

    async def week_runs(self, week: str) -> list[Mapping[str, Any]]:
        documents = [
            doc
            async for doc in self._runs.find(
                {"week": week},
                projection={"cases": 0},
                sort=[("ts", -1)],
                limit=MAX_RUNS_READ,
            )
        ]
        return _latest_per_agent(documents)

    async def previous_week(self, week: str) -> Optional[str]:
        doc = await self._runs.find_one(
            {"week": {"$lt": week}}, projection={"week": 1}, sort=[("week", -1)]
        )
        return str(doc["week"]) if doc is not None else None

    async def dashboard(self) -> Optional[str]:
        doc = await self._dashboards.find_one({"_id": LATEST_DASHBOARD_ID})
        html = doc.get("html") if doc is not None else None
        return html if isinstance(html, str) else None


async def agent_quality(
    reader: EvalsReader, week: Optional[str]
) -> Optional[AgentQualityOut]:
    """The quality numbers of ``week`` (the newest week when ``None``), or
    ``None`` when no run was published for it (the web's empty state).
    """
    chosen = week or await reader.latest_week()
    if chosen is None:
        return None
    documents = await reader.week_runs(chosen)
    if not documents:
        return None
    before = await reader.previous_week(chosen)
    previous: dict[str, float] = {}
    if before is not None:
        for document in await reader.week_runs(before):
            score = candidate_of(document)
            if score is not None:
                previous[str(document["agent"])] = score
    return quality_from_runs(chosen, documents, previous, before)
