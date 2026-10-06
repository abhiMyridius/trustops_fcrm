#!/usr/bin/env python3
"""Tokens of the demo case's agent jobs against the 120k per-case cap (live).

Runs every agent once on the canonical demo case (FCRM-2026-0151), live on
Bedrock, with the **real** pack builders over the seed-backed tools, the
rendered system prompts and the shipped tier budgets, and sums the tokens the
model calls used. Two sums are printed: input plus output, which is what H1
adds up against ``per_case_budget_tokens``, and the same with cache reads and
writes counted (Bedrock reports them apart).

The eleven jobs are the case's own chain (intake on its two documents,
screening, research, assessment, critic, narrative in both modes) and the
three oversight agents (examiner, watchlist, policy_gap), which run on demand.
A job that is retried counts both attempts. It calls Bedrock with the AWS
credentials of the shell's environment and costs about USD 0.50.

Usage::

    uv run --project src/backend --all-packages python ops/scripts/demo_case_tokens.py
        [--only intake,research] [--lift FACTOR]

``--lift`` scales the per-attempt budgets (a diagnostic, as in
``python -m fcrm_evals run --token-budget-factor``).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from collections.abc import Mapping, Sequence
from typing import Any, Final, Optional

from fcrm_agent_worker.live_runtime import live_model_for_slot, run_agent_live
from fcrm_agents.builders import builder_for
from fcrm_agents.demo import DEMO_DOCUMENT, demo_request
from fcrm_agents.models import load_tiers_config
from fcrm_agents.runtime import StaticPermitClient
from fcrm_agents.spec import REGISTRY
from fcrm_contracts.enums import AgentName
from fcrm_contracts.versions import PinnedVersions
from fcrm_evals.instructions import DEFAULT_AI_ROOT
from fcrm_evals.live import lifted_token_budgets, metered_factory
from fcrm_evals.usage import PriceTable, UsageMeter
from fcrm_guardrails import CaseBudget, PermitGuard
from fcrm_prompts import InstructionRenderer
from fcrm_seed.loader import load_raw
from fcrm_tools import DEMO_CASE_ID, ToolRegistry, build_seed_registry, seed_pins

logger = logging.getLogger("demo_case_tokens")

SECOND_DOCUMENT: Final = "payout_partner_contract.pdf"
CASE_CAP_TOKENS: Final = 120_000
CONCURRENCY: Final = 3

Job = tuple[AgentName, Optional[str], Optional[str]]
JOBS: Final[tuple[Job, ...]] = (
    (AgentName.INTAKE, None, DEMO_DOCUMENT),
    (AgentName.INTAKE, None, SECOND_DOCUMENT),
    (AgentName.SCREENING, None, None),
    (AgentName.RESEARCH, None, None),
    (AgentName.ASSESSMENT, None, None),
    (AgentName.CRITIC, None, None),
    (AgentName.NARRATIVE, "completeness", None),
    (AgentName.NARRATIVE, "recommendation", None),
    (AgentName.EXAMINER, None, None),
    (AgentName.WATCHLIST, None, None),
    (AgentName.POLICY_GAP, None, None),
)


async def run_job(
    job: Job,
    *,
    renderer: InstructionRenderer,
    registry: ToolRegistry,
    tiers: Mapping[str, Any],
    prices: PriceTable,
    pins: PinnedVersions,
) -> dict[str, Any]:
    """Build the job's real pack, run it live and report what it used."""
    agent, mode, doc = job
    spec = REGISTRY[agent]
    request = demo_request(agent, mode)
    if agent is AgentName.INTAKE:
        request = request.model_copy(update={"doc_id": doc})
    pack = await builder_for(agent).build(request, registry)
    meter = UsageMeter(prices)
    permit = PermitGuard(
        StaticPermitClient.from_registry(),
        allow_list={spec.name.value: spec.tools},
    )
    started = time.perf_counter()
    result, _model, _usage = await run_agent_live(
        spec,
        pack,
        f"demo:{agent.value}:{mode or doc or ''}",
        registry=registry,
        permit=permit,
        budget=CaseBudget(DEMO_CASE_ID),
        instructions=renderer.render(agent, pins, mode).text,
        tiers_config=tiers,
        model_factory=metered_factory(live_model_for_slot, meter),
    )
    tokens = meter.input_tokens + meter.output_tokens
    cached = sum(c.cache_read_tokens + c.cache_write_tokens for c in meter.calls)
    return {
        "agent": agent.value,
        "mode": mode or doc or "",
        "verdict": result.verdict.value,
        "stopped_at": result.stopped_at.value if result.stopped_at else None,
        "attempts": [a.tokens_used for a in result.attempts],
        "tools": [t for a in result.attempts for t in a.tools_called],
        "tokens": tokens,
        "tokens_incl_cache": tokens + cached,
        "cost_usd": round(meter.cost_usd, 4),
        "items": len(result.accepted),
        "seconds": round(time.perf_counter() - started, 1),
    }


async def run_all(jobs: Sequence[Job], lift: float) -> list[dict[str, Any]]:
    """Run ``jobs`` with at most :data:`CONCURRENCY` model calls in flight."""
    tiers = load_tiers_config(DEFAULT_AI_ROOT)
    registry = build_seed_registry()
    pins = seed_pins(load_raw())
    renderer = InstructionRenderer(ai_root=DEFAULT_AI_ROOT)
    prices = PriceTable.from_tiers(tiers)
    gate = asyncio.Semaphore(CONCURRENCY)

    async def guarded(job: Job) -> dict[str, Any]:
        async with gate:
            return await run_job(
                job,
                renderer=renderer,
                registry=registry,
                tiers=tiers,
                prices=prices,
                pins=pins,
            )

    with lifted_token_budgets(lift):
        return list(await asyncio.gather(*(guarded(job) for job in jobs)))


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the jobs and log one JSON line each, then the totals."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", maxsplit=1)[0])
    parser.add_argument("--only", default=None, help="comma-separated agent names")
    parser.add_argument("--lift", type=float, default=1.0, help="budget factor")
    args = parser.parse_args(argv)
    only = None if args.only is None else set(args.only.split(","))
    jobs = [job for job in JOBS if only is None or job[0].value in only]
    rows = asyncio.run(run_all(jobs, args.lift))
    for row in rows:
        logger.info("%s", json.dumps(row))
    total = sum(r["tokens"] for r in rows)
    note = PriceTable.from_tiers(load_tiers_config(DEFAULT_AI_ROOT)).note()
    logger.info(
        "TOTAL %d of %d tokens (input + output, what H1 sums); %d counting cache "
        "reads and writes; estimated cost USD %.3f (%s)",
        total,
        CASE_CAP_TOKENS,
        sum(r["tokens_incl_cache"] for r in rows),
        sum(r["cost_usd"] for r in rows),
        note,
    )
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    sys.exit(main())
