"""``fcrm-agent``: build a context pack, or run one agent job offline, on the
demo case with the seed-backed tools (no network, no AWS).

    fcrm-agent pack <agent> [--mode completeness|recommendation] --seed
    fcrm-agent run <agent> [--mode completeness|recommendation] --seed
        [--case FCRM-2026-0151] [--tamper] [--retry-corrected]

``run`` prints the pack (section names, token estimates, truncation), the
pre-guard results, every attempt's post-guard results and the verdict. The
fake model answers with :func:`fcrm_agents.demo.demo_output`, derived from
the pack it was built for (all nine agents). ``--tamper`` makes one item fail
Guard 4 (for research: one clause quote reworded); ``--retry-corrected``
supplies the untampered output as attempt 2 (the fallback retry). Output
goes to stdout (``sys.stdout.write``; ``print`` is forbidden by the coding
standards).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from collections.abc import Sequence
from typing import Optional, TextIO

from fcrm_contracts.enums import AgentName
from fcrm_tools import DEMO_CASE_ID, build_seed_registry

from fcrm_agents.builders import builder_for
from fcrm_agents.context_pack import ContextPack
from fcrm_agents.demo import demo_output, demo_request
from fcrm_agents.runtime import AgentRunResult, run_agent_offline
from fcrm_agents.spec import REGISTRY

logger = logging.getLogger(__name__)

RUNNABLE = tuple(a.value for a in AgentName)

LABELS = (
    "clause_id",
    "control_id",
    "field_id",
    "condition_id",
    "finding_type",
    "question_id",
    "gap_id",
    "change_type",
    "domain",
    "case_id",
)
"""The first of these an accepted item has names it in the run summary."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fcrm-agent", description=__doc__.splitlines()[0]
    )
    sub = parser.add_subparsers(dest="command", required=True)
    pack = sub.add_parser("pack", help="build and show one agent's context pack")
    pack.add_argument("agent", choices=[a.value for a in AgentName])
    pack.add_argument("--mode", choices=["completeness", "recommendation"])
    run = sub.add_parser("run", help="run one agent job offline through the guards")
    run.add_argument("agent", choices=RUNNABLE)
    run.add_argument("--mode", choices=["completeness", "recommendation"])
    run.add_argument("--tamper", action="store_true", help="make one item fail Guard 4")
    run.add_argument(
        "--retry-corrected",
        action="store_true",
        help="give the fake model a corrected output for attempt 2",
    )
    for p in (pack, run):
        p.add_argument("--case", default=DEMO_CASE_ID)
        p.add_argument("--seed", action="store_true", help="use the seed-backed tools")
    return parser


def write_pack(out: TextIO, pack: ContextPack) -> None:
    """Pack summary: sections, token estimates, truncation, sources."""
    out.write(
        f"context pack · {pack.agent.value}"
        f"{' · ' + pack.mode if pack.mode else ''} · case {pack.case_id}"
        f" · hash {pack.pack_hash[:12]}\n"
    )
    out.write(f"  tools called: {', '.join(pack.tools_called)}\n")
    for s in pack.section_meta:
        cut = f" · TRUNCATED {s.items_shown}/{s.items_total}" if s.truncated else ""
        out.write(f"  {s.name:<18} ~{s.tokens:>5} tokens (cap {s.cap_tokens}){cut}\n")
    out.write(
        f"  total ~{pack.token_estimate} of {pack.token_cap} tokens · "
        f"{len(pack.sources)} citable sources\n"
    )


def write_result(out: TextIO, result: AgentRunResult) -> None:
    """Guard results per stage and the verdict."""
    out.write("pre-guards\n")
    for g in result.pre_guards:
        out.write(f"  {g.guard.value:<13} {g.status.value:<9} {g.summary}\n")
    for attempt in result.attempts:
        out.write(
            f"attempt {attempt.attempt} ({attempt.slot})"
            f" · ~{attempt.tokens_used} tokens\n"
        )
        for g in attempt.guard_results:
            out.write(f"  {g.guard.value:<13} {g.status.value:<9} {g.summary}\n")
            for line in g.details.get("feedback", [])[:3]:
                out.write(f"    feedback: {line}\n")
    out.write(f"verdict: {result.verdict.value.upper()}")
    if result.stopped_at:
        out.write(f" · stopped at {result.stopped_at.value} · {result.reason}")
    out.write("\n")
    for item in result.accepted:
        label = next(
            (str(v) for a in LABELS if (v := getattr(item, a, None)) is not None), ""
        )
        out.write(f"  accepted {type(item).__name__} {label}\n")
    for note in result.notes_for_analyst:
        out.write(f"  note: {note}\n")


async def _pack(agent: AgentName, mode: Optional[str]) -> ContextPack:
    return await builder_for(agent).build(
        demo_request(agent, mode), build_seed_registry()
    )


async def _run(args: argparse.Namespace, out: TextIO) -> int:
    agent = AgentName(args.agent)
    registry = build_seed_registry()
    pack = await builder_for(agent).build(demo_request(agent, args.mode), registry)
    write_pack(out, pack)
    first = demo_output(agent, pack, tamper=args.tamper)
    retry = demo_output(agent, pack) if args.retry_corrected else None
    result = await run_agent_offline(
        REGISTRY[agent], pack, first, retry_output=retry, registry=registry
    )
    write_result(out, result)
    return 0 if result.verdict.value != "blocked" else 2


def main(argv: Optional[Sequence[str]] = None, out: TextIO = sys.stdout) -> int:
    """Entry point; returns 0 on a passing run, 2 on a blocked one."""
    os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    args = _parser().parse_args(argv)
    if not args.seed:
        out.write(
            "only --seed backing exists until fcrm_common's HTTP tool clients land\n"
        )
        return 1
    if args.case != DEMO_CASE_ID:
        out.write(f"the seed-backed demo inputs exist for {DEMO_CASE_ID} only\n")
        return 1
    if args.command == "pack":
        write_pack(out, asyncio.run(_pack(AgentName(args.agent), args.mode)))
        return 0
    return asyncio.run(_run(args, out))


if __name__ == "__main__":
    raise SystemExit(main())
