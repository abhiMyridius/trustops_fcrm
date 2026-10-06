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
| AI across the six SDLC stages (20%) | `ai/guidance/` (coding standards, commit convention, worktree protocol, subagent brief template, hooks), `docs/governance/` (decision log, gates register, defect trail, failure log), commit history with `Stage:` and `Gate:` trailers. |
| Human-in-the-loop gates with rationale (15%) | `docs/governance/gates-register.md`: sixteen product gates and the SDLC gates, each with the risk it controls and why it sits there. Enforced by Guard 7: a person decides, never the service account. |
| Evals (10%) | `evals/`: nine datasets, 27 deterministic metrics, a live Bedrock run over a sample, a DeepEval judge, and one single-page dashboard (`evals/results/*/dashboard.html`). |
| Context engineering (10%) | Context packs built per agent (`src/backend/packages/agents`), a 197-clause synthetic policy corpus searched by hybrid retrieval (S3, H4), verbatim quotes with a source reference, tool results cited by reference. |
| Production readiness (5%) | Eight-container stack with health checks, retry and dead-letter queues, an insert-only audit role, OPA route authorization, a PO response fence, OpenTelemetry. |
| Token efficiency (5%) | Per-run budgets per tier and 120k tokens per case, in `ai/models/tiers.yaml`; usage metered in the evals. |
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
tests/   unit · integration · contract · e2e (21-scene demo, black-box suite) · resilience
ops/     nginx, OPA, Mongo init, RabbitMQ definitions, otel/grafana, scripts
```

## Run it

`RUNNING.md` has the full guide. In short:

```bash
make bootstrap && make env && make keys   # tools, .env from the template, JWT keys
make check                                # lint, types, OPA, unit tests, env contract, web tests (about 5 min)
make up                                   # the 8-container stack
make seed                                 # reload the 80-case seed and stage the demo PDFs
make evals                                # live agent evals on Bedrock, writes evals/results/<date>/
```

Agents call live Amazon Bedrock only; there is no replay or mock mode. AWS credentials come from the environment (see `.env.example`). Never commit a `.env`.

## Read in this order

1. `BUILD-PLAN.md`: architecture, folder structure, schedule, definition of done.
2. `docs/governance/decision-log.md`: every decision and why (ADR-01…), and `gates-register.md`.
3. `docs/architecture/`: C4 diagrams, event catalogue, data model, sequences, OpenAPI export.
4. `docs/governance/defect-trail.md` and `failure-log.md`: real defects and build incidents, with how each was found and fixed.

## Status

This section is brought up to date at each merge and finalised at the release freeze. See `RUNNING.md` for what runs live today and `docs/governance/defect-trail.md` for open defects.
