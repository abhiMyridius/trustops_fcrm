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

Eight containers: `mongo`, `rabbitmq`, `minio`, `opa`, `api`, `harness`, `gateway`, `web`. Only the gateway (the web app) and MongoDB publish a port on your machine, both on 127.0.0.1. `make up-obs` adds Jaeger, Prometheus, Grafana and an OpenTelemetry collector.

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
| Token efficiency (5%) | Per-run budgets per tier and a per-case cap (160k tokens enforced, 120k target), in `ai/models/tiers.yaml`; usage metered in the evals; `ops/reports/token-analysis.md` (what the budgets were, what was wrong, what changed, measured usage per agent and per case). |
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
make demo                                 # LIVE: case 0151 through the 21 scenes (Bedrock, 100k to 160k tokens, about USD 0.5 a run, estimated and not measured on the committed chain; ask first, ADR-59)
make resilience DISRUPTIVE=1 AWS=1        # LIVE: twelve kill and restart drills, about 15 minutes (see docs/architecture/resilience.md)
make evals                                # live agent evals on Bedrock (about USD 1 projected on the committed chain, not measured; USD 3.14 on 6 Oct with an Opus judge), writes evals/results/<date>/
```

Agents call live Amazon Bedrock only; there is no replay or mock mode. AWS credentials come from the environment (see `.env.example`). Never commit a `.env`.

## Read in this order

1. `BUILD-PLAN.md`: start with "Status as built" (where the build departs from the plan, and why); then architecture, folder structure, definition of done.
2. `RUNNING.md`: how to run it, drive the demo by hand, every `make` target, troubleshooting.
3. `docs/governance/decision-log.md`: every decision and why (ADR-01…), and `gates-register.md`.
4. `docs/architecture/`: C4 diagrams, event catalogue, data model, sequences (with an index to the scenes), OpenAPI export.
5. `docs/governance/defect-trail.md` and `failure-log.md`: real defects and build incidents, with how each was found and fixed; `known-gaps.md`: what is not done, with the evidence.

## Status

A snapshot taken on the night of 6 October 2026, read from commands and committed files, not from memory, with the model rows updated on 7 October 2026 (ADR-62, ADR-65). The committed model chain changed on 7 October (Sonnet 5.5, Haiku and Nova, no Opus); each row below says which figures were measured on the 6 October chain (Sonnet 5.5 on mid and strong, Opus 5.5 as the critic, the eval judge and the strong tier's retry model), and none has been re-measured on the committed one. The committed chain differs from the 6 October chain in the judge tier (the critic and the eval judges run on Nova Pro, with a 28,000-token budget) and in the strong tier's retry model (Sonnet 5.5, not Opus 5.5). It is finalised at the release freeze (`ops/runbooks/release-checklist.md`). `RUNNING.md` says how to run it; `docs/governance/known-gaps.md` says what is not done.

| What | Result | Where it comes from |
|---|---|---|
| `make check` (the commit gate) | green on the tree of `53a1476`: ruff clean, 1004 files formatted, mypy on 618 files, **7707 unit tests passed, 4 skipped**, 121 of 121 Rego tests, 239 web tests, 813 `ai/` checks, env contract ok (54 interpolated variables, 87 documented) | run on the final tree |
| Integration tests | about 540 are marked `integration` and deselected by `make check` (590 deselected counting the e2e and resilience marks); `make test-int` selects them. The last run of all of them, on a throwaway Mongo replica set, was before the clean-up lanes: 525 passed, 14 skipped. The clean-up lanes re-ran the live-Mongo caseflow, common and audit tests (all passed); the whole suite was not repeated on the final build | run on 6 Oct; skips: 7 need `FCRM_TEST_RABBITMQ_URL`, 6 are named black-box skips, 1 is a missing real-document fixture |
| Resilience drills | **all twelve pass live**, in six sessions: the first found two product defects (an OPA that was never reloaded, a step retry window shorter than an api restart), both fixed, and a drill-side assumption. Every run's report is committed | `docs/architecture/resilience.md`, `ops/reports/resilience/`; D-92 to D-94, D-97 |
| The live pipeline (`make demo`) | the last complete run: **21 of 21 scenes** on the 6 October chain (Sonnet 5.5 on mid and strong, an Opus 5.5 critic) at 18:25 IST on 6 October, 5.3 minutes, 197 audit records with a verifying chain, every dead-letter queue empty, composite 15.9 in Band 3, approve with conditions 2-1, five conditions, 157,345 of the 160,000 enforced token cap (the 120,000 target was not met: four Guard 4 retries; a clean run is 110k to 115k). The live figures are the agents' and S2's own; 9.8 to 10.4 are unit-test goldens. **There is no complete live pass on the committed chain** (ADR-65, D-95, D-100): its critic is Nova Pro, which the 21-of-21 run did not use, and the figures above are the 6 October chain's. Four live runs on cheaper chains stopped early. Two of them ran on Sonnet 4.5 for mid and strong with Nova Pro as the judge (the economy profile, judge budget 20,000), a chain that ADR-62 committed and ADR-65 replaced: at scene 9 (assessment kept 3 of 19 claims after Guard 4, so the case had a score and no controls) and at scene 8 (the critic on Nova Pro passed its 20,000 budget, now 28,000). The other two, on Haiku and Nova only, stopped at scene 3 and scene 8. The committed Sonnet 5.5 combination has had no live run, stopped or complete | the integration lane's run on `e4c14f6` plus `4e156a1`; D-95 |
| Live black-box rules and Playwright scenes | 6 of 6 and 12 of 12 on an earlier build; not run on the final build, because they read a completed demo | D-95 |
| Live agent evals (committed baseline) | **8 of 9 gates pass** on the 6 October chain (run `7249d2a`: Sonnet 5.5 on mid and strong, Opus 5.5 as the critic and the judge); the assessment agent fails (`li_within_one` 0.70 against 0.85) and is reported failing. All 41 sampled rows completed. The run cost 525,250 tokens and about USD 3.14 at the verified prices (agents 0.62, judge 2.53; the committed `index.json` records USD 2.86 at the earlier placeholder prices). **There is no committed baseline for the committed chain.** A run of one row per agent on a Sonnet 4.5 chain (Nova Pro judge) gave 7 of 9 gates at about USD 0.44; it showed the runner, the Nova Pro judge and the dashboard work, it is not a baseline, it describes a chain that is no longer committed, and its files are not committed | `evals/results/2026-10-06/gate.json`, `index.json`; D-56, D-95 |
| Browser sweep of every screen (Playwright, four roles, no AI calls) | 43 tests on 7 Oct: 32 passed, 11 failed, every failure a real defect (D-105 to D-111), no server errors or crashes on 118 pages, access rules held in both directions. The fixes are merged and unit tested; **not yet re-run on the rebuilt application** | `docs/governance/test-report.md`; `npm run test:e2e:sweep` |
| Models | the committed chain since 7 October 2026 (`ai/models/tiers.yaml`, ADR-65, which supersedes only the Sonnet 4.5 choice of ADR-62): cheap Haiku 4.5 then Nova Lite; mid Sonnet 5.5 then Haiku 4.5; strong Sonnet 5.5, Sonnet 5.5 again, then Nova Pro (a retry runs on the fallbacks alone, so research and assessment retry on Sonnet 5.5); judge (the critic and the eval judges) Nova Pro then Haiku 4.5. Mid and strong set `effort` low, which Sonnet 5.5 takes. No Opus anywhere (ADR-62), and a unit test fails if one returns. Budgets per attempt: cheap 15,000, mid 15,000, strong 24,000, judge 28,000 tokens; per case 160,000 enforced, 120,000 target. Prices in USD per million tokens, Regional rate card, from the AWS Price List: Haiku 4.5 1.10 in and 5.50 out, Sonnet 5.5 2.20 and 11.00, Nova Lite 0.06 and 0.24, Nova Pro 0.80 and 3.20. A Sonnet 5.5 token costs about two thirds of a Sonnet 4.5 token. **Cost of one complete cycle** (a case from the owner's first upload to the committee outcome, all agents): about USD 0.47 (0.41 to 0.59, by the input and output split), about USD 0.34 with no retries and about USD 0.50 at the 160,000-token cap. This is an estimate from the token counts of the last complete run (6 October) at these prices, not a measurement on the committed chain; the same cycle on Sonnet 4.5 was estimated at about USD 0.65. A full `make evals` is projected at about USD 1, not measured. The economy profile of 6 October (a separate set of cheaper chains for cost-capped checks, with its own make targets, folder and model file) is removed; ADR-56 and ADR-60 stay in the decision log as history and ADR-62 supersedes them | `tests/unit/ai/models/test_tier_chains.py`; D-95, D-96 |
| The stack | eight containers, healthy; `api` takes about 85 s to become healthy (it embeds the 197 clauses on Titan) | `docker compose ps` |
| Decisions, defects, build failures | 62 ADRs; 100 defect rows; failure-log rows in `docs/governance/failure-log.md` | `docs/governance/` |
| API | 112 paths in the committed OpenAPI export; every route the web calls is in it | `docs/architecture/openapi/api.json`; `make openapi` leaves no diff |
| Not wired (print "not yet wired") | `nightly`, `contracts`, `api-gen`, `locks`, `build-usage`, `backup`, `restore` | `Makefile` |
| What is not done | no complete live acceptance pass on the committed model chain and no committed eval baseline for it (D-95, D-100); the Guard 4 retry model for the cheap tier (D-96); the outbox poison row, the web logout and H2's audit key (D-86 to D-88); a job parked by a transport failure may leave its case waiting (D-98); S3 Vectors is not in the image and Redis does nothing; no OCR; identity is a demo directory; the AWS path is a design | `docs/governance/known-gaps.md` |

How the build was done with AI across the six stages, with the `git log` queries that check it: `docs/governance/sdlc-evidence.md`. The AWS production path: `ops/aws/README.md`. Operational procedures: `ops/runbooks/`.
