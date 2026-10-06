"""What a live run writes next to its per-agent results: the gate verdicts,
the run index, the trend line, the baseline pointer and the ``latest`` link.

Layout of ``evals/results/``::

    <date>/<agent>.live.json        one EvalResult per agent (per-row scores,
                                    usage, the sample, the judge's label)
    <date>/<agent>.outputs.jsonl    what each agent produced, row by row
    <date>/gate.json                nine verdicts, one per agent
    <date>/index.json               the run: id, sha, models, totals, tiles
    <date>/report.md  dashboard.html
    trend.jsonl                     one line per run, newest last
    baseline.json                   pointer to the run the gate compares with
    latest -> <date>                symlink, kept pointing at the newest run

Nothing here talks to a model or a database.
"""

from __future__ import annotations

import json
import logging
import subprocess
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Final, Optional

from fcrm_agents.spec import REGISTRY
from fcrm_contracts.enums import AgentName
from pydantic import BaseModel, ConfigDict, Field

from fcrm_evals.pipeline import tolerance_for
from fcrm_evals.runner import EvalResult, GateResult, GateStatus, gate_detail
from fcrm_evals.usage import PriceTable, RunUsage

logger = logging.getLogger(__name__)

RESULT_SUFFIX: Final[str] = "live.json"
OUTPUTS_SUFFIX: Final[str] = "outputs.jsonl"
GATE_FILE: Final[str] = "gate.json"
INDEX_FILE: Final[str] = "index.json"
TREND_FILE: Final[str] = "trend.jsonl"
BASELINE_FILE: Final[str] = "baseline.json"
LATEST_LINK: Final[str] = "latest"
MIN_COMPLETION: Final[float] = 0.80
"""Share of the sampled rows an agent must have produced an output for, for its
gate to pass. A blocked or failed row has no output to measure, and a primary
metric aggregated over the one row that did complete would read as a pass for
an agent that was refused on the other four."""
TREND_POINTS: Final[int] = 8
"""Runs a tile's sparkline shows."""
GIT_TIMEOUT_S: Final[float] = 10.0


class MetricBrief(BaseModel):
    """One of an agent's other metrics, as a tile lists it."""

    model_config = ConfigDict(extra="forbid")

    name: str
    score: Optional[float]
    threshold: float
    passed: Optional[bool]


class AgentSummary(BaseModel):
    """One agent's tile: everything the dashboard shows for it."""

    model_config = ConfigDict(extra="forbid")

    agent: AgentName
    tier: str
    dataset: str
    n: int
    of: int
    primary_metric: Optional[str]
    score: Optional[float]
    threshold: Optional[float]
    status: GateStatus
    measured: bool
    metrics_pass: int
    metrics_total: int
    tokens: int
    cost_usd: float
    judge_cost_usd: float
    latency_p50_s: Optional[float]
    blocked_rows: int
    failed_rows: int
    completed: int = 0
    reasons: list[str] = Field(default_factory=list)
    others: list[MetricBrief] = Field(default_factory=list)


class RunIndex(BaseModel):
    """The run as a whole: what the dashboard header and ``index.json`` hold."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    timestamp: datetime
    git_sha: str
    producer: str
    judge: str
    judge_votes: int
    instructions: str = "rendered"
    token_budget_factor: float = 1.0
    sample_n: Optional[int]
    seed: Optional[int]
    concurrency: int
    models: list[str]
    judge_models: list[str]
    gates_pass: int
    gates_total: int
    usage: RunUsage
    judge_usage: RunUsage
    cost_usd_total: float
    tokens_total: int
    price_status: str
    price_note: str = ""
    """The footnote of every cost in this run (``PriceTable.note``); empty in a
    result file written before prices were verified."""
    baseline_run_id: Optional[str] = None
    agents: list[AgentSummary]
    files: dict[str, str] = Field(default_factory=dict)

    @property
    def price_footnote(self) -> str:
        """What a cost line says about its prices: the run's own note, or, for
        a result file written before the note existed, its status."""
        return self.price_note or f"prices {self.price_status.replace('_', ' ')}"


class TrendPoint(BaseModel):
    """One line of ``trend.jsonl``: a run's primary scores and verdicts."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    timestamp: datetime
    judge: str
    gates_pass: int
    gates_total: int
    cost_usd: float
    scores: dict[str, Optional[float]]
    statuses: dict[str, str]


def git_sha(repo: Path) -> str:
    """The short sha of ``repo``'s HEAD, or ``unknown`` outside a checkout."""
    try:
        done = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_S,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        logger.warning("git sha unavailable under %s", repo)
        return "unknown"
    return done.stdout.strip() or "unknown"


def run_id_for(timestamp: datetime, sha: str) -> str:
    """``2026-10-05T14-22Z-<sha>``: sortable, and safe in a file name."""
    return f"{timestamp.strftime('%Y-%m-%dT%H-%MZ')}-{sha}"


def load_result(path: Path) -> EvalResult:
    """One ``<agent>.live.json``."""
    return EvalResult.model_validate_json(path.read_text(encoding="utf-8"))


def load_baseline(root: Path) -> dict[AgentName, EvalResult]:
    """The results ``baseline.json`` points at (empty when there is none).

    The pointer is ``{"run_id", "dir"}``; a pointer to a directory that is
    gone is logged and treated as no baseline, never as a pass.
    """
    pointer = root / BASELINE_FILE
    if not pointer.is_file():
        return {}
    meta = json.loads(pointer.read_text(encoding="utf-8"))
    folder = root / meta["dir"]
    found: dict[AgentName, EvalResult] = {}
    for agent in AgentName:
        path = folder / f"{agent.value}.{RESULT_SUFFIX}"
        if path.is_file():
            found[agent] = load_result(path)
    if not found:
        logger.warning(
            "baseline %s points at %s, which holds no results", pointer, folder
        )
    return found


def gate_all(
    results: Sequence[EvalResult], baseline: Mapping[AgentName, EvalResult]
) -> dict[AgentName, GateResult]:
    """One verdict per agent: its primary metric measured, at its threshold,
    and no worse than the baseline's (within the judge tolerance for a
    judge-graded primary), on a run that completed at least
    :data:`MIN_COMPLETION` of its rows. With no baseline only the threshold
    applies; an offline run has no usage record and no completion rule."""
    verdicts: dict[AgentName, GateResult] = {}
    for result in results:
        verdict = gate_detail(
            result,
            baseline.get(result.agent),
            result.primary_metric or "",
            tolerance_for(result),
        )
        rate = completion(result)
        if rate is not None and rate < MIN_COMPLETION:
            done = round(rate * result.n)
            reason = (
                f"completed {done}/{result.n} rows ({rate:.0%}); a gate needs at "
                f"least {MIN_COMPLETION:.0%}, because a blocked or failed row has "
                "no output to measure"
            )
            verdict = verdict.model_copy(
                update={
                    "status": GateStatus.FAIL,
                    "reasons": [*verdict.reasons, reason],
                }
            )
        verdicts[result.agent] = verdict
    return verdicts


def completion(result: EvalResult) -> Optional[float]:
    """The share of ``result``'s rows that produced an output (``None`` when it
    has no usage record, i.e. an offline run)."""
    usage = result.usage
    if usage is None or result.n == 0:
        return None
    return (result.n - usage.blocked_rows - usage.failed_rows) / result.n


def agent_summary(result: EvalResult, gate: GateResult, tier: str) -> AgentSummary:
    """The tile of one agent."""
    usage = result.usage
    judged = result.judge_usage
    primary = result.summary(result.primary_metric or "")
    measured = [s for s in result.summaries if s.passed is not None]
    return AgentSummary(
        agent=result.agent,
        tier=tier,
        dataset=result.dataset,
        n=result.n,
        of=result.sampled.of if result.sampled else result.n,
        primary_metric=result.primary_metric,
        score=primary.score if primary else None,
        threshold=primary.threshold if primary else None,
        status=gate.status,
        measured=bool(primary and primary.score is not None),
        metrics_pass=sum(1 for s in measured if s.passed),
        metrics_total=len(measured),
        tokens=(usage.total_tokens if usage else 0)
        + (judged.total_tokens if judged else 0),
        cost_usd=usage.cost_usd if usage else 0.0,
        judge_cost_usd=judged.cost_usd if judged else 0.0,
        latency_p50_s=usage.latency_p50_s if usage else None,
        blocked_rows=usage.blocked_rows if usage else 0,
        failed_rows=usage.failed_rows if usage else 0,
        completed=result.n - (usage.blocked_rows + usage.failed_rows if usage else 0),
        reasons=list(gate.reasons),
        others=[
            MetricBrief(
                name=s.name, score=s.score, threshold=s.threshold, passed=s.passed
            )
            for s in result.summaries
            if not s.primary
        ],
    )


def trend_point(index: RunIndex) -> TrendPoint:
    """The ``trend.jsonl`` line for ``index``."""
    return TrendPoint(
        run_id=index.run_id,
        timestamp=index.timestamp,
        judge=index.judge,
        gates_pass=index.gates_pass,
        gates_total=index.gates_total,
        cost_usd=index.cost_usd_total,
        scores={a.agent.value: a.score for a in index.agents},
        statuses={a.agent.value: a.status.value for a in index.agents},
    )


def read_trend(path: Path, *, last: Optional[int] = None) -> list[TrendPoint]:
    """The runs in ``trend.jsonl``, oldest first (the newest ``last``)."""
    if not path.is_file():
        return []
    with path.open(encoding="utf-8") as handle:
        points = [
            TrendPoint.model_validate_json(line) for line in handle if line.strip()
        ]
    return points[-last:] if last else points


def append_trend(path: Path, point: TrendPoint) -> None:
    """Append ``point``, replacing an earlier line of the same run so a re-run
    on the same id never double-counts."""
    kept = [p for p in read_trend(path) if p.run_id != point.run_id]
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [p.model_dump_json() for p in [*kept, point]]
    path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")


def link_latest(root: Path, folder: Path) -> None:
    """Point ``root/latest`` at ``folder`` (relative, so the tree moves)."""
    link = root / LATEST_LINK
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(folder.resolve().relative_to(root.resolve()))


def write_baseline_pointer(root: Path, index: RunIndex, folder: Path) -> None:
    """Make this run the one later gates compare with."""
    pointer = {
        "run_id": index.run_id,
        "dir": folder.resolve().relative_to(root.resolve()).as_posix(),
    }
    (root / BASELINE_FILE).write_text(
        json.dumps(pointer, indent=1) + "\n", encoding="utf-8"
    )


def build_index(
    *,
    results: Sequence[EvalResult],
    gates: Mapping[AgentName, GateResult],
    tiers: Mapping[str, Any],
    sha: str,
    timestamp: datetime,
    producer: str,
    judge: str,
    judge_votes: int,
    sample_n: Optional[int],
    seed: Optional[int],
    concurrency: int,
    instructions: str = "rendered",
    token_budget_factor: float = 1.0,
    baseline_run_id: Optional[str] = None,
) -> RunIndex:
    """The :class:`RunIndex` of a finished run."""
    prices = PriceTable.from_tiers(tiers)
    agents = [
        agent_summary(r, gates[r.agent], REGISTRY[r.agent].tier.value) for r in results
    ]
    usage = RunUsage()
    judged = RunUsage()
    for result in results:
        usage = usage.plus(result.usage or RunUsage())
        judged = judged.plus(result.judge_usage or RunUsage())
    return RunIndex(
        run_id=run_id_for(timestamp, sha),
        timestamp=timestamp,
        git_sha=sha,
        producer=producer,
        judge=judge,
        judge_votes=judge_votes,
        instructions=instructions,
        token_budget_factor=token_budget_factor,
        sample_n=sample_n,
        seed=seed,
        concurrency=concurrency,
        models=usage.models,
        judge_models=judged.models,
        gates_pass=sum(1 for a in agents if a.status is GateStatus.PASS),
        gates_total=len(agents),
        usage=usage,
        judge_usage=judged,
        cost_usd_total=round(usage.cost_usd + judged.cost_usd, 6),
        tokens_total=usage.total_tokens + judged.total_tokens,
        price_status=prices.status,
        price_note=prices.note(),
        baseline_run_id=baseline_run_id,
        agents=agents,
        files={r.agent.value: f"{r.agent.value}.{RESULT_SUFFIX}" for r in results},
    )
