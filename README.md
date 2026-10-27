# TrustOps · FCRM Risk Assessment Workbench

Genius Hacks Q3 2026 (Myridius) entry. TrustOps assesses the AML/CFT and sanctions risk of a change at a US bank (a new product, vendor, geography, channel or segment) **before it goes live**.

- The **product owner** raises a case and uploads documents.
- **Nine AI agents** propose, each with a citation to a source.
- The **FCRM analyst** accepts, overrides or removes every proposal, item by item, with a reason.
- A **deterministic engine** scores. Agents never write a score.
- The **risk committee** decides Band 3 to 4 cases by hidden vote with a rationale.

Nothing is approved or rejected automatically. Every decision is hash-chained. All data is synthetic.

---

## Repository map

This repository holds three siblings. **The product is `trustops/`.** The other two are reference material.

```text
trustops_fcrm/
├── README.md                 # this file: the map of the repository
├── CLAUDE.md                 # rules for AI coding assistants working here
├── .gitattributes            # LF line endings everywhere; binaries marked
├── .gitignore                # worktrees, .DS_Store, the personal VS Code workspace file
│
├── Documents/                # THE SPEC. Approved planning set, the brief, the as-built references
│   ├── 00-README.md          #   index of the set and the decisions in force (read first)
│   ├── 01-build-plan.html    #   master spec: services, agents, guards, scoring, data, evals
│   ├── 02-blueprint.html     #   component IDs (S1–S5, H1–H4, X1) and the assessment sequence
│   ├── 03-decision-log.html  #   the team's 57 planning decisions (D-01 … D-97, not contiguous)
│   ├── 04-walkthrough.html   #   v3 interactive demo of the built app: 24 scenes, case 0151
│   ├── 05-prototype-teardown.html   # what the FCRM_v2 prototype got right and wrong
│   ├── 06-flow-map.html      #   the full case flow: entry → intake → assess → review → decide → close
│   ├── 07-api-reference.md … 13-evals-and-quality-report.md   # as-built references (API, architecture,
│   │                         #   database, AI harness, events, governance, evals)
│   ├── Genius Hacks Q3 2026.html, Genius_Hacks_Connect.pdf   # the brief and the rubric
│   ├── TrustOps — How the Risk Assessment Workbench Works.html  # the product concept
│   ├── build-report-2026-10-07.html, git-history-2026-10-07.html  # how the build ran, by the numbers
│   └── trascript - session1 - genius-hacks-3.txt   # organisers' kickoff call (context, not spec)
│
├── FCRM_v2/                  # REFERENCE ONLY. The BA's clickable prototype: React + Vite, mock data, no backend
│   ├── src/                  #   pages, in-memory store, seed; three personas by URL prefix
│   └── README.md             #   how to run it (npm run dev on :5173). Never imported by trustops/
│
└── trustops/                 # THE PRODUCT. See the tree below
```

The product follows the layout the brief requires: `/src`, `/ai`, `/docs/{requirements,architecture,governance}`, `/evals`, `/tests`, `/ops`.

```text
trustops/
├── README.md                 # the product README: layout, setup, walkthrough, rubric map, status
├── RUNNING.md                # the full run guide (macOS, Linux, Windows via WSL 2)
├── BUILD-PLAN.md             # the plan, opening with "Status as built": every departure and why
├── CLAUDE.md                 # the build rules: commands, precedence, component IDs, the live-call gate
├── Makefile                  # every command (make help); run make only from this folder
├── docker-compose.yaml       # the 8-container stack; profiles obs (OpenObserve) and cache (Redis)
├── compose.debug.yaml        # publishes the infrastructure ports (make up-debug)
├── compose.dev.yaml          # hot-reload overrides (make up-dev)
├── compose.test.yaml         # separate test project for make e2e and make test-int
├── ruff.toml, mypy.ini, pytest.ini   # lint, strict typing, test discovery
│
├── src/                      # APPLICATION CODE
│   ├── backend/              #   Python 3.13 uv workspace; one Docker image, two processes (ADR-26)
│   │   ├── apps/
│   │   │   ├── api/          #     fcrm_api: one FastAPI process mounting X1 and S1–S5 by URL prefix
│   │   │   └── harness/      #     fcrm_harness: one FastStream process hosting H1–H4 on one broker
│   │   ├── services/
│   │   │   ├── identity/     #     X1  JWT login, refresh, JWKS, roles (cloned from AEO; UPSTREAM.md)
│   │   │   ├── case/         #     S1  the case record, PO / analyst / internal routes, SSE, the PO fence
│   │   │   ├── scoring/      #     S2  the deterministic scoring engine; routing rules R1–R6 as data
│   │   │   ├── policy/       #     S3  policy corpus (197 clauses), controls, precedents, hybrid search
│   │   │   ├── audit/        #     S4  hash-chained append-only audit trail, examiner, analytics
│   │   │   └── decision/     #     S5  committee packs, hidden votes, question threads, outcomes
│   │   ├── workers/
│   │   │   ├── caseflow/     #     H1  the case state machine, gates, routing; calls services over HTTP
│   │   │   ├── agent-worker/ #     H2  runs the nine agents: one Pydantic AI call per job on Bedrock
│   │   │   ├── ingestion-worker/  # H3  documents → canonical text with page:line anchors (AEO clone)
│   │   │   └── context-worker/    # H4  context and vector index builder (AEO clone)
│   │   └── packages/         #     shared libraries
│   │       ├── contracts/    #       every DTO, event and enum; the single source of every shape
│   │       ├── common/       #       settings, Mongo, outbox, broker, HTTP client, errors
│   │       ├── guardrails/   #       the seven guards (input scan, permits, budgets, PO fence …)
│   │       ├── agents/       #       agent specs, context-pack builders, model tiers
│   │       ├── tools/        #       the 13 read-only tools agents may call
│   │       ├── prompts-loader/   #   loads versioned prompts from ai/ and checks the locks
│   │       ├── seed/         #       the generated 80-case seed (make gen-seed; never hand-edited)
│   │       └── evals/        #       the eval runner, metrics, judge, dashboard
│   └── frontend/             #   W1  React 19 + TypeScript + Vite + Tailwind + TanStack Query
│       ├── src/features/     #     po/, analyst/, committee/, monitor/, auth/, common/ (one app, every role)
│       ├── src/api/          #     endpoints, typed clients, SSE stream, hand-written DTOs
│       ├── src/mocks/        #     MSW mock backend from the seed (tests and npm run dev:msw only)
│       ├── src/test/         #     vitest specs (the only place tests live)
│       └── CLAUDE.md         #     npm commands and the frontend rules
│
├── ai/                       # THE AI LAYER: files, checksum-locked, mounted read-only into the harness
│   ├── agents/               #   nine *.agent.yaml: screening, intake, research, assessment, critic,
│   │                         #   narrative, examiner, watchlist, policy_gap
│   ├── prompts/              #   versioned immutable prompts; MANIFEST.yaml names the current version
│   ├── models/tiers.yaml     #   the ONLY place a model id lives: Bedrock tiers, fallbacks, budgets, prices
│   ├── guards/               #   guard config: restricted terms, injection patterns, PII, thresholds
│   ├── tools/, skills/       #   tool specs and reusable prompt skills (cite_verbatim, declare_gap …)
│   ├── policies/             #   OPA Rego: route authorization and agent-to-tool permits
│   ├── schemas/              #   JSON schemas every agent output must satisfy
│   ├── guidance/             #   coding standards, commit convention, subagent briefs, hooks
│   ├── registry.lock/        #   checksums of everything above (make ai-check)
│   ├── _tools/               #   validate_ai.py and compute_locks.py
│   └── README.md, CLAUDE.md  #   what each folder is, and the rules for editing it
│
├── docs/
│   ├── requirements/         # THE SPEC EXPANDED: spec.md, personas, glossary, rules, open questions
│   │   ├── source/           #   copy of Documents/ (its walkthrough is the older v2, which embeds the seed)
│   │   └── research/         #   extraction notes, including the walkthrough seed figures
│   ├── architecture/         # DESIGN: C4 context and container, data model, event catalogue (75 types),
│   │   ├── design/           #   A-backend, B-ai-layer, C-web-tests-ops-sdlc: the three design documents
│   │   ├── sequences/        #   one sequence per stage of the flow
│   │   ├── openapi/api.json  #   generated by make openapi (117 paths); never hand-edited
│   │   └── resilience.md     #   the twelve drills and their results
│   ├── governance/           # GATES AND RATIONALE (every table is append-only; a hook enforces it)
│   │   ├── decision-log.md   #   ADR-01 … ADR-89, each citing the planning decision it extends
│   │   ├── gates-register.md #   every human gate: the risk it controls and why it sits there
│   │   ├── defect-trail.md   #   D-01 … D-131
│   │   ├── failure-log.md    #   build failures and what was learned
│   │   ├── sdlc-evidence.md  #   AI across the six SDLC stages, with the git log queries that prove it
│   │   ├── known-gaps.md     #   what is not done (dated, append-only)
│   │   ├── audit-chain.md, visibility-matrix.md, rules.md, test-report.md
│   ├── feature-inventory.md  # every feature by role with its status and evidence
│   └── presentation/         # the pitch decks (trustops-ai-final-deck.html is the final one) and the build report
│
├── evals/                    # EVALS
│   ├── README.md             #   nine datasets, 27 deterministic metrics, the DeepEval judge, the gate rule
│   ├── fixtures/documents/   #   the three generated demo PDFs
│   └── results/              #   dated runs; baseline.json, trend.jsonl, latest → the committed baseline
│
├── tests/                    # TESTS (never beside source; fixtures only in conftest.py)
│   ├── unit/                 #   mirrors src/: ai/, apps/, packages/, services/, workers/, ops/
│   ├── integration/          #   blackbox/ (the product rules), contract/, cross_package/, live_mongo/ …
│   ├── e2e/                  #   the 21-scene live demo (make demo) and live black-box rules
│   └── resilience/           #   the twelve drills (make resilience)
│
└── ops/                      # DEPLOYMENT, MONITORING, TOKEN ANALYSIS
    ├── nginx/                #   the gateway on :8080, the single entry point for web and API
    ├── opa/                  #   OPA config and permissions.json (the role map, as data)
    ├── mongo/, rabbitmq/, minio/   # init scripts, the queue topology (definitions.json), buckets
    ├── otel/, openobserve/   #   the collector config and the three dashboards
    ├── aws/                  #   the AWS production path (a design)
    ├── runbooks/             #   start/stop/reset, DLQ replay, outages, secrets, release checklist …
    ├── reports/              #   token analysis and the resilience drill reports
    └── scripts/              #   make_env, seed_reset, dlq, export_openapi, the git hooks' checkers …
```

Not in the tree, by design: `.env` (generated by `make env`, never committed), `jwt_keys/` (generated by `make keys`), `.claude/worktrees/` (agent worktrees, gitignored) and `trustops/Hackathon.code-workspace` (personal).

---

## Quick start

Requires Docker, Node 22, `uv` and AWS credentials with Bedrock access. Run from `trustops/`.

```bash
cd trustops
make bootstrap          # opa binary, uv sync, git hooks, pull images
make env && make keys   # .env with generated secrets, the JWT keypair; then add your AWS keys to .env
make up                 # 8 containers: mongo, rabbitmq, minio, opa, api, harness, gateway, web
make seed               # the 80-case seed and the demo PDFs
open http://localhost:8080
```

| Command | What it does |
|---|---|
| `make check` | the commit gate: ruff, mypy, OPA, `ai/` locks, unit tests, env contract, web tests |
| `make test-int` | the integration suite on a throwaway Mongo |
| `make demo` | drives case FCRM-2026-0151 through all scenes on the live stack (**calls Bedrock, costs money**) |
| `make evals` | live agent evals on a sample (**calls Bedrock, costs money**) |
| `make up-obs` | adds the OpenTelemetry collector and OpenObserve on :5080 |
| `make reset` | tears everything down, deletes the data and starts clean |
| `make help` | the full list |

Full guide, including Windows via WSL 2: [`trustops/RUNNING.md`](trustops/RUNNING.md).

---

## Where to read next

| You want to | Read |
|---|---|
| Understand the product | [`Documents/TrustOps — How the Risk Assessment Workbench Works.html`](Documents/), then the walkthrough [`Documents/04-walkthrough.html`](Documents/04-walkthrough.html) |
| Understand the build | [`trustops/README.md`](trustops/README.md), then [`trustops/BUILD-PLAN.md`](trustops/BUILD-PLAN.md) "Status as built" |
| See the architecture | [`trustops/docs/architecture/README.md`](trustops/docs/architecture/README.md) and the three design documents |
| Know why a decision was made | [`trustops/docs/governance/decision-log.md`](trustops/docs/governance/decision-log.md) (ADR rows) |
| Know what is not done | [`trustops/docs/governance/known-gaps.md`](trustops/docs/governance/known-gaps.md) |
| See how the rubric is answered | the "Where the rubric is answered" table in [`trustops/README.md`](trustops/README.md) |
| Change code | [`trustops/CLAUDE.md`](trustops/CLAUDE.md) first; `ai/` and `src/frontend/` have their own |

---

## The rules that never bend

- **Four outcomes, all human:** approve, approve with conditions, defer, reject. Nothing is automatic.
- **Scores come only from the engine** (S2): inherent = likelihood × impact, residual = inherent × retention, composite weighted over the in-scope categories. Agents propose inputs and never write a score.
- **Policy is quoted verbatim** with a source reference, never generated.
- **Every override carries a reason**, and every state change writes an audit record in the same transaction.
- **The audit trail is hash-chained and append-only.**
- **The product owner never sees** a score, band or rating, in any response, stream or page (three independent fences).
- **Model ids live only in `ai/models/tiers.yaml`.** Amazon Bedrock only; no Opus anywhere.
- **A live model call is a human gate.** Nothing that calls Bedrock runs without the operator's go-ahead.

---

## Canonical demo

Case **FCRM-2026-0151**: consumer remittances US → Mexico through a payout partner (Remesas del Norte S.A.). Priya Nair raises it, Rahul Iyer assesses it, Ana Mendes, Joseph Okafor and Lin Chen vote. Unit-test goldens: composite 9.8 in Band 2 with a boundary flag, moving to 10.4 in Band 3 when vendor due diligence is overridden from 7.6 to 4.5, then approve with conditions 2 to 1 with three conditions. A live run prints its own numbers.

## Stack

FastAPI · FastStream on RabbitMQ · Pydantic AI on Amazon Bedrock · MongoDB 8 · MinIO · OPA · nginx · React 19 · Python 3.13 with uv · OpenTelemetry to OpenObserve.

## Where things stand

89 ADRs and 131 defect-trail rows as of 27 October 2026. The stack is built, merged and gated; the status table in [`trustops/README.md`](trustops/README.md#status) gives the measured figures and [`trustops/docs/governance/known-gaps.md`](trustops/docs/governance/known-gaps.md) what remains.
