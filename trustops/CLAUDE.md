# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

TrustOps, the Genius Hacks Q3 2026 FCRM Risk Assessment Workbench: FastAPI services, FastStream workers, Pydantic AI agents on Amazon Bedrock, MongoDB 8, RabbitMQ, React 19. Submission is 9 October 2026. The system is built and merged and runs as eight containers; `BUILD-PLAN.md` starts with "Status as built" (where the build departs from the plan and why), the three design documents under `docs/architecture/design/` hold the detail, and `docs/governance/decision-log.md` holds every decision. `RUNNING.md` is how to run it and `docs/governance/known-gaps.md` is what is not done. Only `ai/` and `src/frontend/` have their own `CLAUDE.md`; read it before editing there.

This folder is the project; the git root is one level up (`trustops_fcrm/`) and holds two siblings: `../Documents/` (the approved spec set, copied under `docs/requirements/source/`) and `../FCRM_v2/` (the BA's clickable prototype: reference only, never imported or built on). Run `git` from either level, `make` only from here.

## Precedence when sources disagree

The brief (`docs/requirements/source/Genius Hacks Q3 2026.html`) for rubric and deliverables → `docs/governance/decision-log.md` (ADR rows) → the team's decision log v1.3 and walkthrough v2 → `BUILD-PLAN.md` and the design documents → the build plan and blueprint HTML → the FCRM_v2 prototype (`../FCRM_v2`, reference only).

## Component IDs

The rules, ADRs, `ai/` files and commit messages name components by ID. Paths are under `src/backend/`; `docs/architecture/folder-structure.md` has the full tree.

| ID | Path | What it is |
|---|---|---|
| X1 | `services/identity` | JWT, JWKS, roles (clone of AEO identity; `UPSTREAM.md`) |
| S1 | `services/case` | case record, PO / analyst / internal routes, SSE, the PO fence |
| S2 | `services/scoring` | the deterministic engine and routing rules R1–R6 as data |
| S3 | `services/policy` | policy corpus, precedents, search (imports H4 as a library) |
| S4 | `services/audit` | hash-chained audit, examiner, analytics |
| S5 | `services/decision` | packs, votes, threads, outcomes (`fcrm_contracts.voting.tally`) |
| H1 | `workers/caseflow` | Caseflow: the step engine, gates, routing; calls the services over HTTP |
| H2 | `workers/agent-worker` | runs the nine agents (Pydantic AI on Bedrock) |
| H3 / H4 | `workers/ingestion-worker`, `workers/context-worker` | AEO clones: ingestion; context and vector search (`UPSTREAM.md`) |
| shared | `packages/{contracts,common,guardrails,agents,tools,prompts-loader,seed,evals}` | contracts, settings / outbox / broker, the seven guards, context packs, tools, prompt loader, the P7 seed, the eval runner |

Eight containers: `mongo`, `rabbitmq`, `minio`, `opa`, `api` (X1 and S1–S5), `harness` (H1–H4), `gateway` (nginx, `:8080`, the entry point for web and API) and `web`; profiles `obs` and `cache` add more. `api` and `harness` share the image `trustops/backend:local`, which only `api` builds; `harness` carries `pull_policy: never` so `make pull` (`--ignore-buildable`) does not try a registry for it.

## How a case moves

- A service never publishes to RabbitMQ inside a request. It writes its state and an outbox row in one Mongo transaction (`fcrm_common.outbox`), and the relay publishes the row later, at least once. Consumers must therefore tolerate redelivery.
- The RabbitMQ topology is owned by ops (`ops/rabbitmq/definitions.json`). Code connects passively and repeats the names only in each worker's `topology.py`. A new queue or binding goes in `definitions.json` first.
- H1 Caseflow consumes three queues:
  - `caseflow.events`: everything on `case.events`, bound with `#`. Unmapped event types are acknowledged as no-ops.
  - `caseflow.agent-results`: `agent.done` and `agent.failed` from H2.
  - `caseflow.steps`: its own `harness.step` work.
- The state machine is a pure reducer: `transitions.py` holds the transition table transcribed from design A §3.1, with guards tried in order. `routing.py` evaluates R1–R6 as plain functions over the pinned rules data. `engine.py` applies a transition, then chains at most one legal internal trigger; `CaseRun.dispatched_in_state` stops self-loops from re-firing.
- Work that needs HTTP is enqueued as a `harness.step` in the same transaction as the transition and runs outside it (ADR-28). Agent jobs go to H2 on `agent-worker.run` (exchange `agent.jobs`, key `agent.run`).
- H3 and H4 consume `ingestion-worker.run` and `context-worker.run`. Their `ingest.done` and `index.built` results are not published yet.

## Rules that apply everywhere

- Contracts first: every shape is imported from `fcrm_contracts`; no lane defines a local copy. A route change updates the OpenAPI export in the same commit (`make openapi`, below).
- Scores come only from `fcrm_scoring`; agents never emit a score, band or rating decision. Policy text is quoted verbatim with a `source_ref`, never generated.
- Model ids live only in `ai/models/tiers.yaml`. There is no Opus in any chain (ADR-62; Sonnet 5.5 runs the mid and strong tiers again since ADR-65); `tests/unit/ai/models/test_tier_chains.py` fails if an Opus returns, and so does a model entry or price row for one. A model change needs a verified price row, an ADR row and live eval evidence, and the committed evals baseline describes only the chain it was measured on. Prompts live only under `ai/prompts` and need a new version file, a `MANIFEST.yaml` bump and regenerated locks on change. `make check` runs `make ai-check` (`ai/_tools/validate_ai.py`, which also scans `src/`, `evals/` and `ops/` for model ids, and `compute_locks.py --check`); after a change under `ai/` regenerate the locks with `python3 ai/_tools/compute_locks.py` first. A prompt or model change carries its live eval evidence (`make evals AGENT=<a>`, `make evals-gate AGENT=<a>`) in the commit's `Gate:` trailer.
- `src/backend/packages/seed/fcrm_seed/seed.json` is generated by `make gen-seed`; never edit it by hand. `docs/architecture/openapi/api.json` is generated by `make openapi`; never edit it by hand. (`make api-gen` is not wired and no generated web client exists: the web's types are hand-written and `src/frontend/src/test/contract/paths.test.ts` ratchets its routes against the export.)
- Every state change writes an audit record through S4 and goes through the outbox in the same Mongo transaction. Human decisions carry a reason.
- **The PO fence.** The PO role never receives score, band, rating, critic, vote, pack or harness data in any response, stream or page. It has three layers that trust none of the others (ADR-32): the PO models have no such fields (`fcrm_contracts.api.case_po`, and `features/po/**` may not import score-bearing web code), S1's role filter and type-prefix blocklist (`fcrm_case/po_guard.py`), and a text scan; a unit test fails if the key list drifts from `fcrm_guardrails`. A new PO-visible field or event is a fence change: add the failing test first (`tests/integration/blackbox/test_bb_02_po_no_restricted_fields.py` is the model).
- Never read `.env*` or anything under `~/MacWorkspace/EVOQ_Applications/AEO/secrets`; compose has no blanket `env_file`. Cloned AEO code carries an `UPSTREAM.md`; fix bugs there and list them.
- Production runs two Python processes from one image (ADR-26), whose entry points are the two apps under `src/backend/apps/` (`api/fcrm_api`, `harness/fcrm_harness`): `fcrm_api.main:app` hosts X1 and S1–S5 as sub-apps mounted by URL prefix (S1, whose prefix `/api/fcrm` prefixes the rest, last), and `python -m fcrm_harness` hosts H1–H4 as `WorkerUnit`s on one broker. A service stays self-contained: `create_app(standalone=True)` for `uvicorn <pkg>.main:app` and tests, and `lifespan.register(app, settings, deps)` for the composite, which never reads the environment or opens clients. Mount sub-apps, never `include_router(prefix=...)`: OPA authorizes against the unprefixed route template, which a mounted sub-app keeps. A lifespan does not run inside a `Mount`, so the composite starts each service itself.
- **One handler per route path.** OPA rows, the OpenAPI export and the web's route test are keyed by method and path template. A second handler on the same method and path is shadowed silently. When two callers need different answers from one path, let the body pick it (S3's `POST /search`, ADR-41), and add the OPA row for the route you add (`ops/opa/data/permissions.json`; a unit test locks the OPA rows of the analyst and owner follow-up routes).
- H1 does HTTP in steps outside the consumer transaction (ADR-28), every step is idempotent by step id, and S4 and S5 writes carry an `Idempotency-Key`. A new step that calls a service must be safe to run twice.
- Commit small and often: `type(scope): summary [D-xx, ADR-nn]` with `Stage:` and `Gate:` trailers (`ai/guidance/commit-convention.md`). `make check` must be green before a commit; the git `pre-commit` and `commit-msg` hooks that `make bootstrap` installs lint, type-check and unit-test what you changed and refuse a message without a decision and a stage. Never `--no-verify`. Write the message to a file through a quoted heredoc and commit with `-F`, never `-m` with backticks (F-03). Stage with `git add -u` plus the new files you mean; never commit `.env`, `jwt_keys/` or `Hackathon.code-workspace`. Tables under `docs/governance` are append-only: the pre-commit hook rejects any change that rewrites or removes an existing `|` table row in any file there, including the rows of `known-gaps.md` and `sdlc-evidence.md` once committed, so add a row rather than edit one.
- Coding standards are `ai/guidance/coding-standards.md` (ADR-21) and are enforced by ruff, mypy and the hooks: Python 3.13, uv workspace at `src/backend/` (frontend in `src/frontend/`), Pydantic 2 with `extra="forbid"` DTOs, type hints on every signature, docstrings on public symbols, `logging` never `print`, specific exceptions only. Tests never sit beside source: `tests/unit/<src path>` and `tests/integration/<src path>` with fixtures only in `tests/conftest.py`; config never inside `src/`. TypeScript strict, no business arithmetic in the client.
- Do not state a capability in a document that does not run. A target that prints "not yet wired" is listed as such, and a gap goes in `docs/governance/known-gaps.md`.
- **A live model call is a human gate (ADR-59).** Never run anything that calls Bedrock (`make evals`, `make demo`, `make e2e`, a resilience drill that runs an agent, an api restart that re-embeds the corpus) without the operator's approval, asked first with what it is, how many calls and an estimate at the verified prices; put the rule in every subagent brief. Unit tests with fakes, the throwaway-Mongo suite and read-only requests need no approval.

## Commands

Run from this folder (the one with the `Makefile`). Python runs through the workspace at `src/backend`: use `uv run --project src/backend --all-packages ...`; a bare `uv run` from here finds no project.

```bash
make bootstrap     # opa binary, uv sync, git hooks, pull images
make env && make keys   # .env with generated secrets, the JWT keypair (never overwrite); add AWS keys to .env yourself
make up / down     # the stack: 8 containers (make up-obs adds otel-collector (OTLP/HTTP only) and OpenObserve at :5080, which holds traces, metrics, logs and the three dashboards; no Prometheus, Grafana or Jaeger (ADR-72); make up-debug publishes the infrastructure ports); make down-v also deletes the data
make seed          # reload the 80-case seed and stage the demo PDFs; make demo-reset re-runs only case 0151
make check         # commit gate: ruff, format, mypy, OPA check and 121 Rego tests, ai/ registry and model-id guards, 7,689 unit tests, env contract, web tsc and 164 vitest tests (about 6 minutes)
make test-slow     # the slow-marked unit tests only (full H2 adversarial matrix, 50-job concurrency check, H3's 500-page PDF budgets); make check skips them (FCRM_SLOW_TESTS=1 and FCRM_RUN_SLOW=1 turn them on)
make test-int      # every integration-marked test under tests/: tests/integration on Docker or FCRM_TEST_MONGO_URI, plus the *_live.py tests under tests/unit, which skip (loudly announced) without FCRM_TEST_MONGO_URI; make gate = check + test-int + eval dataset and self-check
make evals         # LIVE agent evals on Bedrock; writes evals/results/<date>/. Do not run it casually. About USD 3 and 7-11 minutes was measured on the previous model chain and not re-measured on the committed one. EVALS_ARGS="--ai-root <copy of ai/> --results-root <own folder>" tries a variant without touching the shipped ai/ and is never a baseline
make openapi       # export docs/architecture/openapi/api.json; run it after ANY route change and commit the diff
make dlq-replay q=caseflow.steps   # move <q>.dlq back onto <q>; make dlq lists the DLQs
make verify case=FCRM-2026-0118    # verify one case's audit chain through S4
make resilience    # kill/restart drills against the running stack (pytest -m resilience tests/resilience); k=<expr> selects, DISRUPTIVE=1 and AWS=1 widen it; the drills that run an agent call Bedrock (ADR-59)
make evals-full    # evals over every row of every dataset: several times the cost of make evals, so ADR-59 applies in full
make token-report # (needs make up-obs) tokens and cost per agent and model from OpenObserve
make rotate-keys   # regenerate the JWT keypair (make keys FORCE=1) and restart api
uv run --project src/backend --package fcrm-scoring-service pytest tests/unit/services/scoring -k golden   # one unit, one selection (the 9.8 to 10.4 goldens)
```

Web: `src/frontend/CLAUDE.md` has the npm commands (Node 22 must be on `PATH`, and it says how). `make web-check` is the web half of `make check`; it skips, loudly, when `src/frontend/node_modules` is missing, so run `make web-install` once per checkout or a green `make check` has not run the web tests.

Not wired, print "not yet wired": `nightly`, `contracts`, `api-gen`, `locks`, `build-usage`, `backup`, `restore`. `make demo` drives case 0151 through all 21 scenes on the live stack and `make e2e` adds the live black-box rules and the Playwright scenes; both call Bedrock and cost money, so ask the operator first (ADR-59).

`pytest.ini`'s `testpaths = tests` means an unscoped `pytest` call collects every unit's tests in one process. `--package <dist-name>` installs only that unit's dependencies, so a bare `pytest` under `--package` fails collection on another unit's imports: pass the specific `tests/unit/<...>` (or `tests/integration/...`) path with it. The distribution name is the `name` in the unit's `pyproject.toml` (`fcrm-scoring-service`, not `fcrm-scoring`). For the whole suite use `make test-unit` (`--all-packages`), which is what `make check` and `make gate` use.

## Lessons from the integration phase

- **Edit with perl, not BSD sed.** macOS `sed` has no `\b`, and `-i` needs an argument. `perl -0pi -e 's/.../.../' file` does what you meant.
- **Live tests use a throwaway Mongo, never the stack's.** Start your own replica set on a spare port, point `FCRM_TEST_MONGO_URI` at it, and the tests create and drop their own databases:

  ```bash
  docker run -d --rm --name scratch-mongo -p 127.0.0.1:28999:28999 mongo:8.0 mongod --replSet rs0 --bind_ip_all --port 28999
  docker exec scratch-mongo mongosh --quiet --port 28999 --eval 'rs.initiate({_id:"rs0",members:[{_id:0,host:"127.0.0.1:28999"}]}).ok'
  FCRM_TEST_MONGO_URI='mongodb://127.0.0.1:28999/?replicaSet=rs0' uv run --project src/backend --all-packages pytest -m integration tests/integration/live_mongo
  docker rm -f scratch-mongo
  ```

  Keep the log of a live run in a file: one full run once failed in the audit live tests and did not reproduce (D-39). Do not start a container on the operator's own Mongo or RabbitMQ; Docker Desktop can wedge a new container in `Created` (F-04).
- **After a route change, `make openapi` and commit the diff.** `test_the_committed_export_is_current` and the web's `paths.test.ts` fail otherwise (D-38). When you add a route the web calls, delete its entry from `PENDING_ROUTES` (it is empty today).
- **Merging lanes: read the merged class bodies of shared settings, not only the conflict hunks** (D-37), and give a test fake a name no other lane uses (D-45). Run `make openapi` and `make check` on the merged tree.
- **Importing `litellm` or `deepeval` loads the nearest `.env`** into the process (D-31, D-43). `LITELLM_MODE=PRODUCTION` and DeepEval's dotenv switch are set at package import and in the root `conftest.py`; keep them before any new entry point imports either.
- **A logging `extra` key must not be a `LogRecord` field** (`created`, `name`, `msg`...): it raised in the first re-flag post once INFO logging was on.
- **A healthy container is not proof that startup ran.** Verify wiring by asserting state set during startup is visible from a request (F-05).

## Canonical demo

Case FCRM-2026-0151 (US → Mexico remittances via Remesas del Norte); Priya Nair (PO), Rahul Iyer (analyst), Ana Mendes, Joseph Okafor, Lin Chen (committee); the golden figures are 9.8 Band 2 with boundary flag → override CTL-T01 7.6 → 4.5 → 10.4 Band 3 → committee → approve with conditions 2–1 → three conditions → 67 audit records. They are unit-test goldens and the static walkthrough's figures (`docs/requirements/research/06-walkthrough-seed-extraction.md`); a live run prints its own numbers (ADR-27). The case is seeded unassigned at intake with no documents; its first upload opens the run (ADR-39).
