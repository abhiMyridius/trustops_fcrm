"""The live commands of ``python -m fcrm_evals``: ``run --producer live`` and
``gate``.

``run --producer live`` runs the nine agents (or ``--agent``) on Bedrock over
a sampled slice of each dataset, scores them, and writes::

    evals/results/<date>/<agent>.live.json  <agent>.outputs.jsonl
    evals/results/<date>/gate.json  index.json  report.md  dashboard.html
    evals/results/trend.jsonl  baseline.json (with --baseline)  latest -> <date>

and, with ``--publish``, loads the run into Mongo (``fcrm_evals.eval_runs`` and
``eval_dashboards``). It exits 0 when the run completed, whatever the verdicts:
measuring honestly is its job; ``gate`` is what fails a build.

``gate --agent A`` reads the newest run's result for ``A`` and the baseline
``baseline.json`` points at, applies the promotion rule (design B §6.4) and
exits 1 on FAIL.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Optional

from fcrm_agents.models import load_tiers_config
from fcrm_contracts.enums import AgentName

from fcrm_evals.datasets import load
from fcrm_evals.instructions import DEFAULT_AI_ROOT
from fcrm_evals.live import LiveProducer
from fcrm_evals.live_run import (
    LiveRunConfig,
    LiveRunOutcome,
    bedrock_judge_kit,
    fake_judge_kit,
    run_live,
    score_agent,
)
from fcrm_evals.outputs import AgentOutput, dump_outputs, load_outputs
from fcrm_evals.pipeline import tolerance_for
from fcrm_evals.publish import DEFAULT_DB, publish_to_uri
from fcrm_evals.report import render
from fcrm_evals.report_html import render_dashboard
from fcrm_evals.results import (
    BASELINE_FILE,
    GATE_FILE,
    INDEX_FILE,
    LATEST_LINK,
    OUTPUTS_SUFFIX,
    RESULT_SUFFIX,
    TREND_FILE,
    TREND_POINTS,
    RunIndex,
    append_trend,
    build_index,
    gate_all,
    git_sha,
    link_latest,
    load_baseline,
    load_result,
    read_trend,
    trend_point,
    write_baseline_pointer,
)
from fcrm_evals.runner import EvalResult, GateResult, GateStatus, gate_detail
from fcrm_evals.usage import PriceTable

logger = logging.getLogger(__name__)

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[5]
DEFAULT_RESULTS_ROOT: Final[Path] = REPO_ROOT / "evals" / "results"
MONGO_URI_VARIABLE: Final[str] = "MONGODB_URI"


def _note(index: RunIndex) -> str:
    sample = (
        f"{index.sample_n} row(s) per agent, seed {index.seed}"
        if index.sample_n is not None
        else "every row of every dataset"
    )
    judge = (
        "a **fake judge** (offline stand-in: `narrative_faithfulness` is a "
        "heuristic and the DeepEval metrics did not run)"
        if index.judge == "fake"
        else f"the Bedrock judge ({', '.join(index.judge_models) or 'no call made'}), "
        f"{index.judge_votes} vote(s) per narrative item"
    )
    models = ", ".join(index.models) or "none answered"
    prompt = (
        "the system prompts as the H2 worker sends them (unrendered template bodies)"
        if index.instructions == "raw"
        else "fully rendered system prompts"
    )
    lifted = (
        f" DIAGNOSTIC: the per-attempt token budgets were lifted x"
        f"{index.token_budget_factor:g}; this is not the system as shipped."
        if index.token_budget_factor != 1.0
        else ""
    )
    return (
        f"Live run `{index.run_id}` at `{index.git_sha}`: {sample}; agents answered "
        f"on Bedrock via {models} with {prompt}; graded with {judge}. "
        f"{index.gates_pass}/"
        f"{index.gates_total} gates PASS. Estimated cost "
        f"${index.cost_usd_total:.2f} ({index.tokens_total:,} tokens) at the list "
        f"prices in `ai/models/tiers.yaml` (`{index.price_status}`). A gate is the "
        "agent's primary metric against its threshold, on the sampled rows only; "
        "the dashboard's per-metric table shows the rest." + lifted
    )


def write_run(
    *,
    out: Path,
    root: Path,
    outcome: LiveRunOutcome,
    gates: Mapping[AgentName, GateResult],
    index: RunIndex,
    make_baseline: bool,
) -> str:
    """Write every file of a run (module docstring) and return the dashboard.

    The trend line is appended before the dashboard renders, so every
    sparkline ends on this run.
    """
    out.mkdir(parents=True, exist_ok=True)
    prices = PriceTable.from_tiers(outcome.tiers)

    def put(path: Path, text: str) -> None:
        """Write ``text`` with any model id in it replaced by the label."""
        path.write_text(prices.redact(text), encoding="utf-8")

    for result in outcome.results:
        stem = out / f"{result.agent.value}"
        put(
            stem.with_suffix(f".{RESULT_SUFFIX}"),
            result.model_dump_json(indent=1) + "\n",
        )
        recorded = stem.with_suffix(f".{OUTPUTS_SUFFIX}")
        dump_outputs(outcome.outputs[result.agent], recorded)
        put(recorded, recorded.read_text(encoding="utf-8"))
    verdicts = {
        "run_id": index.run_id,
        "gates_pass": index.gates_pass,
        "gates_total": index.gates_total,
        "gates": {a.value: g.model_dump(mode="json") for a, g in gates.items()},
    }
    put(out / GATE_FILE, json.dumps(verdicts, indent=1, sort_keys=True) + "\n")
    put(out / INDEX_FILE, index.model_dump_json(indent=1) + "\n")
    put(
        out / "report.md",
        render(
            outcome.results,
            gates={a.value: g for a, g in gates.items()},
            title="TrustOps eval report: live run",
            note=_note(index),
        ),
    )
    append_trend(root / TREND_FILE, trend_point(index))
    html = prices.redact(
        render_dashboard(
            index, outcome.results, read_trend(root / TREND_FILE, last=TREND_POINTS)
        )
    )
    put(out / "dashboard.html", html)
    if out.resolve().is_relative_to(root.resolve()):
        link_latest(root, out)
        if make_baseline:
            write_baseline_pointer(root, index, out)
    return html


def _mongo_uri(args: argparse.Namespace) -> Optional[str]:
    import os

    return args.mongo_uri or os.environ.get(MONGO_URI_VARIABLE)


async def run_live_command(args: argparse.Namespace) -> int:
    """``run --producer live`` (module docstring)."""
    root: Path = args.results_root
    agents = (AgentName(args.agent),) if args.agent else tuple(AgentName)
    stamp = args.timestamp or datetime.now(UTC)
    out: Path = args.out or root / stamp.strftime("%Y-%m-%d")
    config = LiveRunConfig(
        agents=agents,
        sample=None if args.sample == 0 else args.sample,
        seed=args.seed,
        row_ids=tuple(filter(None, (args.row_ids or "").split(","))),
        concurrency=args.concurrency,
        row_timeout_s=args.row_timeout,
        judge=args.judge,
        judge_votes=args.judge_votes,
        instructions=args.instructions,
        token_budget_factor=args.token_budget_factor,
        spool=out,
    )
    baseline = load_baseline(root)
    outcome = await run_live(config, timestamp=stamp)
    gates = gate_all(outcome.results, baseline)
    sha = git_sha(REPO_ROOT)
    baseline_pointer = root / BASELINE_FILE
    baseline_id = (
        json.loads(baseline_pointer.read_text(encoding="utf-8")).get("run_id")
        if baseline_pointer.is_file()
        else None
    )
    index = build_index(
        results=outcome.results,
        gates=gates,
        tiers=outcome.tiers,
        sha=sha,
        timestamp=outcome.timestamp,
        producer="live",
        judge=config.judge,
        judge_votes=config.judge_votes,
        sample_n=config.sample,
        seed=config.seed,
        concurrency=config.concurrency,
        instructions=config.instructions,
        token_budget_factor=config.token_budget_factor,
        baseline_run_id=baseline_id,
    )
    html = write_run(
        out=out,
        root=root,
        outcome=outcome,
        gates=gates,
        index=index,
        make_baseline=args.baseline,
    )
    logger.info(
        "run %s: %d/%d gates PASS, ~$%.2f, %d tokens, wrote %s",
        index.run_id,
        index.gates_pass,
        index.gates_total,
        index.cost_usd_total,
        index.tokens_total,
        out,
    )
    if args.publish:
        uri = _mongo_uri(args)
        if not uri:
            logger.error("--publish needs --mongo-uri or %s", MONGO_URI_VARIABLE)
            return 2
        published = await publish_to_uri(
            uri, index, outcome.results, gates, html, db_name=args.mongo_db
        )
        logger.info(
            "published %d eval_runs and %d dashboards for %s",
            published.runs_written,
            published.dashboards_written,
            published.run_id,
        )
    return 0


@dataclass(frozen=True)
class SavedRun:
    """A finished run read back from its folder."""

    folder: Path
    meta: RunIndex
    results: list[EvalResult]
    outputs: dict[AgentName, list[AgentOutput]]


def _load_saved(args: argparse.Namespace) -> Optional[SavedRun]:
    """The run in ``--results`` (default: ``latest``), or ``None`` if none."""
    root: Path = args.results_root
    folder = (args.results or root / LATEST_LINK).resolve()
    index_path = folder / INDEX_FILE
    if not index_path.is_file():
        logger.error("no run in %s", folder)
        return None
    results = [
        load_result(folder / f"{agent.value}.{RESULT_SUFFIX}")
        for agent in AgentName
        if (folder / f"{agent.value}.{RESULT_SUFFIX}").is_file()
    ]
    return SavedRun(
        folder=folder,
        meta=RunIndex.model_validate_json(index_path.read_text(encoding="utf-8")),
        results=results,
        outputs={
            r.agent: load_outputs(r.agent, folder / f"{r.agent.value}.{OUTPUTS_SUFFIX}")
            for r in results
        },
    )


def _rebuild(
    args: argparse.Namespace,
    saved: SavedRun,
    results: list[EvalResult],
    *,
    judge: str,
    judge_votes: int,
) -> RunIndex:
    """Verdicts, index and every file of ``saved``'s folder, from ``results``."""
    root: Path = args.results_root
    pointer = root / BASELINE_FILE
    own_baseline = (
        pointer.is_file()
        and json.loads(pointer.read_text(encoding="utf-8")).get("run_id")
        == saved.meta.run_id
    )
    gates = gate_all(results, {} if own_baseline else load_baseline(root))
    tiers = load_tiers_config(DEFAULT_AI_ROOT)
    meta = saved.meta
    index = build_index(
        results=results,
        gates=gates,
        tiers=tiers,
        sha=meta.git_sha,
        timestamp=meta.timestamp,
        producer=meta.producer,
        judge=judge,
        judge_votes=judge_votes,
        sample_n=meta.sample_n,
        seed=meta.seed,
        concurrency=meta.concurrency,
        instructions=meta.instructions,
        token_budget_factor=meta.token_budget_factor,
        baseline_run_id=meta.baseline_run_id,
    )
    outcome = LiveRunOutcome(
        config=LiveRunConfig(),
        timestamp=meta.timestamp,
        results=results,
        outputs=saved.outputs,
        tiers=tiers,
    )
    write_run(
        out=saved.folder,
        root=root,
        outcome=outcome,
        gates=gates,
        index=index,
        make_baseline=False,
    )
    return index


def rerender_command(args: argparse.Namespace) -> int:
    """``rerender``: recompute a finished run's verdicts, index, report and
    dashboard from the files it wrote, with no model call (after a change to the
    gate, the dashboard or the price table, or to rebuild a lost file).

    Returns 0, or 2 when the folder holds no run.
    """
    saved = _load_saved(args)
    if saved is None:
        return 2
    index = _rebuild(
        args,
        saved,
        saved.results,
        judge=saved.meta.judge,
        judge_votes=saved.meta.judge_votes,
    )
    logger.info(
        "re-rendered %s: %d/%d gates PASS",
        index.run_id,
        index.gates_pass,
        index.gates_total,
    )
    return 0


async def rejudge_command(args: argparse.Namespace) -> int:
    """``rejudge``: score a finished run's saved outputs again, with the judge
    named by ``--judge``, and rebuild its files. No agent is called: the rows,
    the outputs and the usage are the saved ones; only the judge's calls (and
    their cost) are new. Use it after a change to a judge or its context.

    Returns 0, or 2 when the folder holds no run.
    """
    saved = _load_saved(args)
    if saved is None:
        return 2
    config = LiveRunConfig(
        judge=args.judge,
        judge_votes=args.judge_votes,
        concurrency=args.concurrency,
        instructions="rendered" if saved.meta.instructions == "rendered" else "raw",
    )
    chosen = bedrock_judge_kit if args.judge == "bedrock" else fake_judge_kit
    gate = asyncio.Semaphore(max(1, config.concurrency))
    results: list[EvalResult] = []
    for result in saved.results:
        if result.sampled is None:
            results.append(result)
            continue
        producer = LiveProducer.for_agent(
            result.agent, ai_root=config.ai_root, instructions=config.instructions
        )
        wanted = set(result.sampled.row_ids)
        results.append(
            await score_agent(
                result.agent,
                config,
                [r for r in load(result.agent) if r.row_id in wanted],
                result.sampled,
                saved.outputs[result.agent],
                result.usage,
                packs=producer.packs,
                prices=producer.prices,
                judge_factory=chosen,
                semaphore=gate,
                timestamp=result.timestamp,
            )
        )
    index = _rebuild(
        args, saved, results, judge=args.judge, judge_votes=args.judge_votes
    )
    logger.info(
        "re-judged %s: %d/%d gates PASS, judge ~$%.2f",
        index.run_id,
        index.gates_pass,
        index.gates_total,
        index.judge_usage.cost_usd,
    )
    return 0


def gate_command(args: argparse.Namespace) -> int:
    """``gate --agent A``: the promotion rule for one agent's newest result.

    Returns 0 on PASS, 1 on FAIL, 2 when there is no result to gate.
    """
    root: Path = args.results_root
    folder = (args.results or root / LATEST_LINK).resolve()
    path = folder / f"{args.agent}.{RESULT_SUFFIX}"
    if not path.is_file():
        logger.error("no result for %s at %s; run `make evals` first", args.agent, path)
        return 2
    candidate = load_result(path)
    baselines = load_baseline(root)
    baseline = baselines.get(AgentName(args.agent))
    if baseline is not None and baseline.timestamp == candidate.timestamp:
        baseline = None  # the baseline run itself: only the threshold applies
    verdict = gate_detail(
        candidate,
        baseline,
        candidate.primary_metric or "",
        tolerance_for(candidate),
    )
    logger.info(
        "%s %s: %s %s (threshold %s, baseline %s)%s",
        args.agent,
        verdict.status.value,
        verdict.primary,
        verdict.candidate,
        verdict.threshold,
        verdict.baseline,
        "".join(f"\n  - {r}" for r in verdict.reasons),
    )
    return 0 if verdict.status is GateStatus.PASS else 1


def add_live_arguments(run: argparse.ArgumentParser) -> None:
    """The ``run --producer live`` options."""
    run.add_argument("--sample", type=int, default=5, help="rows per agent; 0 = all")
    run.add_argument("--seed", type=int, default=7)
    run.add_argument("--row-ids", default=None, help="comma-separated row ids")
    run.add_argument("--concurrency", type=int, default=3)
    run.add_argument("--row-timeout", type=float, default=420.0)
    run.add_argument("--judge", choices=["bedrock", "fake"], default="bedrock")
    run.add_argument("--judge-votes", type=int, default=3)
    run.add_argument(
        "--token-budget-factor",
        type=float,
        default=1.0,
        help="DIAGNOSTIC: scale the per-attempt token limits; 1.0 is the shipped "
        "budget and the only value for a baseline",
    )
    run.add_argument(
        "--instructions",
        choices=["raw", "rendered"],
        default="raw",
        help="system prompt: raw = as the H2 worker sends it; "
        "rendered = fully rendered",
    )
    run.add_argument(
        "--baseline", action="store_true", help="make this run the baseline"
    )
    run.add_argument("--publish", action="store_true", help="load into Mongo")
    run.add_argument("--mongo-uri", default=None)
    run.add_argument("--mongo-db", default=DEFAULT_DB)
    run.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS_ROOT)


def add_rerender_parser(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """The ``rerender`` subcommand."""
    rerender = sub.add_parser(
        "rerender", help="rebuild a finished run's gate, report and dashboard"
    )
    rerender.add_argument("--results", type=Path, default=None, help="a run folder")
    rerender.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS_ROOT)


def add_rejudge_parser(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """The ``rejudge`` subcommand."""
    rejudge = sub.add_parser(
        "rejudge", help="grade a finished run's saved outputs again (judge calls only)"
    )
    rejudge.add_argument("--results", type=Path, default=None, help="a run folder")
    rejudge.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS_ROOT)
    rejudge.add_argument("--judge", choices=["bedrock", "fake"], default="bedrock")
    rejudge.add_argument("--judge-votes", type=int, default=3)
    rejudge.add_argument("--concurrency", type=int, default=3)


def add_gate_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """The ``gate`` subcommand."""
    gate = sub.add_parser("gate", help="the promotion gate for one agent")
    gate.add_argument("--agent", choices=[a.value for a in AgentName], required=True)
    gate.add_argument("--results", type=Path, default=None, help="a run folder")
    gate.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS_ROOT)


def timestamp_of(args: argparse.Namespace) -> Optional[datetime]:
    """The pinned ``--timestamp``, if any."""
    stamp: Optional[datetime] = args.timestamp
    return stamp


__all__ = [
    "DEFAULT_RESULTS_ROOT",
    "add_gate_parser",
    "add_live_arguments",
    "add_rejudge_parser",
    "add_rerender_parser",
    "rejudge_command",
    "rerender_command",
    "gate_command",
    "run_live_command",
    "write_run",
]
