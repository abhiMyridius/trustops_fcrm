# TrustOps · FCRM Risk Assessment Workbench

Genius Hacks Q3 2026 (Myridius) entry. TrustOps assesses the AML/CFT and sanctions risk of a change at a US bank (a new product, vendor or geography) before it goes live. The product owner raises the case. AI agents propose, with citations. The FCRM analyst decides item by item. A deterministic engine scores. The risk committee decides Band 3–4 cases by hidden vote.

Nothing is approved or rejected automatically. Every rating traces to a source, every decision is hash-chained, and all data is synthetic.

The canonical demo is case **FCRM-2026-0151**: consumer remittances US → Mexico through a payout partner. It runs from the PO's first upload to a committee outcome, a closed case and a verified audit chain. The figures it shows are in `docs/requirements/research/06-walkthrough-seed-extraction.md`.

## How it works

```
 PO ──► S1 case ──outbox──► RabbitMQ ──► H1 Caseflow ──► H2 agent worker ──► Amazon Bedrock
         │  ▲                              │  │  (one Pydantic AI call per job)
         │  └── SSE per case ◄─────────────┘  └──► S2 scoring · S3 policy · S4 audit · S5 decision
 analyst / committee: web app ──► gateway ──► api (X1 identity, S1–S5)
```

Eight containers: `mongo`, `rabbitmq`, `minio`, `opa`, `api`, `harness`, `gateway`, `web`. Only the gateway publishes a host port. `make up-obs` adds Jaeger, Prometheus, Grafana and an OpenTelemetry collector.

- **`api`** hosts identity and the five services as sub-apps of one FastAPI process: S1 case, S2 scoring, S3 policy, S4 audit, S5 decision.
- **`harness`** hosts four workers on one broker: H1 Caseflow (orchestrator), H2 agent worker, H3 document ingestion, H4 context (vector index) builder.
- **State changes are transactional.** Every state change writes its audit record and its event through a Mongo outbox in the same transaction.
- **Caseflow is a state machine.** Its steps run outside the transaction, so no HTTP call ever sits inside one. Steps are idempotent by step id.

## Where the rubric is answered

| Rubric line | Where to look |
|---|---|
| AI harness and agent orchestration (30%) | Nine agents, each one Pydantic AI call, in `ai/agents/`; orchestrated by H1 (`src/backend/workers/caseflow`, design in `docs/architecture/sequences/`). Seven guards in `ai/guards/` and `src/backend/packages/guardrails`; tool permits through OPA in `ai/policies/`; Bedrock tiers and fallbacks in `ai/models/tiers.yaml`. |
| AI across the six SDLC stages (20%) | `docs/governance/sdlc-evidence.md` (stage by stage: what AI did, the artefacts, the gate, and the `git log` queries that check it), `ai/guidance/` (coding standards, commit convention, worktree protocol, subagent brief template, hooks), `docs/governance/` (decision log, gates register, defect trail, failure log), commit history with `Stage:` and `Gate:` trailers. |
| Human-in-the-loop gates with rationale (15%) | `docs/governance/gates-register.md`: sixteen product gates and the SDLC gates, each with the risk it controls and why it sits there. Enforced by Guard 7: a person decides, never the service account. |
| Evals (10%) | `evals/`: nine datasets, 27 deterministic metrics, a live Bedrock run over a sample, a DeepEval judge, and one single-page dashboard (`evals/results/*/dashboard.html`). |
| Context engineering (10%) | Context packs built per agent (`src/backend/packages/agents`), a 197-clause synthetic policy corpus searched by hybrid retrieval (S3, H4), verbatim quotes with a source reference, tool results cited by reference. |
| Production readiness (5%) | Eight-container stack with health checks, retry and dead-letter queues, an insert-only audit role, OPA route authorization, a PO response fence, OpenTelemetry. `ops/runbooks/` (start, replay, restart, outage, secrets, backup, audit chain, incident, release), `ops/aws/README.md` (the AWS path, a design), `docs/governance/known-gaps.md` (what is not done). |
| Token efficiency (5%) | Per-run budgets per tier and 120k tokens per case, in `ai/models/tiers.yaml`; usage metered in the evals; `ops/reports/token-analysis.md` (what the budgets were, what was wrong, what changed, measured usage per agent and per case). |
| Deterministic logic over an LLM (5%) | Scoring (S2), routing rules R1–R6, the vote tally, release checks, completion, chain verification: all code. Agents propose inputs and never write a score. |

## What stays deterministic

- **Scores** come only from S2: inherent = L × I, residual = inherent × retention, composite weighted over the in-scope categories.
- **Golden numbers:** the demo composite is 9.8 (Band 2, boundary flag) and moves to 10.4 (Band 3) when vendor due diligence is overridden from 7.6 to 4.5. These are unit tests.
- **Policy** is quoted verbatim with a source reference, never generated.
- **The audit trail** is hash-chained and append-only.
- **The PO never sees** a score, band or rating, in any response, stream or page.

## Layout (the brief's required shape)

```
src/     backend/ (apps: api, harness · services: case, scoring, policy, audit, decision, identity · workers: caseflow, agent-worker, ingestion, context · packages) · frontend/ (React 19, Vite)
ai/      prompts, agent and tool specs, skills, model tiers, guard config, Claude Code guidance
docs/    requirements · architecture · governance
evals/   datasets, results, dashboard; code in src/backend/packages/evals
tests/   unit · integration (black-box scenarios, contract, live-Mongo, service and worker tests)
ops/     nginx, OPA, Mongo init, RabbitMQ definitions, otel/grafana, scripts, runbooks, aws (a design), reports
```

## Run it

`RUNNING.md` has the full guide. In short:

```bash
make bootstrap && make env && make keys   # tools, .env from the template, JWT keys
make check                                # lint, types, OPA, unit tests, env contract, web tests (about 6 min)
make up                                   # the 8-container stack
make seed                                 # reload the 80-case seed and stage the demo PDFs
make evals                                # live agent evals on Bedrock (about USD 3), writes evals/results/<date>/
```

Agents call live Amazon Bedrock only; there is no replay or mock mode. AWS credentials come from the environment (see `.env.example`). Never commit a `.env`.

## Read in this order

1. `BUILD-PLAN.md`: start with "Status as built" (where the build departs from the plan, and why); then architecture, folder structure, definition of done.
2. `RUNNING.md`: how to run it, drive the demo by hand, every `make` target, troubleshooting.
3. `docs/governance/decision-log.md`: every decision and why (ADR-01…), and `gates-register.md`.
4. `docs/architecture/`: C4 diagrams, event catalogue, data model, sequences (with an index to the scenes), OpenAPI export.
5. `docs/governance/defect-trail.md` and `failure-log.md`: real defects and build incidents, with how each was found and fixed; `known-gaps.md`: what is not done, with the evidence.

## Status

A snapshot taken on 6 October 2026, read from commands and committed files, not from memory. It is brought up to date at each merge and finalised at the release freeze (`ops/runbooks/release-checklist.md`). `RUNNING.md` says how to run it; `docs/governance/known-gaps.md` says what is not done.

| What | Result | Where it comes from |
|---|---|---|
| `make check` (the commit gate) | green in 5 min 55 s: ruff clean, 925 files formatted, mypy on 606 files, **7,094 unit tests passed, 4 skipped**, 120 of 120 Rego tests, 144 web tests, env contract ok (55 interpolated variables, 89 documented) | run on 6 Oct, code as of `5a02c09`; the commits after it are documentation |
| Integration tests | 538 are marked `integration` and deselected by `make check`: 464 under `tests/integration` and 74 under `tests/unit/**/*_live.py`; `make test-int` selects all 538 (the live ones skip without `FCRM_TEST_MONGO_URI`). All of them, run by hand on a throwaway Mongo replica set with no stack: **525 passed, 14 skipped** in 192 to 225 s | run on 6 Oct; skips: 7 need `FCRM_TEST_RABBITMQ_URL`, 6 are named black-box skips, 1 is a missing real-document fixture |
| Live agent evals | **8 of 9 gates pass**; the assessment agent fails (`li_within_one` 0.70 against 0.85) and is reported failing. All 41 sampled rows completed. The run cost 525,250 tokens and about USD 2.86 (agents 0.56, judge 2.30) | `evals/results/2026-10-06/gate.json`, `index.json` |
| One demo case against the 120,000-token cap | 114,626 tokens with one retry, about 91,000 clean, measured before `research.system` v3 | `evals/README.md`; `ops/reports/token-analysis.md` |
| The stack | eight containers, booted healthy from empty volumes by the integration lane, which also ran the intake leg live (both demo PDFs read, intake proposals in about 50 s); `api` takes about 85 s to become healthy. No full-case run is recorded | `docs/governance/defect-trail.md` D-41, D-54; commit `5a02c09`. The documentation lane did not start the stack |
| The demo | driven by hand through the web UI (`RUNNING.md`); the golden figures (9.8 to 10.4, 2-1, 67 records) are unit tests. No live full-case run is committed | `make demo` is not wired |
| Decisions, defects, build failures | 49 ADRs (13 accepted by the operator, 19 by the orchestrator or lead, 17 still proposed); 57 defect rows; 5 failure-log rows | `docs/governance/` |
| API | 110 paths in the committed OpenAPI export; every route the web calls is in it | `docs/architecture/openapi/api.json`; `make openapi` leaves no diff |
| Not wired (print "not yet wired") | `make demo`, `seed-verify`, `verify`, `e2e`, `resilience`, `nightly`, `contracts`, `api-gen`, `locks`, `build-usage`, `backup`, `restore` | `Makefile` |
| What is not done | the 120k case cap is reported, not enforced across jobs; the web examiner does not handle a 202; S3 Vectors is not in the image and Redis does nothing; no live end-to-end suite or resilience drills; no OCR; identity is a demo directory; the AWS path is a design | `docs/governance/known-gaps.md` |

How the build was done with AI across the six stages, with the `git log` queries that check it: `docs/governance/sdlc-evidence.md`. The AWS production path: `ops/aws/README.md`. Operational procedures: `ops/runbooks/`.
