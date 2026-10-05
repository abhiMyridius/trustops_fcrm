"""``python -m fcrm_evals <command>``.

- ``build [--check]``: regenerate (or verify) the bundled datasets.
- ``run --producer echo|degraded|recorded [--agent A] [--outputs F] --out D``:
  score agents and write ``<agent>.<producer>.json`` plus ``report.md``.
- ``run --producer live [--sample N --seed S --judge bedrock|fake ...]``: run
  the agents on Bedrock over a sampled slice, grade them, write the run folder
  and the dashboard, optionally publish (:mod:`fcrm_evals.live_cli`).
- ``gate --agent A``: the promotion gate on the newest live result.
- ``rerender [--results D]``: rebuild a finished run's verdicts, report and
  dashboard from its files, with no model call.
- ``rejudge [--results D] [--judge bedrock|fake]``: grade a finished run's saved
  outputs again (judge calls only; no agent is called).
- ``selfcheck --out D``: the offline proof (echo perfect and passing,
  degraded failing the gate); writes results, ``gate.json`` and
  ``report.md``; exit code 1 if any expectation fails.

``--timestamp`` pins the result timestamp so committed example outputs are
reproducible byte for byte.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Optional

from fcrm_contracts.enums import AgentName

from fcrm_evals.datasets import build as dataset_build
from fcrm_evals.live_cli import (
    add_gate_parser,
    add_live_arguments,
    add_rejudge_parser,
    add_rerender_parser,
    gate_command,
    rejudge_command,
    rerender_command,
    run_live_command,
)
from fcrm_evals.outputs import load_outputs
from fcrm_evals.pipeline import evaluate_agent, selfcheck
from fcrm_evals.producers import (
    DegradedProducer,
    EchoProducer,
    OutputProducer,
    RecordedProducer,
)
from fcrm_evals.report import render
from fcrm_evals.runner import EvalResult

logger = logging.getLogger(__name__)

SELFCHECK_NOTE = (
    "Self-check run: `echo` scores each dataset's own reference outputs (must "
    "be 1.000 everywhere), `degraded` scores the same outputs with deliberate "
    "mistakes. Neither is a measurement of a real agent; the seed's weekly "
    'per-agent figures (`seed.json["evals"]`) remain seeded targets until a '
    "live or recorded run replaces them."
)


def _write_results(results: Sequence[EvalResult], out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for result in results:
        path = out / f"{result.agent.value}.{result.producer}.json"
        path.write_text(result.model_dump_json(indent=1) + "\n", encoding="utf-8")


def _producer(name: str, agent: AgentName, outputs: Optional[Path]) -> OutputProducer:
    if name == "echo":
        return EchoProducer()
    if name == "degraded":
        return DegradedProducer()
    if outputs is None:
        msg = "--outputs is required with --producer recorded"
        raise SystemExit(msg)
    return RecordedProducer(load_outputs(agent, outputs))


async def _run(args: argparse.Namespace) -> int:
    agents = [AgentName(args.agent)] if args.agent else list(AgentName)
    results = [
        await evaluate_agent(
            agent,
            _producer(args.producer, agent, args.outputs),
            timestamp=args.timestamp,
        )
        for agent in agents
    ]
    _write_results(results, args.out)
    (args.out / "report.md").write_text(render(results), encoding="utf-8")
    logger.info("wrote %d result(s) and report.md to %s", len(results), args.out)
    return 0


async def _selfcheck(args: argparse.Namespace) -> int:
    check = await selfcheck(timestamp=args.timestamp)
    _write_results(check.results, args.out)
    gates = {label: g.model_dump(mode="json") for label, g in check.gates.items()}
    (args.out / "gate.json").write_text(
        json.dumps(gates, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    report = render(
        check.results,
        gates=check.gates,
        title="TrustOps eval report: offline self-check",
        note=SELFCHECK_NOTE,
    )
    (args.out / "report.md").write_text(report, encoding="utf-8")
    logger.info("self-check %s; wrote %s", "ok" if check.ok else "FAILED", args.out)
    return 0 if check.ok else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fcrm_evals")
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="regenerate or --check the datasets")
    build.add_argument("--check", action="store_true")
    for name in ("run", "selfcheck"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--out", type=Path, required=name == "selfcheck")
        cmd.add_argument("--timestamp", type=datetime.fromisoformat, default=None)
        if name == "run":
            cmd.add_argument(
                "--producer",
                choices=["echo", "degraded", "recorded", "live"],
                default="echo",
            )
            cmd.add_argument(
                "--agent", choices=[a.value for a in AgentName], default=None
            )
            cmd.add_argument("--outputs", type=Path, default=None)
            add_live_arguments(cmd)
    add_gate_parser(sub)
    add_rerender_parser(sub)
    add_rejudge_parser(sub)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point; returns a process exit code."""
    args = _parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    if args.command == "build":
        return dataset_build.main(["--check"] if args.check else [])
    if args.command == "gate":
        return gate_command(args)
    if args.command == "rerender":
        return rerender_command(args)
    if args.command == "rejudge":
        return asyncio.run(rejudge_command(args))
    if args.command == "run" and args.producer == "live":
        return asyncio.run(run_live_command(args))
    if args.command == "run":
        if args.out is None:
            msg = "--out is required unless --producer live"
            raise SystemExit(msg)
        return asyncio.run(_run(args))
    return asyncio.run(_selfcheck(args))


if __name__ == "__main__":
    sys.exit(main())
