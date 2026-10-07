# Running and checking TrustOps

How to set up the repository, start the stack, drive the demo case by hand in the web UI, run the checks and fix what goes wrong. Written on 6 October 2026 from the repository at that date, with the model chain, its budgets and its cost figures updated on 7 October 2026 (ADR-62); every command below was either run, expanded with `make -n`, or checked against the Makefile, the compose files and `docs/architecture/openapi/api.json`. What was not run is said in "What is not done and not verified".

Run every command from the folder that holds the `Makefile` (`trustops/` inside the repository). `make` anywhere else fails with `No rule to make target`.

```bash
cd ~/MacWorkspace/Hackathon/trustops_fcrm/trustops     # or wherever you cloned it
```

## 1. What you need

| Need | Notes |
|---|---|
| Docker Desktop with Compose v2, running | The default stack is 8 containers. Under `~/Downloads` macOS may block Docker's file mounts; keep the repository elsewhere (for example `~/MacWorkspace`) or grant Docker access in System Settings, Privacy and Security |
| `uv` | Installs the Python 3.13 workspace. Put it in `~/.local/bin`; the Makefile adds that to `PATH` |
| Node 22 and `npm` | Only for `make web-check`, `make web-install` and the dev server. `make up` builds the web image in Docker and needs no host Node |
| `make`, `openssl`, `python3` | The macOS system versions work (GNU make 3.81). `make keys` uses `openssl`; `make env`, `make seed` and the waiters use `python3` |
| AWS credentials with Amazon Bedrock access in the region of `AWS_DEFAULT_REGION` (default `us-east-1`) | Needed for every agent run and for the corpus embeddings that S3 does when `api` starts. There is no replay or mock mode (ADR-27) |
| Several minutes and some disk for the first build | The images are built from the repository; `make bootstrap` also pulls the infrastructure images. Disk use was not measured |

The models the stack calls are named in `ai/models/tiers.yaml` and nowhere else. Enable model access for each in the account before the first run. Labels, not ids:

| Tier | Used by | Chain (attempt 1) | Budget per attempt |
|---|---|---|---|
| cheap | screening, intake, watchlist | Haiku 4.5, then Nova Lite | 15,000 tokens, 60 s |
| mid | narrative, examiner, policy_gap | Sonnet 4.5, then Haiku 4.5 | 15,000 tokens, 120 s |
| strong | research, assessment | Sonnet 4.5, then Sonnet 4.5, then Nova Pro | 24,000 tokens, 180 s |
| judge | critic (and the eval judge) | Nova Pro, then Haiku 4.5 | 28,000 tokens, 180 s |
| embeddings | S3 policy search, H4 index builds | Titan Embed Text v2, 1024 dimensions | |

One retry per job, and per case 160,000 tokens enforced and 120,000 as the target (`ai/models/tiers.yaml`). A retry runs on the fallbacks alone, so research and assessment retry on Sonnet 4.5. There is no Opus and no Sonnet 5 in any chain (ADR-62). Details and the failure behaviour: `ops/runbooks/bedrock-outage-and-fallbacks.md`.

## 2. First run

```bash
make bootstrap     # uv sync the workspace, install the opa binary, install git hooks, pull the infrastructure images
make env           # write .env: generated local passwords, an empty AWS block (never overwrites)
make keys          # write the RS256 JWT keypair into jwt_keys/ (never overwrites)
```

Add the AWS credentials without printing them. Either open `.env` and fill `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` in section 1 (the region is pre-filled; uncomment `AWS_SESSION_TOKEN` for SSO or assumed-role keys), or export them in the shell that runs `make up`:

```bash
open -e .env
# or, for this shell only (zsh):
read -rs "?AWS_ACCESS_KEY_ID: " AWS_ACCESS_KEY_ID; echo; export AWS_ACCESS_KEY_ID
read -rs "?AWS_SECRET_ACCESS_KEY: " AWS_SECRET_ACCESS_KEY; echo; export AWS_SECRET_ACCESS_KEY
```

`AWS_PROFILE` is not passed into the containers. Never commit `.env` or `jwt_keys/` (`ops/runbooks/secrets-and-keys.md`).

```bash
make up            # build the images, start 8 containers, wait for health (up to 600 s)
make ps            # every container healthy
curl -s http://localhost:8080/healthz; echo       # {"status":"ok","service":"gateway"}
make seed          # force-reload the seed and stage the three demo PDFs in MinIO
```

| Step | What to expect |
|---|---|
| `make up`, first time | a few minutes of image builds; then the waiter prints each container as it turns healthy |
| `api` | about 85 seconds to become healthy: S3 embeds the 197 corpus clauses on Bedrock when it starts (commit `5a02c09`). Do not call it hung until `make logs svc=api` shows an error |
| `make seed` | drops and reloads every `fcrm_*` database, restarts `api` and `harness` (another ~85 s), then copies the demo PDFs to `uploads/_fixtures/`. The PO's "Use the demo's two files" button needs this |

The first start already seeds an empty database (80 cases, the 65 closed cases' nine-record audit chains, 12 users, the policy corpus). `make seed` is what stages the PDFs and gives you a clean slate again.

`make up` runs `mongo`, `rabbitmq`, `minio`, `opa`, `api` (identity and S1-S5 in one process), `harness` (H1 Caseflow, H2 agent worker, H3 ingestion, H4 context in one process), `gateway` and `web`. Only the gateway publishes a port on your machine (8080).

## 3. URLs and who you can be

| What | URL |
|---|---|
| The app (web through the gateway) | http://localhost:8080 |
| The API (same origin) | http://localhost:8080/api/fcrm/... |
| Vite dev server, hot reload against the live backend | http://localhost:5173 (`cd src/frontend && npm ci && npm run dev`) |
| Vite dev server against the in-browser mock | `npm run dev:msw` in `src/frontend`; no Docker needed |
| Grafana, Jaeger, Prometheus | http://localhost:3000, :16686, :9090 after `make up-obs`; Grafana user `admin`, password `GF_SECURITY_ADMIN_PASSWORD` from `.env` |
| RabbitMQ management, MinIO console, OPA | http://localhost:15672, :9001, :8181 after `make up-debug` |

Every seeded user shares one password, `demo-password-2026` (override with `make env DEMO_PASSWORD=...` before `.env` exists). On the login page "Sign in with corporate SSO" opens a picker of the seeded users; if identity does not serve the picker the page shows a username and password form. The username is the seed id without `u-` (`riyer` or `u-riyer` both work).

| Persona | Log in as | Home | Sees |
|---|---|---|---|
| Product owner | Priya Nair, `pnair` (also `tgold`, `skim`, `mros`, `ldia`) | `/` | her cases, documents, the questions FCRM asks her, conditions, the plain outcome. **Never** a score, band, rating, critic finding, vote or harness event; 403 on the monitor and on every analyst, scoring and decision route |
| FCRM analyst | Rahul Iyer, `riyer` (also `mshah`, `dbrooks`) | `/analyst` | the queue, the workbench for a case (summary, submission, inbox, policy, risk and controls, recommendation, handoff, activity), Assurance (ask the examiner, overrides, watchlist, agent quality), Configuration (eight sections), the harness monitor |
| Risk committee | Ana Mendes `amendes`, Joseph Okafor `jokafor`, Lin Chen `lchen` | `/committee` | the packs released to them, the pack (read-only), questions, their own vote. Other votes stay hidden until all three are cast |
| Observer | Panel Observer, `observer` | `/monitor/<case>` | the harness monitor and agent quality only; no pack, no audit records |

The demo needs five people (Priya, Rahul, Ana, Joseph, Lin) across three working roles. Services also call each other with a service account (HMAC tokens); it is not a person and cannot decide a gate (Guard 7).

## 4. Drive the demo by hand

`make demo` drives the same case through the 21 scenes by itself, as the five users through the gateway, and prints the live numbers; it calls Bedrock (100k to 160k tokens), so ask the operator first (ADR-59). What follows is the same flow by hand.

The case is **FCRM-2026-0151**: consumer remittances US to Mexico through a payout partner (Remesas del Norte S.A.). It is seeded as Priya's draft at intake, unassigned, with no documents. You play all five people. Use a different browser profile or a private window per person, or sign out between steps.

Read this first:

- The agents are live. The proposals, the completeness questions, the numbers and the wording differ from run to run. This guide describes structure: which screen, which action, what appears. The golden figures (9.8 to 10.4, committee 2-1, 67 audit records) are proved by the unit tests and belong to the static walkthrough; a live run prints its own numbers (ADR-27). Do not expect to see exactly 9.8 or 67.
- Nothing is approved or rejected automatically. At every step the next move is a person's.
- Leave a second window on `/monitor/FCRM-2026-0151` (as Rahul) to watch agents, guards, audit records and tokens as they happen.
- To start over: `make demo-reset` (deletes only this case's data).
- Do not press "New case" on the dashboard for the canonical run: it mints a different case id. Open 0151 from My queue.

The 21 scenes are the walkthrough's (`docs/requirements/research/06-walkthrough-seed-extraction.md`, section 3.10 lists the audit records each writes). The flow behind them is in `docs/architecture/sequences/`.

| # | Scene | As | Where | Do | Expect |
|---|---|---|---|---|---|
| 1 | Sign in | Priya | `/login`, then `/` | sign in | the dashboard: active cases, awaiting my answer, average days, open conditions, My cases. No risk number anywhere |
| 2 | New case | Priya | `/queue`, then `/case/FCRM-2026-0151` | open the seeded draft | the case id, stage Intake, tabs Overview, Documents, Review, Inbox, Activity, Conditions. Nothing is on record until the first write; the first upload opens the run (`case.created`, `workflow.started`) |
| 3 | Upload | Priya | `/case/FCRM-2026-0151/documents` | click "Use the demo's two files" (or Browse your own PDFs) | both files go Stored, Scanned, Read; the Assistant card narrates. H3 converts to Markdown with page:line anchors after Guard 1 (injection scan, PII masking); H2 runs the intake agent once per document (cheap tier). Tens of seconds, not instant. A scanned PDF is refused (no OCR) |
| 4 | Proposals | Priya | `/case/.../overview` | Confirm or Remove each proposed change type (a removal needs a reason); read the "Values found" with their source chips | proposed change types (the demo expects vendor onboarding, new geography, new product) and the values each with a page and line. Confirming the last type resolves the field set; the case shows how many fields are required and how many are filled |
| 5 | Reuse | Priya | same page, "A similar case you can reuse from" | tick values, "Reuse" | a precedent from S3's search (the demo expects FCRM-2026-0118); reused values carry provenance "reused" |
| 6 | Gaps | Priya | same page, "Questions for you" | answer the blocking questions, save | blocking gaps are asked, optional ones are not; a fact such as a cash-pickup channel is flagged |
| 7 | Review and submit | Priya | `/case/.../review` | correct a value if you like (the original is kept), then Submit | four plain checks (completion at least 80%, always-required fields, change types confirmed, no unresolved conflicts), then "Submitted". Triage assigns the analyst with the fewest open cases (R5): Rahul. Priya sees only the stage |
| 8 | Analyst queue | Rahul | `/analyst/queue` | open 0151 | it is in My queue; screening has run and proposes the fast lane or not (R4; you confirm). Research and assessment run next; the Harness button and `/monitor/FCRM-2026-0151` show agents, guards and tokens |
| 9 | Summary | Rahul | `/analyst/case/FCRM-2026-0151/summary` | read | "The assessment core is running" until it finishes; then the critic's findings (a banner) and the completeness report (missing, stale, contradictory). **The score card reads "Score withheld"**: a live score is shown only after the case's own analyst opens Risk and controls (ADR-45). Another analyst never sees it |
| 10 | Clarify | Rahul | Summary, then `/analyst/case/.../inbox` | "Ask now", then keep, drop or edit each drafted question, send | one round only; the analyst clock pauses; the submission stays untouched |
| 11 | PO answers | Priya | `/case/.../inbox` | answer the questions, optionally attach the vendor due-diligence scope letter | answers arrive as a new submission version; research and assessment re-run only for the changed fields; the case returns to Rahul in review |
| 12 | Policy | Rahul | `/analyst/case/.../policy` | decide each clause: accept, or remove with a written reason of at least 20 characters; record the gap in the corpus | every clause is quoted word for word with its source reference. One decision per item: there is no accept-all. Decisions need the case in the review stage (after the clarification round); earlier, the API answers 409 `wrong_stage` (ADR-44) |
| 13 | Controls | Rahul | `/analyst/case/.../risk-controls` | open the tab (this starts the review and unmasks the score), confirm each control rating item by item; optionally add a planned control | the score appears: composite, band, boundary flag when within 0.5 of an edge, with the derivation per area. The controls matched per area show rating and evidence date (older than 24 months is flagged stale); a planned control is shown and never scored. If a decision fails to resolve its item, that is open defect D-53 |
| 14 | Override | Rahul | same tab | override the vendor due-diligence control (CTL-T01) to a lower rating, with a written reason of at least 20 characters | S2 recomputes. If the band changes or a boundary flag rises, a banner says "Acknowledgement required": acknowledge it in the workbench header (R3). The override is a `human.override` record with before, after and your reason. Overriding to the value the agent proposed is refused (422) |
| 15 | Recommend | Rahul | `/analyst/case/.../recommendation` | read the narrative agent's draft, edit, Lock | lock is refused until every proposal and acknowledgement is done. Locking freezes the numbers |
| 16 | Handoff | Rahul | `/analyst/case/.../handoff` | check the four release checks, release to the committee | the pack is assembled by code from the locked numbers and pinned versions; no model. Release goes to three members; votes are hidden |
| 17 | Committee asks | Ana, then Rahul | `/committee`, `/committee/case/.../pack`, `.../inbox`; Rahul on Handoff | Ana reads the 12-section pack and asks a question; Rahul answers | a thread visible to all members; the answer is attached to the pack as an addendum. If the answer says a number changed, the pack unlocks, a pack v2 is built and every vote is requested again (R6) |
| 18 | Vote | Joseph, Ana, Lin | `/committee/case/.../vote` | each casts one vote with a rationale of at least 20 characters (the demo: two approve with conditions, one defer) | "n of 3 votes in"; nobody sees another's vote; on the third vote all are revealed together and S5 tallies. Two identical votes win; a 1-1-1 split is `no_majority`, opens a thread and a second round; nothing is decided automatically (ADR-31, ADR-36) |
| 19 | Outcome | Priya, Rahul | `/case/.../conditions`; the workbench | read | the committee outcome is recorded, the drafted conditions become tracked conditions, the case closes, and Priya sees one plain message ("Your change was decided ...") with her conditions. H1 then verifies the audit chain |
| 20 | Examiner | Rahul | `/analyst/assurance/ask`; also `.../overrides`, `.../watchlist`, `.../agent-quality` | ask why the case went to committee | an answer in which each sentence cites an audit record that exists, or a refusal. The question can take up to 60 s (see the 202 gap). The watchlist tab lists re-flags after a new FATF list is activated under Configuration |
| 21 | Recap | Rahul | `/monitor/FCRM-2026-0151` | read | counters: agents run, guards passed and blocked, audit records, chain verified, tokens against the case cap (160k enforced, 120k target) |

Check the result without the UI (tokens as in section 6):

```bash
curl -s -H "Authorization: Bearer $A" $G/api/fcrm/audit/verify/FCRM-2026-0151; echo   # {"ok":true,"record_count":N,...}
curl -s -H "Authorization: Bearer $A" $G/api/fcrm/cases/FCRM-2026-0151/harness | head -c 600; echo
```

If a run stops, check `make dlq` and `make logs svc=harness`, then `ops/runbooks/dlq-replay.md`. If an agent fails twice the run moves to `handoff`: the analyst completes the step by hand on the Summary tab (the proposals as JSON and a reason), and the run resumes (`docs/architecture/sequences/handoff.md`).

## 5. Make targets

"Stack" means `make up` must be running. "Bedrock" means real model calls and real money. Times are what was measured or documented on 6 October 2026 (warm caches, Apple silicon); "not timed" means no measurement exists.

| Target | What it does | Stack | Bedrock | Time |
|---|---|---|---|---|
| `help` | list targets | no | no | instant |
| `bootstrap` | `tools`, `uv sync`, install git hooks, `pull` | no | no | minutes the first time (not timed) |
| `tools` | install the pinned OPA 1.15.2 into `~/.local/bin` if missing | no | no | seconds |
| `env` | write `.env` from `.env.example` with generated secrets; never overwrites | no | no | seconds |
| `keys` | write the JWT keypair into `jwt_keys/`; `FORCE=1` rotates | no | no | instant |
| `pull` | pull the pinned images | no | no | minutes the first time |
| `up-infra` | mongo, rabbitmq, minio, opa only | starts it | no | about a minute |
| `up` | build and start the 8-container default stack | starts it | the corpus embedding at `api` start | build a few minutes, `api` about 85 s |
| `up-obs` | `up` plus otel-collector, Jaeger, Prometheus, Grafana (12 containers) | starts it | as `up` | as `up` |
| `up-cache` | `up` plus Redis. **Nothing uses Redis today** (the SSE fan-out is not implemented) | starts it | as `up` | as `up` |
| `up-debug` | `up` with infrastructure and harness health ports published to the host | starts it | as `up` | as `up` |
| `up-dev` | bind-mounted source, `uvicorn --reload`, the Vite dev server | starts it | as `up` | as `up` |
| `down` | stop every profile, keep volumes | yes | no | seconds |
| `down-v` | stop and delete every volume (all local data) | yes | no | seconds |
| `reset` | `down-v` then `up` | yes | as `up` | as `up` |
| `ps` | container status and health | yes | no | instant |
| `logs svc=<name>` | follow logs (all when `svc` empty) | yes | no | until Ctrl-C |
| `gen-seed` | regenerate `seed.json` and assert it is byte-stable | no | no | seconds (not timed) |
| `seed` | reload the seed, restart `api` and `harness`, stage the demo PDFs; refuses `ENV=prod` | yes | corpus embedding on restart | not timed as a whole; includes one `api` restart (about 85 s) |
| `demo-reset` | delete case 0151's documents, uploads, audit records and run | yes | no | seconds |
| `check` | the commit gate: ruff, format, mypy, OPA check and tests, `ai-check`, unit tests, env contract, web typecheck and vitest | no (needs Docker for `compose config`) | no | 355 s measured (unit tests 288 s) |
| `lint`, `format-check`, `typecheck` | ruff, ruff format, mypy over every package | no | no | under 2 s warm |
| `opa-check`, `opa-test` | Rego check and 120 Rego tests | no | no | 0.1 s, 12 s |
| `test-unit`, `test` | the 7689 unit tests (`-m "not integration and not e2e"`) | no | no | about 290 s |
| `compose-check` | `.env.example` against the compose files, `compose config` of each file set | no (needs Docker) | no | 1.4 s |
| `web-install` | `npm ci` in `src/frontend` | no | no | not timed |
| `web-check` | web typecheck and 164 vitest tests | no | no | 17 s |
| `gate` | `check`, `test-int`, `fcrm_evals build --check`, `selfcheck` | needs Docker | no | more than `check` (not timed) |
| `test-int`, `test-integration` | every integration-marked test under `tests/` (`pytest -m integration tests`): the 464 under `tests/integration` on testcontainers or on `FCRM_TEST_MONGO_URI`, and the 74 `*_live.py` tests under `tests/unit`, which need `FCRM_TEST_MONGO_URI` and skip without it (the target says so) | needs Docker, not the stack | no | not timed through make; all of them took 192 to 225 s on a throwaway replica set |
| `ai-check` | `ai/_tools/validate_ai.py` (registry, tool permits, OPA decisions, and the rule that no model id appears outside `ai/models/tiers.yaml` in `ai/`, `src/`, `evals/` or `ops/`) and `compute_locks.py --check` | no | no | under 10 s |
| `evals` | live agent evals, 5 rows per agent, Bedrock judge; writes `evals/results/<date>/`. `EVALS_ARGS="--ai-root <copy of ai/> --results-root <own folder>"` tries a variant without touching the shipped `ai/`; its result is never a baseline | no | **yes**, about USD 3 measured on the previous chain, not re-measured on the committed one | 7 to 11 minutes on the previous chain |
| `evals-full` | every row of every dataset | no | **yes**, several times `evals` | slow |
| `evals-gate AGENT=<a>` | the promotion gate for one agent on the newest run | no | no | seconds |
| `openapi` | export the merged OpenAPI to `docs/architecture/openapi/api.json` | no | no | seconds |
| `dlq` | list the dead-letter queues with depth | yes | no | instant |
| `dlq-inspect q=<queue>` | show what is parked in `<queue>.dlq` (type, error, attempt, preview) | yes | no | seconds |
| `dlq-replay q=<queue>` | move `<queue>.dlq` back onto `<queue>` (`ops/scripts/dlq.py`) | yes | no | seconds |
| `dlq-purge q=<queue> CONFIRM=1` | delete the parked messages of `<queue>.dlq` (refused without `CONFIRM=1`) | yes | no | seconds |
| `token-report` | per-agent tokens and cost from Prometheus into `ops/token-analysis/runtime/` | needs `up-obs` | no | seconds |
| `rotate-keys` | `keys FORCE=1`, then restart `api`; everyone signs in again | yes | no | the `api` restart, about 85 s |
| `seed-verify` | the live stack holds the seed: 80 cases, the stage distribution, the demo case untouched, a chain that verifies | yes | no | seconds |
| `verify case=<id>` | verify one case's audit chain through S4 | yes | no | seconds |
| `demo` | `demo-reset`, then the 21 scenes of case 0151 through the gateway as the five users, printing the live numbers | yes | **yes**: 100k to 160k tokens, about USD 0.6 to 1.0, measured on the previous chain and not re-measured on the committed one, where Sonnet 4.5 costs about 1.5 times Sonnet 5.5 per token (ask first, ADR-59) | 4 to 5 minutes on the previous chain |
| `e2e` | `demo`, then the live black-box rules (BB-03, 06, 08, 09, 13, 14) and the Playwright live scenes | yes | **yes**: the demo's | the demo plus a few minutes |
| `resilience` | kill and restart drills; the ones that restart a container need `DISRUPTIVE=1`, the ones that call AWS or restart the api need `AWS=1` (`docs/architecture/resilience.md`) | yes | with `AWS=1`: 3 to 5 intake calls (Haiku, under USD 0.10) and api restarts (Titan embeddings, cents) | about 15 to 18 minutes for all twelve |
| `nightly`, `contracts`, `api-gen`, `locks`, `build-usage`, `backup`, `restore` | **not wired**: print "not yet wired" and exit 1 | | | |

`make -n <target>` prints what a target would run without running it.

## 6. Verify with curl, through the gateway

```bash
G=http://localhost:8080
login() {
  curl -s -X POST $G/api/fcrm/auth/login -H 'content-type: application/json' \
    -d "{\"username\":\"$1\",\"password\":\"demo-password-2026\"}" |
  python3 -c 'import sys, json; print(json.load(sys.stdin)["access_token"])'
}
A=$(login riyer)      # Rahul Iyer, analyst
P=$(login pnair)      # Priya Nair, product owner
C=$(login amendes)    # Ana Mendes, committee
```

The login is limited to 10 requests a minute (the gateway and identity both). Log in once per persona and reuse the token; an access token lasts 15 minutes.

| Check | Command | Expect |
|---|---|---|
| Gateway is up | `curl -s $G/healthz` | `{"status":"ok","service":"gateway"}` |
| Who am I | `curl -s -H "Authorization: Bearer $A" $G/api/fcrm/auth/me` | `200`, the analyst |
| The analyst reads the active scoring model | `curl -s -H "Authorization: Bearer $A" $G/api/fcrm/scoring/models/active` | `200` with the model JSON |
| The analyst's queue | `curl -s -H "Authorization: Bearer $A" $G/api/fcrm/queue` | `200`; 0151 appears once triage assigned it |
| A seeded closed case's chain verifies | `curl -s -H "Authorization: Bearer $A" $G/api/fcrm/audit/verify/FCRM-2026-0118` | `200`, `ok: true`, `record_count` 9 |
| The committee reads the trail | `curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $C" $G/api/fcrm/audit/records/FCRM-2026-0118` | `200` |
| The committee queue | `curl -s -H "Authorization: Bearer $C" $G/api/fcrm/decision/committee/queue` | `200`; empty until a pack is released |
| The PO never sees scores | `curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $P" $G/api/fcrm/scoring/models/active` | `403` |
| The PO has no monitor | `curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $P" $G/api/fcrm/cases/FCRM-2026-0151/harness` | `403` |
| No token | `curl -s -o /dev/null -w '%{http_code}\n' $G/api/fcrm/scoring/models/active` | `401` |
| Nobody but a service account writes audit records | `curl -s -o /dev/null -w '%{http_code}\n' -X POST -H "Authorization: Bearer $A" -H 'content-type: application/json' -d '{}' $G/api/fcrm/audit/records` | `403` |
| `/internal/` is not reachable from outside | `curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $A" $G/api/fcrm/policy/internal/clauses` | `404` (the gateway hides it) |

Every route is in `docs/architecture/openapi/api.json` (112 paths); the roles are in `ops/opa/data/permissions.json`. Each route in the table above, and the roles allowed on it, was checked against both; the responses need the live stack.

## 7. Check the code (no stack)

```bash
make check         # about 8 minutes: ruff, format, mypy over 618 files, OPA, 7689 unit tests, env contract, web typecheck and 164 vitest tests
make test-int      # every integration-marked test: tests/integration on testcontainers (needs Docker), the *_live.py tests under tests/unit when FCRM_TEST_MONGO_URI is set
make gate          # check, test-int, datasets and eval self-check
```

To point the integration tests at a Mongo replica set you started yourself (never the stack's Mongo; the tests create and drop their own databases):

```bash
docker run -d --rm --name scratch-mongo -p 127.0.0.1:28999:28999 mongo:8.0 mongod --replSet rs0 --bind_ip_all --port 28999
docker exec scratch-mongo mongosh --quiet --port 28999 --eval 'rs.initiate({_id:"rs0",members:[{_id:0,host:"127.0.0.1:28999"}]}).ok'
FCRM_TEST_MONGO_URI='mongodb://127.0.0.1:28999/?replicaSet=rs0' make test-int
# with the variable set, make test-int also runs the 74 *_live.py tests under tests/unit; one path on its own:
FCRM_TEST_MONGO_URI='mongodb://127.0.0.1:28999/?replicaSet=rs0' uv run --project src/backend --all-packages pytest -m integration tests/unit/workers/caseflow
docker rm -f scratch-mongo
```

On 6 October the whole integration-marked set (464 plus 74) run this way, with no stack, gave 525 passed and 14 skipped in 192 to 225 s. The skips: 7 need `FCRM_TEST_RABBITMQ_URL`, 6 are named black-box skips, 1 is a missing real-document fixture.

Web checks on their own: `cd src/frontend && npm ci && npm test && npm run typecheck && npm run lint`. Playwright scenes against the mock: `npm run test:e2e` (4 specs, not run by the lane that wrote this file).

On 6 October 2026 `make check` was green on the tree of `8b55edd`: 7689 passed, 4 skipped, 590 deselected (integration, e2e and resilience marks), 121 of 121 Rego tests, 164 web tests.

## 8. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `make: *** No rule to make target 'bootstrap'` | You are not in the folder with the `Makefile` |
| `429 Too Many Requests` on login | the login limit is 10 a minute per address, at the gateway and in identity. Wait a minute; reuse tokens |
| `make up` seems stuck on `api` | it takes about 85 s: S3 embeds 197 clauses on Bedrock. `make logs svc=api` shows progress or an error. With no or expired AWS credentials expect a failure there |
| Everything stalls after the laptop slept | Docker paused the VM; in-flight Bedrock calls timed out. On the build machine, closing the lid on battery stalled every background job and looked like an API fault (D-50): keep it plugged in with the lid open, or `sudo pmset -a disablesleep 1`, and check `pmset -g log` for sleep events first. Then `make ps`, `make dlq`; replay what is parked (`make dlq-replay q=<queue>`) or `docker compose restart harness` |
| A case stops moving | `make dlq` and `make logs svc=harness`; a parked `caseflow.steps` message freezes a run until fixed and replayed (`ops/runbooks/dlq-replay.md`). List every queue with `docker compose exec -T rabbitmq rabbitmqctl -q list_queues name messages consumers` |
| The case shows "handed to a person" | an agent failed twice and the run is in `handoff`: open the Summary tab and use "Complete by hand" (the form shows what the agent was given). Check Bedrock (`ops/runbooks/bedrock-outage-and-fallbacks.md`); a manual output's claims are not matched to their sources |
| Every API call returns `503` | OPA is unreachable and the services fail closed. `make ps`, `make logs svc=opa` |
| Agent runs fail with a credentials or region error | AWS keys missing or expired in `.env` (or the shell), or no model access. `docker compose up -d` after fixing |
| I need to run the demo again | `make demo-reset` (this case only); `make seed` (everything); `make down-v` then `make up` (everything, including volumes) |
| `port is already allocated` | another stack holds the port. Set the matching `*_HOST_PORT` in `.env` (for example `GATEWAY_HOST_PORT=9080`) |
| `mkdir /host_mnt/Users/<you>/Downloads: operation not permitted` | macOS blocks Docker's access to `~/Downloads`: grant it, or move the repository |
| `audit` restarts with `ChainVerifyFailedError` | the Mongo volume holds audit records written under an older hash format (D-29). `make down-v` and start again |
| `api` fails asking for `DEMO_USERS_PASSWORD_HASH` | `.env` predates D-25: move it aside and run `make env` |
| `up` stops naming `MONGO_EVALS_READER_PASSWORD` | an older `.env`: add the line `MONGO_EVALS_READER_PASSWORD=` followed by any URL-safe string (`openssl rand -hex 16`); no `down-v` needed |
| The login page shows a password form, not the picker | identity serves the picker only when `DEMO_USERS_ENABLED=true` and `ENV` is `local`, `test` or `ci`. Use `riyer` and the demo password |
| Dev server says `Port 5173 is already in use` | an earlier `npm run dev` survives: `kill $(lsof -tnP -iTCP:5173 -sTCP:LISTEN)` |
| Every screen errors on the dev server | `npm run dev` is the live mode and proxies to :8080: `make up` first, or use `npm run dev:msw` |
| `ports are not available ... 6379` | a Homebrew `redis-server` holds the port (only with `up-cache` and `up-debug`): `brew services stop redis` or set `REDIS_HOST_PORT` |
| The examiner question errors after a minute | the answer took longer than 60 s and the API answered 202; the web does not poll for it (known gap). Read it with `GET /api/fcrm/audit/examiner/answers/<question_id>` |
| A stray `make down-v` deleted my data | there is no backup unless you made one (`ops/runbooks/backup-restore.md`); `make up` reseeds the 80 cases |

More: `ops/runbooks/README.md` indexes one short page per task (start, stop, reset, dead letters, restarts, outages, secrets, backups, audit chain, agent incidents, release).

## 9. Cost and tokens

- **Per agent run:** tokens per attempt are capped by tier: 15,000 (cheap), 15,000 (mid), 24,000 (strong), 28,000 (judge), with one retry; a run that needs more is blocked and handed to a person, never silently cut (`ai/models/tiers.yaml`).
- **Per case:** 160,000 tokens enforced by H1's ledger and 120,000 as the target (ADR-50). One measured run of the demo case's eleven agent jobs used 114,626 tokens (about USD 0.50), 91,000 when every job answered first time, before `research.system` v3 (`evals/README.md`); the last complete live run used 157,345 tokens. Both were measured on the previous model chain (Sonnet 5.5 and Opus 5.5) and not repeated on the committed one. The static walkthrough's "16.9k" is a script, not a measurement.
- **An eval run:** `make evals` was about USD 3 and 7 to 11 minutes on the previous chain (USD 2.86 on 6 October: agents 0.56, judge 2.30); it has not been re-measured on the committed chain. `make evals-full` is several times that. The judge was 80% of the cost with Opus 5.5 as the judge (it is Nova Pro now, so the share is not measured); `EVALS_ARGS="--judge fake"` skips it and says FAKE on every output.
- **Hand-driving the demo** costs roughly the agents' share of one case, plus the corpus embedding at each `api` start. The prices in `ai/models/tiers.yaml` are verified AWS list prices (AWS Price List API, us-east-1, 6 Oct 2026, the Sonnet 4.5 row 7 Oct 2026; each row names its source); confirm them against the AWS bill.
- Full analysis: `ops/reports/token-analysis.md`.

## 10. What is not done and not verified

The full list, each item with its evidence, is `docs/governance/known-gaps.md`. The ones that change what you can do:

- `make nightly`, `make contracts`, `make api-gen`, `make locks`, `make build-usage`, `make backup` and `make restore` print "not yet wired". `make demo`, `make e2e`, `make seed-verify`, `make verify` and `make resilience` are wired; the golden numbers (9.8 to 10.4, 2-1, 67 records) are unit tests, and a live run prints its own.
- **No complete live acceptance pass on the committed chain.** The last 21-scene pass is 21 of 21 on the previous chain (Sonnet 5.5 and Opus 5.5) before the clean-up merges. Two live runs on the chains that are now committed (they ran as the economy profile) stopped early: at scene 9, where assessment kept 3 of 19 claims after Guard 4 so the case had no controls, and at scene 8, where the critic on Nova Pro passed its 20,000 budget (now 28,000). Runs on Haiku and Nova only stopped at scene 3 and scene 8. The prompts and thresholds were measured on Sonnet 5.5 and Opus 5.5 (D-95).
- **A manual output (a run completed from `handoff`) is less machine-verified than an agent's:** its claims are not matched against their sources; the record says so.
- **S1 pins the versions S3 holds in force** (`GET /internal/config/pins`, cached 5 s) and reads a published field set from S3; when S3 does not answer in a few seconds it pins the seed's versions and logs a warning naming the case. Proved on a real replica set, not yet in the composed stack.
- **The per-case token cap is enforced across jobs by a ledger: 160,000 enforced, 120,000 target** (ADR-50); a run that needs retries can use most of the cap.
- **Redis does nothing** (`make up-cache`); **S3 Vectors is not in the image** (vectors are in memory; each `api` start embeds the corpus).
- **The web examiner does not handle a 202.**
- **Two integration mismatches are open defects:** the `evidence_store` agent tool reads a different shape than S1 answers (D-52), and the web's decide call for a clause or control may not resolve the item id (D-53). If a review decision errors on the first item, that is D-53.
- The black-box tests under `tests/integration` run in process. The live ones (`make e2e`) read a completed demo, and the twelve resilience drills have run live (`docs/architecture/resilience.md`); neither has been run on the committed model chain (D-95, D-100).
- **The assessment agent fails its eval gate** (`li_within_one` 0.70 against 0.85); eight of nine agents pass (`evals/results/2026-10-06/gate.json`). That run was on the previous chain, and there is no committed eval baseline for the committed chain: a run of one row per agent on it gave 7 of 9 gates at about USD 0.44 and is not a baseline.
- **The policy corpus and demo documents are synthetic**, 193 of 197 clauses generated (ADR-29). No OCR, no SharePoint.
- **Identity is a demo directory** with one shared password and one JWT key; no TLS; single-node Mongo and RabbitMQ.
- The AWS path (`ops/aws/README.md`) is a design; nothing was deployed.

## Appendix: the compose files and the `.env`

| File | Purpose |
|---|---|
| `docker-compose.yaml` | the whole stack; profile `obs` adds the four observability containers, profile `cache` adds Redis |
| `compose.debug.yaml` | publishes Mongo (:27017), RabbitMQ (:5672, :15672), MinIO (:9000, :9001), OPA (:8181), Redis and the harness health port (:9100) to the host. `make up-debug` |
| `compose.dev.yaml` | bind mounts, `uvicorn --reload`, the Vite dev server, DEBUG logs. `make up-dev` |
| `compose.test.yaml` | the test stack: in-memory vectors, dummy embeddings for `api`, AWS keys blanked for `api`; project `trustops-test`. Agents still call live Bedrock. Used by the unwired `make e2e` |

`.env` has three sections: (1) the AWS block (the only place a person must add anything), (2) optional host ports, commented out, (3) generated passwords and secrets. `.env.example` is the full reference for every variable, and `make compose-check` keeps it equal to the compose files. A host port is the number on your machine for a container; by default only the gateway publishes one. A generated password cannot change after the first start without `make down-v`, except `MONGO_APP_PASSWORD`, `MONGO_AUDIT_WRITER_PASSWORD` and `MONGO_EVALS_READER_PASSWORD` (`ops/runbooks/secrets-and-keys.md`).
