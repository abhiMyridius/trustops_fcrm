# TrustOps · FCRM Risk Assessment Workbench

Genius Hacks Q3 2026 (Myridius) entry. An AI-driven workbench that assesses the AML/CFT and sanctions risk of a change at a US bank before it goes live: the product owner raises the case, AI agents propose with citations, the FCRM analyst decides item by item, a deterministic engine scores, and the risk committee decides Band 3–4 cases by hidden vote. Every rating is traceable, every decision is hash-chained, and nothing is approved or rejected automatically.

Status: planning complete (3 Oct 2026); build in progress; submission 9 Oct 2026.

## Read in this order

1. `BUILD-PLAN.md`: architecture, folder structure, build streams, schedule, tests, definition of done.
2. `docs/governance/decision-log.md`: every decision and why (ADR-01…); `docs/governance/gates-register.md`: every human gate and its rationale.
3. `docs/architecture/design/`: the three design documents (backend and harness; AI layer and evals; web, tests, ops and SDLC harness).
4. `docs/requirements/`: the approved spec set and brief (`source/`) and the analysis that produced this plan (`research/`).

## Layout (the brief's required shape)

```
src/     services (case, scoring, policy, audit, decision, identity) · workers (caseflow, agent-worker, ingestion, context) · packages · web
ai/      prompts, agent and tool specs, skills, model tiers, guard config, Claude Code guidance
docs/    requirements · architecture · governance
evals/   datasets, metrics, runner, judge, results
tests/   integration · contract · e2e (21-scene demo, black-box suite) · resilience · perf
ops/     compose, nginx, OPA, Mongo init, otel/grafana, scripts, runbooks, AWS path, token analysis
```

## Quick start

Arrives with the first build commit: `make bootstrap && make up && make seed-verify`, then `make demo` to drive case FCRM-2026-0151 end to end, and `make e2e` for the 21-scene suite. Everything runs locally with docker-compose; model calls go to Amazon Bedrock (`.env` from `.env.example`), or run fully offline with `FCRM_LLM_MODE=replay`.

## Walkthrough and decision log

The demo case, the 21 scenes and the figures they show are in `docs/requirements/research/06-walkthrough-seed-extraction.md`; the decision log is `docs/governance/decision-log.md`.
