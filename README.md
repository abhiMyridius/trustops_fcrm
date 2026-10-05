# TrustOps · FCRM Risk Assessment Workbench

Genius Hacks Q3 2026 (Myridius) entry. An AI-driven workbench that assesses the AML/CFT and sanctions risk of a change at a US bank before it goes live: the product owner raises the case, AI agents propose with citations, the FCRM analyst decides item by item, a deterministic engine scores, and the risk committee decides Band 3–4 cases by hidden vote. Every rating is traceable, every decision is hash-chained, and nothing is approved or rejected automatically.

Status: build in progress, single continuous run (BUILD-PLAN.md §5); submission 9 Oct 2026.

As of this commit: every pure, framework-free piece of business logic is written and tested — the deterministic scoring engine, the hash-chained audit trail, the Caseflow state machine, S1's field-set/completion logic, S5's pack hashing and vote tally, S3's hybrid-search rank fusion, and all Pydantic contracts (API, events, agent proposals, tools). 163+ tests, all green; ruff, ruff format and mypy --strict clean repo-wide. The HTTP/Mongo/RabbitMQ wiring, the agent runtime, the guardrails package, the cloned AEO engines, the compose infrastructure, the `ai/` prompt and agent layer, and the web app are landing next. `git log --oneline` is the most current status; every commit says what it adds and what it's still waiting on.

## Read in this order

1. `BUILD-PLAN.md`: architecture, folder structure, build streams, schedule, tests, definition of done.
2. `docs/requirements/spec.md`, `personas.md`, `rules.md`, `glossary.md`: the expanded specification.
3. `docs/governance/decision-log.md`: every decision and why (ADR-01…); `gates-register.md`: every gate and its rationale; `rules.md`: enforcement status per rule; `visibility-matrix.md`, `audit-chain.md`: the two structural guarantees; `defect-trail.md`, `failure-log.md`: product defects and build-process incidents, both real, not hypothetical.
4. `docs/architecture/c4-context.md` / `c4-container.md`: the diagrams; `event-catalogue.md`, `data-model.md`: the wire-level detail; `design/`: the three source design documents (backend and harness; AI layer and evals; web, tests, ops and SDLC harness).
5. `docs/requirements/research/`: the analysis that produced this plan, and `source/`: the approved spec set and brief.

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

See [`RUNNING.md`](RUNNING.md) for setup, the checks, starting the full stack and confirming it works, including what is not wired yet. In short:

```bash
make bootstrap && make env && make keys
make check && make test-int
docker compose -f docker-compose.yaml -f compose.obs.yaml up -d --build --scale ingestion-worker=0 --scale context-worker=0
```

`make demo`, `make seed-verify` and `make e2e` still print "pending". They land with the integration wiring.

## Walkthrough and decision log

The demo case, the 21 scenes and the figures they show are in `docs/requirements/research/06-walkthrough-seed-extraction.md`; the decision log is `docs/governance/decision-log.md`.
