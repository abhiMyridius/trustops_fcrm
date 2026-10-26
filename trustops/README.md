# TrustOps · FCRM Risk Assessment Workbench

Genius Hacks Q3 2026 (Myridius) entry. TrustOps assesses the AML/CFT and sanctions risk of a change at a US bank (a new product, vendor or geography) before it goes live. The product owner raises the case. AI agents propose, with citations. The FCRM analyst decides item by item. A deterministic engine scores. The risk committee decides Band 3–4 cases by hidden vote.

Nothing is approved or rejected automatically. Every rating traces to a source, every decision is hash-chained, and all data is synthetic.

The canonical demo is case **FCRM-2026-0151**: consumer remittances US → Mexico through a payout partner. It runs from the PO's first upload to a committee outcome, a closed case and a verified audit chain.

## Repository layout

The repository follows the brief's required shape:

| Path | What it holds | Start at |
|---|---|---|
| [`/src`](#src--application-code) | application code | `src/backend/apps/`, `src/frontend/` |
| [`/ai`](#ai--prompts-agent-config-guidance-files) | prompts, agent config, guidance files | `ai/README.md` |
| [`/docs/requirements`](#docsrequirements--the-spec-expanded-from-the-brief) | the spec expanded from the brief | `docs/requirements/spec.md` |
| [`/docs/architecture`](#docsarchitecture--design-decisions-and-diagrams) | design decisions and diagrams | `docs/architecture/README.md` |
| [`/docs/governance`](#docsgovernance--review-gates-and-the-rationale-for-each) | review gates and the rationale for each | `docs/governance/gates-register.md` |
| [`/evals`](#evals--eval-definitions-datasets-and-results) | eval definitions, datasets and results | `evals/README.md` |
| [`/tests`](#tests--automated-test-suites) | automated test suites | `tests/conftest.py` |
| [`/ops`](#ops--deployment-monitoring-token-analysis) | deployment, monitoring, token analysis | `ops/runbooks/README.md` |
| `README.md` | [setup](#setup), [walkthrough](#walkthrough), [decision log](#decision-log) | this file |

Two more files sit at this level: `BUILD-PLAN.md` (the plan, opening with "Status as built": every departure and why) and `RUNNING.md` (the full run guide). The approved planning set and the as-built reference documents (API, architecture, database, AI harness, events, governance, evals) are one level up in `../Documents/`, indexed by `../Documents/00-README.md`.

---

## `/src` — application code

Python 3.13 in a uv workspace (`src/backend/`) and a React 19 web app (`src/frontend/`). Every service and worker is its own package with its own `pyproject.toml`; production runs them as **two processes from one image** (ADR-26).

| Folder | ID | What it is |
|---|---|---|
| `src/backend/apps/api` | | `fcrm_api.main:app`: one FastAPI process that mounts X1 and S1–S5 as sub-apps by URL prefix |
| `src/backend/apps/harness` | | `python -m fcrm_harness`: one FastStream process that hosts H1–H4 on one broker |
| `src/backend/services/identity` | X1 | JWT login, refresh, JWKS, roles (cloned from AEO identity; see its `UPSTREAM.md`) |
| `src/backend/services/case` | S1 | The case record, the owner, analyst and internal routes, server-sent events, the PO fence |
| `src/backend/services/scoring` | S2 | The deterministic scoring engine and routing rules R1–R6 as data |
| `src/backend/services/policy` | S3 | Policy corpus, controls, precedents, hybrid search, versioned configuration |
| `src/backend/services/audit` | S4 | Hash-chained audit trail, examiner, override analytics, agent quality |
| `src/backend/services/decision` | S5 | Committee packs, votes, question threads, outcomes |
| `src/backend/workers/caseflow` | H1 | Caseflow: the case state machine, gates, routing; calls the services over HTTP |
| `src/backend/workers/agent-worker` | H2 | Runs the nine agents, one Pydantic AI call per job on Amazon Bedrock |
| `src/backend/workers/ingestion-worker` | H3 | Document ingestion to canonical text |
| `src/backend/workers/context-worker` | H4 | Context and vector index builder |
| `src/backend/packages/` | shared | `contracts` (every shape), `common` (settings, Mongo, outbox, broker), `guardrails` (the seven guards), `agents` and `tools` (context packs, tool backings), `prompts-loader`, `seed` (the generated P7 seed), `evals` (the eval runner) |
| `src/frontend/` | W1 | React 19, Vite, TypeScript, Tailwind, TanStack Query; one app for every role. Its own `CLAUDE.md` has the npm commands |

Rules that hold across `/src`: every shape is imported from `fcrm_contracts`; a service never reads another service's database; a service never publishes to RabbitMQ inside a request (it writes an outbox row in the same Mongo transaction); scores come only from S2.

## `/ai` — prompts, agent config, guidance files

The AI layer is files, locked by checksum and mounted read-only into the harness. `ai/README.md` explains each folder; `ai/CLAUDE.md` has the rules for editing them.

| Folder | Holds |
|---|---|
| `ai/agents/` | The nine agent specs (`*.agent.yaml`): screening, intake, research, assessment, critic, narrative, examiner, watchlist, policy_gap |
| `ai/prompts/` | Versioned, immutable prompt files per agent; `MANIFEST.yaml` names the current version of each; `CHANGELOG.md` |
| `ai/tools/`, `ai/skills/` | 13 read-only tool specs and the skills prompts include (for example `cite_verbatim`, `declare_gap`) |
| `ai/models/tiers.yaml` | The only file that names a model: tiers, Bedrock ids, fallbacks, budgets, prices |
| `ai/guards/` | The seven guards' order and behaviour (`guards.yaml`), thresholds, injection patterns, PII entities, restricted terms |
| `ai/policies/` | Agent-to-tool permits for OPA (`tools.rego`, `tool_permits.json`) |
| `ai/registry.lock/`, `ai/_tools/` | Checksums of agents, tools, skills and prompts; `compute_locks.py` and `validate_ai.py` (run by `make check`) |
| `ai/guidance/` | Build-time guidance for Claude Code: coding standards, commit convention, worktree protocol, subagent brief template, review checklist, hooks |

## `/docs/requirements` — the spec expanded from the brief

| File | Holds |
|---|---|
| `spec.md` | The specification expanded from the brief |
| `personas.md`, `rules.md`, `glossary.md`, `open-questions.md` | The people, the product rules, the terms, what was left open |
| `research/` | Research notes the spec was built from: the brief and rubric, the build plan and decision log, the blueprint and walkthrough, the BA's prototype, the AEO reuse inventory, the walkthrough seed extraction (the golden figures), runtime facts |
| `source/` | The approved inputs as received: the brief, the team's planning set (build plan, blueprint, decision log v1.3, flow map, prototype teardown), the concept page, the kickoff transcript, and the v2 walkthrough the seed is generated from |

## `/docs/architecture` — design decisions and diagrams

Every diagram is Mermaid and renders on GitHub. Start at `docs/architecture/README.md`.

| File | Holds |
|---|---|
| `c4-context.md`, `c4-container.md` | The system on one page, then its eight containers |
| `sequences/` | One sequence per stage of the case (intake, assessment, override, committee, close, handoff, oversight, token ledger), mapped to the demo scenes |
| `data-model.md` | Every collection with its owning service and contract |
| `event-catalogue.md` | Events between services and their routing |
| `resilience.md` | Twelve kill-and-restart drills and what they found |
| `ux-decisions.md` | User-experience decisions and how they were reached |
| `openapi/api.json` | The merged OpenAPI export of the `api` process, generated by `make openapi` |
| `design/` | The full design as written on 3 October: backend (A), AI layer (B), web, tests, ops and SDLC (C) |
| `folder-structure.md` | Where the code lives |

## `/docs/governance` — review gates and the rationale for each

| File | Holds |
|---|---|
| `gates-register.md` | Every product and SDLC gate, the risk it controls and why it sits where it does. Enforced by Guard 7: a person decides, never the service account |
| `decision-log.md` | ADR-01 to ADR-89: every decision, why, which earlier row it amends, its status |
| `rules.md`, `visibility-matrix.md` | The product rules and what each role may see (the PO fence) |
| `audit-chain.md` | The hash-chain specification |
| `defect-trail.md`, `failure-log.md` | Real defects (to D-131) and build incidents, with how each was found and fixed |
| `test-report.md` | What was tested, when, and with what result |
| `sdlc-evidence.md` | How AI was used across the six SDLC stages, with the `git log` queries that check it |
| `known-gaps.md` | What is not done, with the evidence (dated 6 October; read it with the decision log) |

Tables in `docs/governance` are append-only: the pre-commit hook rejects a change that rewrites or removes an existing row.

## `/evals` — eval definitions, datasets and results

| Where | Holds |
|---|---|
| `evals/README.md` | What a run measures, the live runs so far, the budgets table, datasets, findings |
| `src/backend/packages/evals/fcrm_evals/` | The eval definitions: the runner, metrics, the judge, gate logic, publishing |
| `src/backend/packages/evals/fcrm_evals/datasets/data/` | The nine datasets, one JSONL file per agent |
| `evals/fixtures/documents/` | The synthetic demo PDFs and their ground truth (`facts.json`) |
| `evals/results/<date>/` | Each run: per-agent outputs, `gate.json`, `index.json`, `report.md`, a single-page `dashboard.html`; `baseline.json`, `trend.jsonl` and `latest` sit beside them |

Each agent has one primary metric and one promotion gate. A prompt or model change carries its live eval evidence in the commit's `Gate:` trailer. Live evals call Bedrock and cost money: ask the operator first (ADR-59).

## `/tests` — automated test suites

Tests never sit beside source; fixtures live only in `tests/conftest.py`.

| Folder | Holds | Run with |
|---|---|---|
| `tests/unit/` | Unit tests mirroring `src/` (apps, services, workers, packages, ai, ops) | `make check` (the commit gate) or `make test-unit` |
| `tests/integration/` | Black-box scenarios, contract tests, cross-package, live-Mongo, service and worker tests | `make test-int` (Docker, or `FCRM_TEST_MONGO_URI`) |
| `tests/resilience/` | Drills D1 to D8: harness, RabbitMQ, Mongo, API and identity outages, dead-letter replay, handoff, the audit chain | `make resilience` (disruptive and AWS drills are opt-in) |
| `tests/e2e/` | The live demo of case 0151 and the live black-box rules | `make demo`, `make e2e` (live Bedrock: ask first) |
| `src/frontend/src/test/` | Web unit, contract and accessibility tests (Vitest) | `make web-check` |
| `src/frontend/e2e/` | Playwright scenes and the browser sweep | `npm run test:e2e` in `src/frontend` |

## `/ops` — deployment, monitoring, token analysis

| Folder | Holds |
|---|---|
| `ops/nginx/` | The gateway: routing, token check, rate limits, size limits |
| `ops/opa/` | Route authorization policy and data (default deny, deny overrides) |
| `ops/mongo/` | Replica set, users (an insert-only audit role), validators and indexes, run on every start |
| `ops/rabbitmq/` | The broker topology: exchanges, work, retry and dead-letter queues, policies |
| `ops/minio/` | Object storage buckets |
| `ops/otel/`, `ops/openobserve/` | Telemetry collector and the three dashboards (`make up-obs`) |
| `ops/scripts/` | Env and key generation, seed reset and verify, dead-letter tool, OpenAPI export, token report, resilience drills, commit-message lint |
| `ops/runbooks/` | Start, stop and reset; restart a worker; dead-letter replay; Bedrock outage; secrets and keys; backup and restore; verify the audit chain; incident; release checklist |
| `ops/reports/` | `token-analysis.md` (budgets, what changed, measured usage per agent and per case) and the resilience drill reports |
| `ops/aws/README.md` | The AWS production path (a design, not built by this run) |

Compose files at this level: `docker-compose.yaml` (the stack), `compose.dev.yaml` (hot reload and the Vite dev server), `compose.debug.yaml` (infrastructure ports), `compose.test.yaml` (the test overlay).

---

## Setup

`RUNNING.md` is the full guide, including macOS, Linux and Windows (WSL 2) notes, every `make` target and troubleshooting.

**You need:** Docker with Compose v2; git, make, openssl, python3 and curl; [uv](https://docs.astral.sh/uv/); Node 22 only for the web checks; and AWS keys allowed to use Amazon Bedrock.

```bash
git clone https://github.com/abhiMyridius/trustops_fcrm.git
cd trustops_fcrm/trustops        # run make from here
make bootstrap                    # tools, Python workspace, git hooks, images
make env && make keys             # .env with generated local secrets, the JWT keypair
# add AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY to .env
make up                           # the eight-container stack
make seed                         # the 80-case seed and the demo PDFs
```

Open http://localhost:8080. Every seeded user's password is `demo-password-2026`.

| Who | User name | Role |
|---|---|---|
| Priya Nair | `pnair` | product owner who raises the change |
| Rahul Iyer | `riyer` | FCRM analyst who reviews it |
| Ana Mendes, Joseph Okafor, Lin Chen | `amendes`, `jokafor`, `lchen` | risk committee that votes |
| Panel Observer | `observer` | read-only harness monitor |

The stack is eight containers: `mongo`, `rabbitmq`, `minio`, `opa`, `api`, `harness`, `gateway`, `web`. Only the gateway and MongoDB publish a port, both on 127.0.0.1. `make up-obs` adds an OpenTelemetry collector and OpenObserve (traces, metrics, logs, three dashboards). Agents call live Amazon Bedrock only; there is no replay or mock mode. Never commit a `.env`.

**Day-to-day commands** (from this folder):

```bash
make check          # the commit gate: lint, types, OPA tests, ai/ checks, unit tests, env contract, web tests (about 6 minutes)
make test-int       # integration tests on Docker or FCRM_TEST_MONGO_URI
make openapi        # regenerate docs/architecture/openapi/api.json after any route change
make verify case=FCRM-2026-0118     # verify one case's audit chain
make dlq            # dead-letter queues and their depths
make down           # stop the stack (make down-v also deletes the data)
```

**What costs money** (each calls Bedrock; ask the operator first, ADR-59): `make demo`, `make e2e`, `make evals`, `make evals-full`, and the AWS resilience drills.

## Walkthrough

The demo follows case **FCRM-2026-0151** through 21 scenes, from the owner's first upload to a closed case:

1. **Intake.** Priya raises the case and uploads documents; the intake agent proposes change types and field values, each with a page and line source; she confirms or corrects every one and submits.
2. **Agents at work.** Caseflow screens the case (fast lane or full assessment); research and assessment run in parallel; S2 computes the first score; the critic reviews on a different model family.
3. **Analyst review.** Rahul confirms the lane, reviews every clause, rating, control and critic finding item by item, acknowledges the boundary flag, asks the owner one round of clarification questions, and overrides vendor due diligence (CTL-T01, 7.6 → 4.5) with a reason. The score moves from **9.8 (Band 2, boundary flag)** to **10.4 (Band 3)**.
4. **Recommendation and pack.** The narrative agent drafts the recommendation; Rahul edits and locks it; Caseflow builds a frozen, hashed committee pack; Rahul releases it.
5. **Committee.** Ana, Joseph and Lin read the pack (Summary and Submission tabs), may ask questions, and vote by hidden ballot: **approve with conditions, 2–1**. A 1-1-1 split would re-vote (R6).
6. **Close and oversight.** Three conditions are activated and tracked; the owner sees a plain-language outcome, never a score; the audit chain of **67 records** verifies.

The bold figures are the seeded goldens that unit tests check (`docs/requirements/research/06-walkthrough-seed-extraction.md`); a live run computes its own numbers (ADR-27). Case 0151 is seeded unassigned at intake with no documents; its first upload opens the run (ADR-39).

Three ways to see it:

- **By hand:** `RUNNING.md` section 4, "Drive the demo by hand", with the personas above.
- **Automated, live:** `make demo` drives all 21 scenes on the running stack (Bedrock; ask first).
- **Without running anything:** `../Documents/04-walkthrough.html` is an animated, as-built walkthrough of the app's screens for case 0151 (24 scenes). `docs/architecture/sequences/README.md` maps each scene to its sequence diagram.

## Decision log

Every decision is a row, and every commit names the decisions it implements.

| Record | Holds |
|---|---|
| `docs/requirements/source/03-decision-log.html` | The team's decision log v1.3: D-01 to D-97, frozen on 3 October |
| `docs/governance/decision-log.md` | **ADR-01 to ADR-89**: the build's decisions, each saying which D-row it adds to or amends, its rubric line and its status (proposed → accepted by the operator → superseded) |
| `docs/governance/defect-trail.md` | D-98 onward (to D-131): defects found and how each was fixed |
| `BUILD-PLAN.md`, "Status as built" | Every departure from the plan, with its reason and decision row |

Commits follow `type(scope): summary [D-xx, ADR-nn]` with `Stage:` and `Gate:` trailers (`ai/guidance/commit-convention.md`), enforced by git hooks.

Decisions that shape the system:

| ADR | Decision |
|---|---|
| ADR-02 | RabbitMQ is the bus, with a transactional outbox: state and event commit together |
| ADR-03 | Amazon Bedrock only; no direct vendor keys |
| ADR-04, ADR-65 | Model tiers; the critic runs on a different model family from the assessor; no Opus in any chain (ADR-62) |
| ADR-07 | No agent has a scoring tool; scores come only from S2 |
| ADR-14 | One retry on the first fallback with the error shown, then a human handoff |
| ADR-26 | Two Python processes from one image, instead of one container per unit |
| ADR-27 | Live models only; no replay or mock mode |
| ADR-28 | HTTP work runs as idempotent steps outside the database transaction |
| ADR-31, ADR-36 | Two identical votes win; a 1-1-1 split is `no_majority` and re-votes |
| ADR-32 | The PO fence has three independent layers |
| ADR-59 | A live model call needs the operator's approval |
| ADR-81, ADR-83 | One attempt may spend 64k tokens on every tier; a case is capped at 250k (120k target) |

---

## Where the rubric is answered

| Rubric line | Where to look |
|---|---|
| AI harness and agent orchestration (30%) | Nine agents, each one Pydantic AI call, in `ai/agents/`; orchestrated by H1 (`src/backend/workers/caseflow`, design in `docs/architecture/sequences/`). Seven guards in `ai/guards/` and `src/backend/packages/guardrails`; tool permits through OPA in `ai/policies/`; Bedrock tiers and fallbacks in `ai/models/tiers.yaml`. |
| AI across the six SDLC stages (20%) | `docs/governance/sdlc-evidence.md` (stage by stage: what AI did, the artefacts, the gate, and the `git log` queries that check it), `ai/guidance/` (coding standards, commit convention, worktree protocol, subagent brief template, hooks), `docs/governance/` (decision log, gates register, defect trail, failure log), commit history with `Stage:` and `Gate:` trailers. |
| Human-in-the-loop gates with rationale (15%) | `docs/governance/gates-register.md`: the product gates and the SDLC gates, each with the risk it controls and why it sits there. Enforced by Guard 7: a person decides, never the service account. |
| Evals (10%) | `evals/` and `src/backend/packages/evals`: nine datasets, deterministic metrics, a live Bedrock run over a sample, a DeepEval judge, one gate per agent and a single-page dashboard (`evals/results/*/dashboard.html`). |
| Context engineering (10%) | Context packs built per agent (`src/backend/packages/agents`), a 197-clause synthetic policy corpus searched by hybrid retrieval (Titan vectors in memory fused with keyword ranking; precedents and controls by keyword; S3, H4), verbatim quotes with a source reference, tool results cited by reference. |
| Production readiness (5%) | Eight-container stack with health checks, retry and dead-letter queues, an insert-only audit role, OPA route authorization, a PO response fence, OpenTelemetry. `ops/runbooks/`, `ops/aws/README.md` (the AWS path, a design), `docs/governance/known-gaps.md` (what is not done). |
| Token efficiency (5%) | Per-attempt budgets per tier (64k an attempt on every tier) and a per-case ledger (250k enforced, 120k target), in `ai/models/tiers.yaml` (ADR-81, ADR-83); usage metered in the evals; `ops/reports/token-analysis.md`. |
| Deterministic logic over an LLM (5%) | Scoring (S2), routing rules R1–R6, the vote tally, release checks, completion, chain verification: all code. Agents propose inputs and never write a score. |

## What stays deterministic

- **Scores** come only from S2: inherent = L × I, residual = inherent × retention, composite weighted over the in-scope categories.
- **Golden numbers:** the demo composite is 9.8 (Band 2, boundary flag) and moves to 10.4 (Band 3) when vendor due diligence is overridden from 7.6 to 4.5. These are unit tests.
- **Policy** is quoted verbatim with a source reference, never generated.
- **The audit trail** is hash-chained and append-only.
- **The PO never sees** a score, band or rating, in any response, stream or page.

## Status

A snapshot taken on the night of 6 October 2026, read from commands and committed files, not from memory; the model rows were updated on 7 October (ADR-62, ADR-65), and the budget, stack, decision and API rows on 26 October. Live figures were measured on the 6 October chain (Sonnet 5.5 on mid and strong, Opus 5.5 as the critic, the eval judge and the strong tier's retry model); none has been re-measured on the committed chain, which runs the critic and the eval judges on Nova Pro and retries the strong tier on Sonnet 5.5. `docs/governance/known-gaps.md` says what is not done.

| What | Result | Where it comes from |
|---|---|---|
| `make check` (the commit gate) | green on the tree of `53a1476` (7 Oct): ruff clean, 1004 files formatted, mypy on 618 files, **7707 unit tests passed, 4 skipped**, 121 of 121 Rego tests, 239 web tests, 813 `ai/` checks, env contract ok | run on the 7 October tree; not repeated for this update |
| Integration tests | about 540 are marked `integration` and deselected by `make check`; `make test-int` selects them. The last run of all of them, on a throwaway Mongo replica set: 525 passed, 14 skipped | run on 6 Oct; skips: 7 need `FCRM_TEST_RABBITMQ_URL`, 6 are named black-box skips, 1 is a missing real-document fixture |
| Resilience drills | **all twelve pass live**, in six sessions: the first found two product defects (an OPA that was never reloaded, a step retry window shorter than an api restart), both fixed, and a drill-side assumption. Every run's report is committed | `docs/architecture/resilience.md`, `ops/reports/resilience/`; D-92 to D-94, D-97 |
| The live pipeline (`make demo`) | the last complete run: **21 of 21 scenes** on the 6 October chain at 18:25 IST on 6 October, 5.3 minutes, 197 audit records with a verifying chain, every dead-letter queue empty, composite 15.9 in Band 3, approve with conditions 2-1, five conditions, 157,345 tokens against the cap of the time (160,000). The live figures are the agents' and S2's own; 9.8 to 10.4 are unit-test goldens. **There is no complete live pass on the committed chain** (ADR-65, D-95, D-100). Four live runs on cheaper chains stopped early; see D-95 | the integration lane's run on `e4c14f6` plus `4e156a1`; D-95 |
| Live black-box rules and Playwright scenes | 6 of 6 and 12 of 12 on an earlier build; not run on the final build, because they read a completed demo | D-95 |
| Live agent evals (committed baseline) | **8 of 9 gates pass** on the 6 October chain (run `7249d2a`); the assessment agent fails (`li_within_one` 0.70 against 0.85) and is reported failing. All 41 sampled rows completed; 525,250 tokens, about USD 3.14 at the verified prices (the committed `index.json` records USD 2.86 at earlier placeholder prices). **There is no committed baseline for the committed chain** | `evals/results/2026-10-06/gate.json`, `index.json`; D-56, D-95 |
| Browser sweep of every screen (Playwright, four roles, no AI calls) | 43 tests on 7 Oct: 32 passed, 11 failed, every failure a real defect (D-105 to D-111), no server errors on 118 pages, access rules held in both directions. The fixes are merged and unit tested; not yet re-run on the rebuilt application | `docs/governance/test-report.md`; `npm run test:e2e:sweep` |
| Models | the committed chain since 7 October (`ai/models/tiers.yaml`, ADR-65): cheap Haiku 4.5 then Nova Lite; mid Sonnet 5.5 then Haiku 4.5; strong Sonnet 5.5, Sonnet 5.5 again, then Nova Pro; judge (the critic and the eval judges) Nova Pro then Haiku 4.5. No Opus anywhere (ADR-62), and a unit test fails if one returns. Prices in USD per million tokens (AWS Price List, Regional): Haiku 4.5 1.10 in and 5.50 out, Sonnet 5.5 2.20 and 11.00, Nova Lite 0.06 and 0.24, Nova Pro 0.80 and 3.20. One complete cycle was estimated at about USD 0.47 from the 6 October token counts at these prices; not measured on the committed chain | `tests/unit/ai/models/test_tier_chains.py`; D-95, D-96 |
| Token budgets | one attempt may spend 64,000 tokens on every tier: cheap and mid 60,000 plus a 4,000 output cap, strong and judge 56,000 plus 8,000 (ADR-81, ADR-83). Per case: 250,000 enforced, 120,000 target. An input too large for its tier's pack is split into parts and merged by code (ADR-77, ADR-78) | `ai/models/tiers.yaml` |
| The stack | eight containers, healthy. `api` reuses the clause embeddings stored in Mongo at start-up and embeds only new or changed clauses (`SKIP_EMBED_IF_PRESENT`, default true); a full re-embed of the 197 clauses takes about 85 s | `docker compose ps`; commit `7199686` |
| Decisions, defects, build failures | 89 ADRs (ADR-77 to ADR-80 and ADR-82 still proposed); the defect trail runs to D-131; failure-log rows in `docs/governance/failure-log.md` | `docs/governance/` |
| API | 117 paths and 127 operations in the committed OpenAPI export; every route the web calls is in it | `docs/architecture/openapi/api.json`; `make openapi` leaves no diff |
| Not wired (print "not yet wired") | `nightly`, `contracts`, `api-gen`, `locks`, `build-usage`, `backup`, `restore` | `Makefile` |
| What is not done | no complete live acceptance pass on the committed model chain and no committed eval baseline for it (D-95, D-100); the Guard 4 retry model for the cheap tier (D-96); the outbox poison row, the web logout and H2's audit key (D-86 to D-88); a job parked by a transport failure may leave its case waiting (D-98); S3 Vectors is not in the image and Redis does nothing; no OCR; identity is a demo directory; the AWS path is a design; backup and restore are manual | `docs/governance/known-gaps.md` |

How the build was done with AI across the six stages, with the `git log` queries that check it: `docs/governance/sdlc-evidence.md`. The AWS production path: `ops/aws/README.md`. Operational procedures: `ops/runbooks/`.
