# Running TrustOps

TrustOps checks the financial-crime risk of a change at a bank (a new product, a new country, a new supplier) before the change goes live. These steps start it on your own computer and open it in a browser. They work the same on macOS, Linux and Windows (through WSL 2). The detailed reference for engineers starts at "Reference for engineers" further down.

## Quick start

**What you need, once**

1. **Docker with Compose v2**, installed and running: Docker Desktop on macOS or Windows, or Docker Engine on Linux.
2. **git, make, openssl, python3 and curl.** macOS has them once the command-line tools are installed (`xcode-select --install`). On Ubuntu, including Ubuntu in WSL: `sudo apt install git make openssl python3 curl`.
3. **uv**, a small tool that installs the Python parts: `curl -LsSf https://astral.sh/uv/install.sh | sh` puts it in `~/.local/bin`.
4. **Amazon AWS keys** that are allowed to use Amazon Bedrock (the AI service). Everything the AI does goes through it and costs money. See "What costs money" below.

**Notes for your system**

- **macOS:** keep the project folder outside `~/Downloads`. Docker may refuse to read files from there.
- **Windows:** use WSL 2 with Ubuntu, and run every command below in the Ubuntu terminal, not in PowerShell or Command Prompt. Clone the project inside your Linux home folder (for example `~/work`), not under `/mnt/c/`: it is much faster there, and the files keep the line endings the scripts need. Docker Desktop with WSL integration turned on for your Ubuntu, or Docker Engine installed inside WSL, both work. Your Windows browser reaches the app at the same `localhost` address.
- **Linux:** add yourself to the `docker` group once (`sudo usermod -aG docker $USER`, then sign out and back in), so `docker` and `make` work without `sudo`.

**Steps**

1. Open a terminal, get the code and go to the project folder, the one that contains a file called `Makefile`:
   ```bash
   git clone https://github.com/abhiMyridius/trustops_fcrm.git
   cd trustops_fcrm/trustops
   ```
   If you already have a copy, just `cd` into its `trustops` folder.
2. Set up. This is needed the first time only:
   ```bash
   make bootstrap    # installs the tools and downloads the helper programs
   make env          # creates the settings file .env with made-up local passwords
   make keys         # creates the sign-in keys
   ```
3. Add your AWS keys. Open `.env` in any text editor (for example `nano .env`, or `code .env` for VS Code), fill in the two lines `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`, and save. If your keys are temporary (AWS SSO or an assumed role), also fill `AWS_SESSION_TOKEN`. Never share this file and never commit it to git.
4. Start the program:
   ```bash
   make up
   ```
   The first time takes several minutes while it builds. It then waits about a minute and a half more while the AI search prepares itself. When it prints `ready`, it is running. `make ps` should show every line as healthy.
5. Load the sample data and the demo documents (about another minute and a half):
   ```bash
   make seed
   ```
6. Open http://localhost:8080 in your browser and sign in. Every sample user has the same password, `demo-password-2026`:

   | Who | User name | What they do |
   |---|---|---|
   | Priya Nair | `pnair` | the product owner who raises the change |
   | Rahul Iyer | `riyer` | the analyst who reviews it |
   | Ana Mendes, Joseph Okafor, Lin Chen | `amendes`, `jokafor`, `lchen` | the risk committee that votes |

7. Try the example case, FCRM-2026-0151 (money transfers from the US to Mexico). Either follow "Drive the demo by hand" below, or let a script play all five people with `make demo`. The script uses the AI and costs money, so only run it when you mean to.
8. Stop when you are done:
   ```bash
   make down         # stops everything and keeps your data
   make down-v       # stops everything and deletes your data
   ```

**If something goes wrong**

| What you see | What to do |
|---|---|
| `No rule to make target` | You are in the wrong folder. Go to the folder that contains the `Makefile` (step 1). |
| `Cannot connect to the Docker daemon` | Docker is not running. Start Docker Desktop, or on Linux and WSL run `sudo service docker start`. |
| `permission denied while trying to connect to the Docker daemon socket` | Linux: you are not in the `docker` group yet (see "Notes for your system"). |
| It seems stuck while starting | It is probably still preparing. Wait two minutes and run `make ps`. If a line stays unhealthy, run `make logs svc=api` and read the last lines. |
| `api` is unhealthy and its log says `Permission denied: '/keys/jwt.pem'` | Linux and WSL: the sign-in key was made before 8 October 2026 and only you could read it. Run `make keys` (it now fixes the permissions) and then `make up` again. |
| `$'\r': command not found` or `bad interpreter` | Windows changed the line endings of the scripts. Clone the project again inside WSL (see "Notes for your system"). |
| Errors about AWS keys, region or models | Check the two key lines in `.env`, and that your AWS account allows the AI models listed below. Then run `make up` again. |
| A case stops moving | Run `make dlq`. If it lists a waiting message, the table "Troubleshooting" further down says how to release it. |
| You want a clean start | `make demo-reset` resets only the example case. `make seed` reloads all sample data. |

**What costs money**

- Starting the program costs under one cent each time, because it prepares the AI search.
- Running the demo (by script or by hand) is estimated at about 50 cents for one complete case, from the first upload to the committee outcome: USD 0.47 (between 0.41 and 0.59, depending on how the tokens split between input and output), USD 0.34 if nothing is retried, and about USD 0.50 at the 160,000-token limit. This is worked out from the token counts of the last complete run at the current prices. It has not been measured on the current models.
- `make evals` (the AI quality test) is projected at about one dollar on the current models, and has not been run on them. It cost USD 3.14 on 6 October, when an Opus model graded the answers.
- `make check`, the code checks, is free.

**Look at the database (optional)**

MongoDB is open on your own machine only, at port 27017, as soon as `make up` has finished (on Windows, Compass running in Windows reaches it at the same address). In MongoDB Compass, print your connection line first:

```bash
set -a; . ./.env; set +a
echo "mongodb://$MONGO_INITDB_ROOT_USERNAME:$MONGO_INITDB_ROOT_PASSWORD@127.0.0.1:27017/?authSource=admin&directConnection=true"
```

Paste the printed line into Compass as the connection string. The cases are in the database `fcrm_case`, collection `cases`. If Compass says the port is taken, another Mongo is already using 27017: add `MONGO_HOST_PORT=27018` to `.env`, run `make up` again and use 27018 in the line above.

---

## Reference for engineers

Everything below is detailed reference: the exact models, every command, the 21 demo scenes, troubleshooting and the file layout. Terms such as ADR and D-number point to the decision log and the defect list in `docs/governance`.


How to set up the repository, start the stack, drive the demo case by hand in the web UI, run the checks and fix what goes wrong. Written on 6 October 2026 from the repository at that date, with the model chain, its budgets and its cost figures updated on 7 October 2026 (ADR-62, ADR-65); every command below was either run, expanded with `make -n`, or checked against the Makefile, the compose files and `docs/architecture/openapi/api.json`. What was not run is said in "What is not done and not verified".

Run every command from the folder that holds the `Makefile` (`trustops/` inside the repository). `make` anywhere else fails with `No rule to make target`.

```bash
cd trustops_fcrm/trustops     # inside your clone
```

## 1. What you need

| Need | Notes |
|---|---|
| Docker with Compose v2, running | Docker Desktop (macOS, Windows) or Docker Engine (Linux, or inside WSL 2). The default stack is 8 containers. macOS: under `~/Downloads` Docker's file mounts may be blocked; keep the repository elsewhere or grant Docker access in System Settings, Privacy and Security. Windows: work inside WSL 2 and keep the clone in the Linux file system, not under `/mnt/c/` |
| `uv` | Installs the Python 3.13 workspace (uv fetches Python 3.13 itself). `curl -LsSf https://astral.sh/uv/install.sh \| sh` puts it in `~/.local/bin`; the Makefile adds that to `PATH` |
| Node 22 and `npm` | Only for `make web-check`, `make web-install`, the dev server and the browser tests. Any install works as long as `node --version` prints 22 (for example `nvm install 22`, or your package manager). `make up` builds the web image in Docker and needs no host Node |
| `git`, `make`, `openssl`, `python3`, `curl`, `bash` | GNU make 3.81 or later (the macOS system make works; Linux and WSL ship 4.x). `make keys` uses `openssl`; `make env`, `make seed` and the waiters use `python3`; `make tools` downloads OPA with `curl` and checks it with `sha256sum` or `shasum`. Ubuntu and WSL: `sudo apt install git make openssl python3 curl` |
| AWS credentials with Amazon Bedrock access in the region of `AWS_DEFAULT_REGION` (default `us-east-1`) | Needed for every agent run and for the corpus embeddings that S3 does when `api` starts. There is no replay or mock mode (ADR-27) |
| Several minutes and some disk for the first build | The images are built from the repository; `make bootstrap` also pulls the infrastructure images. Disk use was not measured |

The models the stack calls are named in `ai/models/tiers.yaml` and nowhere else. Enable model access for each in the account before the first run. Labels, not ids:

| Tier | Used by | Chain (attempt 1) | Budget per attempt |
|---|---|---|---|
| cheap | screening, intake, watchlist | Haiku 4.5, then Nova Lite | 15,000 tokens, 60 s |
| mid | narrative, examiner, policy_gap | Sonnet 5.5, then Haiku 4.5 | 15,000 tokens, 120 s |
| strong | research, assessment | Sonnet 5.5, then Sonnet 5.5, then Nova Pro | 24,000 tokens, 180 s |
| judge | critic (and the eval judge) | Nova Pro, then Haiku 4.5 | 28,000 tokens, 180 s |
| embeddings | S3 policy search, H4 index builds | Titan Embed Text v2, 1024 dimensions | |

One retry per job, and per case 160,000 tokens enforced and 120,000 as the target (`ai/models/tiers.yaml`). A retry runs on the fallbacks alone, so research and assessment retry on Sonnet 5.5. Mid and strong send `effort` low, which Sonnet 5.5 takes and Haiku and Nova reject. There is no Opus in any chain (ADR-62, ADR-65), and a unit test fails if one returns. Details and the failure behaviour: `ops/runbooks/bedrock-outage-and-fallbacks.md`.

## 2. First run

```bash
make bootstrap     # uv sync the workspace, install the opa binary, install git hooks, pull the infrastructure images
make env           # write .env: generated local passwords, an empty AWS block (never overwrites)
make keys          # write the RS256 JWT keypair into jwt_keys/ (never overwrites; readable by the api container)
```

Add the AWS credentials without printing them. Either open `.env` in any editor (`nano .env`, `code .env`) and fill `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` in section 1 (the region is pre-filled; uncomment `AWS_SESSION_TOKEN` for SSO or assumed-role keys), or export them in the shell that runs `make up` (works in bash and zsh):

```bash
printf 'AWS_ACCESS_KEY_ID: ';     read -rs AWS_ACCESS_KEY_ID;     echo; export AWS_ACCESS_KEY_ID
printf 'AWS_SECRET_ACCESS_KEY: '; read -rs AWS_SECRET_ACCESS_KEY; echo; export AWS_SECRET_ACCESS_KEY
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

`make up` runs `mongo`, `rabbitmq`, `minio`, `opa`, `api` (identity and S1-S5 in one process), `harness` (H1 Caseflow, H2 agent worker, H3 ingestion, H4 context in one process), `gateway` and `web`. Only the gateway publishes the web app on your machine (port 8080), and MongoDB is published on 127.0.0.1:27017 for Compass and mongosh.

## 3. URLs and who you can be

| What | URL |
|---|---|
| The app (web through the gateway) | http://localhost:8080 |
| The API (same origin) | http://localhost:8080/api/fcrm/... |
| Vite dev server, hot reload against the live backend | http://localhost:5173 (`cd src/frontend && npm ci && npm run dev`) |
| Vite dev server against the in-browser mock | `npm run dev:msw` in `src/frontend`; no Docker needed |
| Dashboards | in OpenObserve (below), under **Dashboards**: "TrustOps · Agents, tokens and cost", "TrustOps · Case throughput" and "TrustOps · Guards and overrides". `make up-obs` loads them, and `make obs-dashboards` reloads them after a change to `ops/openobserve/dashboards/`. There is no Prometheus, Grafana or Jaeger (ADR-71, ADR-72) |
| OpenObserve: traces, metrics and logs | http://localhost:5080 after `make up-obs`; sign in with `ZO_ROOT_USER_EMAIL` and `ZO_ROOT_USER_PASSWORD` from `.env`. **Logs**: stream `trustops`; **Traces**: stream `trustops` (an OpenObserve that ignores the stream header for traces files them under `default`); **Metrics**: one stream per metric (`fcrm_agent_tokens_total` and so on, plus RabbitMQ's `rabbitmq_queue_messages_ready`), queried with PromQL in the Metrics tab or on the dashboards. A log line carries its `trace_id`, so you can go from a log to its trace. The same log lines are still on each container's stdout (`docker compose logs api`). To send everything to your team's own OpenObserve instead, see "Send telemetry to your own OpenObserve" below |
| MongoDB (Compass, mongosh), this machine only | `127.0.0.1:27017`, started by `make up`; use `?authSource=admin&directConnection=true` and the root user from `.env` |
| RabbitMQ management, MinIO console, OPA | http://localhost:15672, :9001, :8181 after `make up-debug` |

Every seeded user shares one password, `demo-password-2026` (override with `make env DEMO_PASSWORD=...` before `.env` exists). On the login page "Sign in with corporate SSO" opens a picker of the seeded users; if identity does not serve the picker the page shows a username and password form. The username is the seed id without `u-` (`riyer` or `u-riyer` both work).

| Persona | Log in as | Home | Sees |
|---|---|---|---|
| Product owner | Priya Nair, `pnair` (also `tgold`, `skim`, `mros`, `ldia`) | `/po` | her cases, documents, the questions FCRM asks her, conditions, the plain outcome. **Never** a score, band, rating, critic finding, vote or harness event; 403 on the monitor and on every analyst, scoring and decision route |
| FCRM analyst | Rahul Iyer, `riyer` (also `mshah`, `dbrooks`) | `/analyst` | the queue, the workbench for a case (summary, submission, inbox, policy, risk and controls, recommendation, handoff, activity), Assurance (ask the examiner, overrides, watchlist, agent quality), Configuration (eight sections), the harness monitor |
| Risk committee | Ana Mendes `amendes`, Joseph Okafor `jokafor`, Lin Chen `lchen` | `/committee` | the packs released to them, the pack (read-only), questions, their own vote. Other votes stay hidden until all three are cast |
| Observer | Panel Observer, `observer` | `/monitor/<case>` | the harness monitor only (ADR-19): it is the one screen the web gives this role, and the monitor of a case with no run says so as information. No pack, no audit records, no Assurance screens. The API's OPA rows would still answer this token on `GET /audit/agent-quality` (agent quality is not case data), but no observer page calls it |

The demo needs five people (Priya, Rahul, Ana, Joseph, Lin) across three working roles. Services also call each other with a service account (HMAC tokens); it is not a person and cannot decide a gate (Guard 7).

### Send telemetry to your own OpenObserve

`make up-obs` sends traces, metrics and logs to the bundled OpenObserve container by default. To use your team's OpenObserve instead, add these lines to `.env` and run `make up-obs` again:

```bash
OPENOBSERVE_OTLP_ENDPOINT=https://<your-openobserve-host>/api/<organisation>   # for example http://openobserve.internal:5080/api/default
OPENOBSERVE_USER=<user e-mail>
OPENOBSERVE_PASSWORD=<password>
# OPENOBSERVE_STREAM=trustops        # optional: the stream for logs and traces
```

The collector adds `/v1/traces`, `/v1/metrics` and `/v1/logs` to the address and builds the sign-in header from the user and password. Do not paste a ready-made `Authorization: Basic ...` header into a file: base64 is not encryption, and anyone who reads it has the password. The bundled OpenObserve container still starts (it is part of the `obs` profile) but receives nothing. Leave the lines out to go back to it.

- **Quote a password that has a `$`, a space or a `#`:** `OPENOBSERVE_PASSWORD='my$ecret #1'`. Unquoted, Docker Compose reads `$x` as a variable and ` #` as the start of a comment and cuts the password there without a warning. `make up-obs` checks for this, for a user set without a password, and for an address without `/api/<organisation>`, and stops with the fix.
- **The monitor's "Trace in OpenObserve" link** opens the bundled OpenObserve. For your team's, also set `TRACE_UI_URL` to its traces page, for example `TRACE_UI_URL=https://<your-openobserve-host>/web/traces?org_identifier=<organisation>&stream=trustops`.

## 4. Drive the demo by hand

`make demo` drives the same case through the 21 scenes by itself, as the five users through the gateway, and prints the live numbers; it calls Bedrock (100k to 160k tokens), so ask the operator first (ADR-59). What follows is the same flow by hand.

The case is **FCRM-2026-0151**: consumer remittances US to Mexico through a payout partner (Remesas del Norte S.A.). It is seeded as Priya's draft at intake, unassigned, with no documents. You play all five people. Use a different browser profile or a private window per person, or sign out between steps.

Read this first:

- The agents are live. The proposals, the completeness questions, the numbers and the wording differ from run to run. This guide describes structure: which screen, which action, what appears. The golden figures (9.8 to 10.4, committee 2-1, 67 audit records) are proved by the unit tests and belong to the static walkthrough; a live run prints its own numbers (ADR-27). Do not expect to see exactly 9.8 or 67.
- Nothing is approved or rejected automatically. At every step the next move is a person's.
- Leave a second window on `/monitor/FCRM-2026-0151` (as Rahul) to watch agents, guards, audit records and tokens as they happen.
- To start over: `make demo-reset` (deletes only this case's data).
- Do not create a new case for the canonical run: open 0151 from My queue. "New case" opens a form and creates nothing until you select Create case (ADR-75). It offers two paths: "Let TrustOps read my documents" (lands on Documents) or "I'll choose the change types and fill in the details" (pick from the six types, then fill the required fields on Overview; documents are optional). Either way it mints a new case id, not 0151.

The 21 scenes are the walkthrough's (`docs/requirements/research/06-walkthrough-seed-extraction.md`, section 3.10 lists the audit records each writes). The flow behind them is in `docs/architecture/sequences/`.

| # | Scene | As | Where | Do | Expect |
|---|---|---|---|---|---|
| 1 | Sign in | Priya | `/login`, then `/po` | sign in | the dashboard: active cases, awaiting my answer, average days, open conditions, My cases. No risk number anywhere |
| 2 | New case | Priya | `/queue`, then `/case/FCRM-2026-0151` | open the seeded draft | the case id, stage Intake, tabs Overview, Documents, Review, Inbox, Activity, Conditions. Nothing is on record until the first write; the first upload opens the run (`case.created`, `workflow.started`). On Overview you can later add a change type the assistant missed, or remove one with a reason, until you submit (`case.types_changed`) |
| 3 | Upload | Priya | `/case/FCRM-2026-0151/documents` | click "Use the demo's two files" (or Browse your own PDFs) | both files go Stored, Scanned, Read; the Assistant card narrates. H3 converts to Markdown with page:line anchors after Guard 1 (injection scan, PII masking); H2 runs the intake agent once per document (cheap tier). Tens of seconds, not instant. A scanned PDF is refused (no OCR) |
| 4 | Proposals | Priya | `/case/.../overview` | Confirm or Remove each proposed change type (a removal needs a reason); read the "Values found" with their source chips | proposed change types (the demo expects vendor onboarding, new geography, new product) and the values each with a page and line. Confirming the last type resolves the field set; the case shows how many fields are required and how many are filled |
| 5 | Reuse | Priya | same page, "A similar case you can reuse from" | tick values, "Reuse" | a precedent from S3's search (the demo expects FCRM-2026-0118); reused values carry provenance "reused" |
| 6 | Gaps | Priya | same page, "Questions for you" | answer the blocking questions, save | blocking gaps are asked, optional ones are not; a fact such as a cash-pickup channel is flagged |
| 7 | Review and submit | Priya | `/case/.../review` | correct a value if you like (the original is kept), then Submit | four plain checks (completion at least 80%, always-required fields, change types confirmed, no unresolved conflicts), then "Submitted". Triage assigns the analyst with the fewest open cases (R5): Rahul. Priya sees only the stage |
| 8 | Analyst queue | Rahul | `/analyst/queue` | open 0151 | it is in My queue; screening has run and proposes the fast lane or not (R4; you confirm). Research and assessment run next; the Harness button and `/monitor/FCRM-2026-0151` show agents, guards and tokens |
| 9 | Summary | Rahul | `/analyst/case/FCRM-2026-0151/summary` | read | "The assessment core is running" until it finishes; then the critic's findings (a banner) and the completeness report (missing, stale, contradictory). **The score card reads "Score withheld"**: a live score is shown only after the case's own analyst opens Risk and controls (ADR-45). Another analyst never sees it |
| 10 | Clarify | Rahul | Summary, then `/analyst/case/.../inbox` | "Ask now" opens the Inbox composer: keep, drop or edit each drafted question, then "Send round" (that send records the decision to ask, D-120). Any time from triage on, "Ask the owner a question" (Summary) or the Inbox's "Ask the owner" card sends ad-hoc questions (ADR-73) | one round only, and it pauses the analyst clock; ad-hoc questions do not pause it and do not use up the round; the submission stays untouched |
| 11 | PO answers | Priya | `/case/.../inbox` | answer the questions, optionally attach the vendor due-diligence scope letter; an ad-hoc question about a field also takes its new value | answers arrive as a new submission version; research and assessment re-run only for the changed fields; the case returns to Rahul in review. An ad-hoc answer that changes a value re-runs them only in review with no agent running; otherwise Rahul's Inbox flags "values changed since the agents ran" |
| 12 | Policy | Rahul | `/analyst/case/.../policy` | decide each clause: accept, or remove with a written reason of at least 20 characters; record the gap in the corpus; optionally search the corpus and press "Add to case" on a hit research did not propose, with a reason | every clause is quoted word for word with its source reference. One decision per item: there is no accept-all. Decisions need the case in the review stage (after the clarification round); earlier, the API answers 409 `wrong_stage` (ADR-44). An added clause is accepted at once, its text read from the pinned corpus, marked "Added by" you with the reason, recorded as `clause.added`, quoted in the pack, and it does not change the score (ADR-74) |
| 13 | Controls | Rahul | `/analyst/case/.../risk-controls` | open the tab (this starts the review and unmasks the score), confirm each control rating item by item; optionally add a planned control | the score appears: composite, band, boundary flag when within 0.5 of an edge, with the derivation per area. The controls matched per area show rating and evidence date (older than 24 months is flagged stale); a planned control is shown and never scored. If a decision fails to resolve its item, that is open defect D-53 |
| 14 | Override | Rahul | same tab | override the vendor due-diligence control (CTL-T01) to a lower rating, with a written reason of at least 20 characters | S2 recomputes. If the band changes or a boundary flag rises, a banner says "Acknowledgement required": acknowledge it in the workbench header (R3). The override is a `human.override` record with before, after and your reason. Overriding to the value the agent proposed is refused (422) |
| 15 | Recommend | Rahul | `/analyst/case/.../recommendation` | read the narrative agent's draft, edit, Lock | lock is refused until every proposal and acknowledgement is done. Locking freezes the numbers |
| 16 | Handoff | Rahul | `/analyst/case/.../handoff` | check the four release checks, release to the committee | the pack is assembled by code from the locked numbers and pinned versions; no model. Release goes to three members; votes are hidden |
| 17 | Committee asks | Ana, then Rahul | `/committee`, `/committee/case/.../pack`, `.../inbox`; Rahul on Handoff | Ana reads the 12-section pack and asks a question; Rahul answers | a thread visible to all members; the answer is attached to the pack as an addendum. If the answer says a number changed, the pack unlocks, a pack v2 is built and every vote is requested again (R6) |
| 18 | Vote | Joseph, Ana, Lin | `/committee/case/.../vote` | each casts one vote with a rationale of at least 20 characters (the demo: two approve with conditions, one defer) | "n of 3 votes in"; nobody sees another's vote; on the third vote all are revealed together and S5 tallies. Two identical votes win; a 1-1-1 split is `no_majority`, opens a thread and a second round; nothing is decided automatically (ADR-31, ADR-36) |
| 19 | Outcome | Priya, Rahul | `/case/.../conditions`; the workbench | read | the committee outcome is recorded, the drafted conditions become tracked conditions, the case closes, and Priya sees one plain message ("Your change was decided ...") with her conditions. H1 then verifies the audit chain |
| 20 | Examiner | Rahul | `/analyst/assurance/ask`; also `.../overrides`, `.../watchlist`, `.../agent-quality` | ask why the case went to committee | an answer in which each sentence cites an audit record that exists, or a refusal. The question can take up to 60 s (see the 202 gap). The watchlist tab lists re-flags after a new FATF list is activated under Configuration |
| 21 | Recap | Rahul | `/monitor/FCRM-2026-0151` | read | counters: agents run, guards passed and blocked, audit records, chain verified, tokens against the case cap (160k enforced, 120k target) |

A source chip is a button (ADR-76): in scene 4 Priya clicks a value's `case p.1 l.9` chip and a panel shows that page of her own upload with the cited line marked, six lines either side, Previous/Next page and "Open the PDF" (the file at `#page=N` in a new tab); in scene 12 Rahul (and in scene 17 the committee) click a clause's reference and read the clause word for word from the corpus. The owner has no clause preview. A document H3 has not read yet (or a scanned PDF with no text) answers "no extracted text"; the PDF still opens.

Check the result without the UI (tokens as in section 6):

```bash
curl -s -H "Authorization: Bearer $A" $G/api/fcrm/audit/verify/FCRM-2026-0151; echo   # {"ok":true,"record_count":N,...}
curl -s -H "Authorization: Bearer $A" $G/api/fcrm/cases/FCRM-2026-0151/harness | head -c 600; echo
```

If a run stops, check `make dlq` and `make logs svc=harness`, then `ops/runbooks/dlq-replay.md`. If an agent fails twice the run moves to `handoff`: the analyst completes the step by hand on the Summary tab (the proposals as JSON and a reason), and the run resumes (`docs/architecture/sequences/handoff.md`).

## 5. Make targets

"Stack" means `make up` must be running. "Bedrock" means real model calls and real money. Times are what was measured or documented on 6 October 2026 on one laptop (Apple silicon, warm caches); expect different times on other machines. "Not timed" means no measurement exists.

| Target | What it does | Stack | Bedrock | Time |
|---|---|---|---|---|
| `help` | list targets | no | no | instant |
| `bootstrap` | `tools`, `uv sync`, install git hooks, `pull` | no | no | minutes the first time (not timed) |
| `tools` | install the pinned OPA 1.15.2 into `~/.local/bin` if missing (the Linux or macOS build, Intel or ARM, picked from `uname`) | no | no | seconds |
| `env` | write `.env` from `.env.example` with generated secrets; never overwrites | no | no | seconds |
| `keys` | write the JWT keypair into `jwt_keys/` (0644, so the api container's own user can read it on Linux and WSL); an existing key keeps its value and gets those permissions; `FORCE=1` rotates | no | no | instant |
| `pull` | pull the pinned images | no | no | minutes the first time |
| `up-infra` | mongo, rabbitmq, minio, opa only | starts it | no | about a minute |
| `up` | build and start the 8-container default stack | starts it | the corpus embedding at `api` start | build a few minutes, `api` about 85 s |
| `up-obs` | `up` plus otel-collector and OpenObserve (10 containers); traces, metrics and logs go to OpenObserve over OTLP/HTTP (port 4318; there is no gRPC receiver), and the three dashboards are loaded. Stops first, with the fix, on an `.env` that would start it wrongly (`ops/scripts/check_obs_env.py`) | starts it | as `up` | as `up` |
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
| `test-unit`, `test` | the 7707 unit tests (`-m "not integration and not e2e"`) | no | no | about 290 s |
| `compose-check` | `.env.example` against the compose files, `compose config` of each file set | no (needs Docker) | no | 1.4 s |
| `web-install` | `npm ci` in `src/frontend` | no | no | not timed |
| `web-check` | web typecheck and 239 vitest tests | no | no | 17 s |
| `gate` | `check`, `test-int`, `fcrm_evals build --check`, `selfcheck` | needs Docker | no | more than `check` (not timed) |
| `test-int`, `test-integration` | every integration-marked test under `tests/` (`pytest -m integration tests`): the 464 under `tests/integration` on testcontainers or on `FCRM_TEST_MONGO_URI`, and the 74 `*_live.py` tests under `tests/unit`, which need `FCRM_TEST_MONGO_URI` and skip without it (the target says so) | needs Docker, not the stack | no | not timed through make; all of them took 192 to 225 s on a throwaway replica set |
| `ai-check` | `ai/_tools/validate_ai.py` (registry, tool permits, OPA decisions, and the rule that no model id appears outside `ai/models/tiers.yaml` in `ai/`, `src/`, `evals/` or `ops/`) and `compute_locks.py --check` | no | no | under 10 s |
| `evals` | live agent evals, 5 rows per agent, Bedrock judge; writes `evals/results/<date>/`. `EVALS_ARGS="--ai-root <copy of ai/> --results-root <own folder>"` tries a variant without touching the shipped `ai/`; its result is never a baseline | no | **yes**, projected at about USD 1 on the committed chain and not measured; USD 3.14 on 6 October with an Opus 5.5 judge | 7 to 11 minutes on the 6 October chain; not timed on the committed one |
| `evals-full` | every row of every dataset | no | **yes**, several times `evals` | slow |
| `evals-gate AGENT=<a>` | the promotion gate for one agent on the newest run | no | no | seconds |
| `openapi` | export the merged OpenAPI to `docs/architecture/openapi/api.json` | no | no | seconds |
| `dlq` | list the dead-letter queues with depth | yes | no | instant |
| `dlq-inspect q=<queue>` | show what is parked in `<queue>.dlq` (type, error, attempt, preview) | yes | no | seconds |
| `dlq-replay q=<queue>` | move `<queue>.dlq` back onto `<queue>` (`ops/scripts/dlq.py`) | yes | no | seconds |
| `dlq-purge q=<queue> CONFIRM=1` | delete the parked messages of `<queue>.dlq` (refused without `CONFIRM=1`) | yes | no | seconds |
| `token-report` | per-agent tokens, cost, latency and budget use from OpenObserve into `ops/token-analysis/runtime/` (no per-case table: metrics carry no `case_id`) | needs `up-obs` | no | seconds |
| `obs-dashboards` | load or refresh the three OpenObserve dashboards from `ops/openobserve/dashboards/` (matched by title, updated in place) | needs `up-obs` | no | seconds |
| `rotate-keys` | `keys FORCE=1`, then restart `api`; everyone signs in again | yes | no | the `api` restart, about 85 s |
| `seed-verify` | the live stack holds the seed: 80 cases, the stage distribution, the demo case untouched, a chain that verifies | yes | no | seconds |
| `verify case=<id>` | verify one case's audit chain through S4 | yes | no | seconds |
| `demo` | `demo-reset`, then the 21 scenes of case 0151 through the gateway as the five users, printing the live numbers | yes | **yes**: 100k to 160k tokens, about USD 0.47 for the complete cycle (0.41 to 0.59), about 0.34 with no retries and about 0.50 at the 160,000-token cap; an estimate from the token counts of the 6 October run at the verified prices, not a measurement on the committed chain (ask first, ADR-59) | 4 to 5 minutes on the 6 October chain |
| `e2e` | `demo`, then the live black-box rules (BB-03, 06, 08, 09, 13, 14) and the Playwright live scenes | yes | **yes**: the demo's, the cost of one cycle | the demo plus a few minutes |
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
make check         # about 8 minutes: ruff, format, mypy over 618 files, OPA, 7707 unit tests, env contract, web typecheck and 239 vitest tests
make test-int      # every integration-marked test: tests/integration on testcontainers (needs Docker), the *_live.py tests under tests/unit when FCRM_TEST_MONGO_URI is set
make gate          # check, test-int, datasets and eval self-check
```

**Browser sweep of every screen (needs the application running on port 8080; makes no AI calls, about nine minutes):**

```bash
cd src/frontend
npm ci                                  # once per checkout (Node 22 on PATH)
npx playwright install --with-deps chromium   # once per machine: the test browser (--with-deps installs its Linux libraries; drop it on macOS)
npm run test:e2e:sweep
```

It opens every screen as the product owner, the analyst, a committee member, the observer and a signed-out visitor, clicks every safe button and tab, checks the access rules in both directions, tabs through each page and runs an accessibility scan. It never uploads a document, submits, votes or publishes, so it costs nothing. Its report is `src/frontend/test-results/sweep-report.json`, with screenshots beside it. The results of each level of testing, with dates, are in `docs/governance/test-report.md`.

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

On 6 October 2026 `make check` was green on the tree of `53a1476`: 7707 passed, 4 skipped, 590 deselected (integration, e2e and resilience marks), 121 of 121 Rego tests, 239 web tests.

## 8. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `make: *** No rule to make target 'bootstrap'` | You are not in the folder with the `Makefile` |
| `429 Too Many Requests` on login | the login limit is 10 a minute per address, at the gateway and in identity. Wait a minute; reuse tokens |
| `make up` seems stuck on `api` | it takes about 85 s: S3 embeds 197 clauses on Bedrock. `make logs svc=api` shows progress or an error. With no or expired AWS credentials expect a failure there |
| Everything stalls after the laptop slept | Docker paused its virtual machine; in-flight Bedrock calls timed out. Closing the lid on battery once stalled every background job and looked like an API fault (D-50). Keep the machine awake while a case is running (on macOS, `caffeinate -i` in a second terminal does it). Then `make ps`, `make dlq`; replay what is parked (`make dlq-replay q=<queue>`) or `docker compose restart harness` |
| A case stops moving | `make dlq` and `make logs svc=harness`; a parked `caseflow.steps` message freezes a run until fixed and replayed (`ops/runbooks/dlq-replay.md`). List every queue with `docker compose exec -T rabbitmq rabbitmqctl -q list_queues name messages consumers` |
| The case shows "handed to a person" | an agent failed twice and the run is in `handoff`: open the Summary tab and use "Complete by hand" (the form shows what the agent was given). Check Bedrock (`ops/runbooks/bedrock-outage-and-fallbacks.md`); a manual output's claims are not matched to their sources |
| Every API call returns `503` | OPA is unreachable and the services fail closed. `make ps`, `make logs svc=opa` |
| Agent runs fail with a credentials or region error | AWS keys missing or expired in `.env` (or the shell), or no model access. `docker compose up -d` after fixing |
| I need to run the demo again | `make demo-reset` (this case only); `make seed` (everything); `make down-v` then `make up` (everything, including volumes) |
| `port is already allocated` | another stack holds the port. Set the matching `*_HOST_PORT` in `.env` (for example `GATEWAY_HOST_PORT=9080`) |
| `mkdir /host_mnt/Users/<you>/Downloads: operation not permitted` | macOS blocks Docker's access to `~/Downloads`: grant it, or move the repository |
| `api` unhealthy, its log says `Permission denied: '/keys/jwt.pem'` | Linux and WSL 2: the api runs as its own user (uid 10001), and a key made by the earlier `make keys` was readable only by you. `make keys` sets 0644 on the existing key without changing it; then `make up` |
| `$'\r': command not found` or `bad interpreter` in a container | the scripts were checked out with Windows line endings (`core.autocrlf=true` on a clone under `/mnt/c/`). The repository's `.gitattributes` now forces Unix endings; clone again inside the WSL file system |
| `Cannot connect to the Docker daemon` | Docker is not running: start Docker Desktop, or `sudo service docker start` on Linux and WSL |
| `permission denied while trying to connect to the Docker daemon socket` | Linux: `sudo usermod -aG docker $USER`, then sign out and back in |
| `audit` restarts with `ChainVerifyFailedError` | the Mongo volume holds audit records written under an older hash format (D-29). `make down-v` and start again |
| `api` fails asking for `DEMO_USERS_PASSWORD_HASH` | `.env` predates D-25: move it aside and run `make env` |
| `make up-obs` stops with `obs-env: .env has no ZO_ROOT_USER_PASSWORD` | an older `.env` (`make env` never overwrites): add `ZO_ROOT_USER_EMAIL=admin@trustops.example` and `ZO_ROOT_USER_PASSWORD=Oo-` followed by the output of `openssl rand -hex 14`; OpenObserve needs upper and lower case, a digit and a symbol |
| OpenObserve restarts and its log says the password is too weak | `ZO_ROOT_USER_PASSWORD` lacks a character class; fix `.env`, then `make down-v` (the login is stored on the first start, and `down-v` deletes the stored logs too) |
| `make up-obs` ends with `obs-dashboards: GET /dashboards answered 401` | the OpenObserve login in `.env` is not the one the container was created with (it is stored on the first start): put the original back, or `make down-v` and start again; then `make obs-dashboards` |
| A dashboard panel says "No data" | nothing has recorded that metric in the time range yet: the agent panels fill when an agent job runs, the case panels when a case moves, the guard and override panels when a guard rules or an analyst overrides. Widen the time range (top right) |
| `up` stops naming `MONGO_EVALS_READER_PASSWORD` | an older `.env`: add the line `MONGO_EVALS_READER_PASSWORD=` followed by any URL-safe string (`openssl rand -hex 16`); no `down-v` needed |
| The login page shows a password form, not the picker | identity serves the picker only when `DEMO_USERS_ENABLED=true` and `ENV` is `local`, `test` or `ci`. Use `riyer` and the demo password |
| Dev server says `Port 5173 is already in use` | an earlier `npm run dev` survives: `kill $(lsof -tnP -iTCP:5173 -sTCP:LISTEN)` (or `fuser -k 5173/tcp` on Linux) |
| Every screen errors on the dev server | `npm run dev` is the live mode and proxies to :8080: `make up` first, or use `npm run dev:msw` |
| `ports are not available ... 6379` | a Redis on your machine holds the port (only with `up-cache` and `up-debug`): stop it (`brew services stop redis` on macOS, `sudo service redis-server stop` on Linux) or set `REDIS_HOST_PORT` |
| `port is already allocated` on 27017 | a MongoDB on your machine holds the port: add `MONGO_HOST_PORT=27018` to `.env` and `make up` again |
| A stray `make down-v` deleted my data | there is no backup unless you made one (`ops/runbooks/backup-restore.md`); `make up` reseeds the 80 cases |

More: `ops/runbooks/README.md` indexes one short page per task (start, stop, reset, dead letters, restarts, outages, secrets, backups, audit chain, agent incidents, release).

## 9. Cost and tokens

- **Per agent run:** tokens per attempt are budgeted by tier: 15,000 (cheap), 15,000 (mid), 24,000 (strong), 28,000 (judge), with one retry (`ai/models/tiers.yaml`). Each request may write what the attempt's budget leaves (at least 1,024 tokens); a valid answer that tips the attempt over its budget is kept and recorded as an overrun, and a run that goes further is blocked and handed to a person, never silently cut (ADR-80). An input too large for one context pack is split inside H2 into at most `AGENT_MAX_PARTS` (4) parts whose answers are merged into one result; one that needs more parts is handed to a person (ADR-77).
- **Per case:** 160,000 tokens enforced by H1's ledger and 120,000 as the target (ADR-50). One measured run of the demo case's eleven agent jobs used 114,626 tokens (about USD 0.50), 91,000 when every job answered first time, before `research.system` v3 (`evals/README.md`); the last complete live run used 157,345 tokens. Both were measured on the 6 October chain (Sonnet 5.5 on mid and strong, an Opus 5.5 critic) and not repeated on the committed one, whose critic is Nova Pro. The one live run of the critic on Nova Pro used 21,851 tokens, against 16.4k on Opus for the demo case, so a run on the committed chain may use more than these counts. The static walkthrough's "16.9k" is a script, not a measurement.
- **One complete cycle** (a case from the owner's first upload to the committee outcome, all agents): about USD 0.47 (0.41 to 0.59, by the split between input and output tokens), about USD 0.34 with no retries, about USD 0.50 at the 160,000-token cap. This is an estimate from the token counts of the last complete run (6 October) at the verified prices, not a measurement on the committed chain. The same cycle on Sonnet 4.5 was estimated at about USD 0.65, because a Sonnet 5.5 token costs about two thirds of a Sonnet 4.5 token. `make e2e` costs the same as one cycle.
- **An eval run:** `make evals` was USD 3.14 and 7 to 11 minutes on the 6 October chain, with Opus 5.5 as the judge (agents 0.62, judge 2.53 at the verified prices; the committed result files record USD 2.86 at the earlier placeholder prices). On the committed chain it is projected at about USD 1, because the judge is Nova Pro (0.80 in and 3.20 out) and not Opus 5.5 (4.40 and 22.00); it has not been measured. `make evals-full` is several times that. The judge was 80% of the cost with Opus 5.5 as the judge (the share on Nova Pro is not measured); `EVALS_ARGS="--judge fake"` skips it and says FAKE on every output.
- **Hand-driving the demo** costs roughly the agents' share of one case, plus the corpus embedding at each `api` start. The prices in `ai/models/tiers.yaml` are verified AWS list prices (AWS Price List API, us-east-1, Regional rate card, read on 6 Oct 2026; each row names its source); confirm them against the AWS bill.
- Full analysis: `ops/reports/token-analysis.md`.

## 10. What is not done and not verified

The full list, each item with its evidence, is `docs/governance/known-gaps.md`. The ones that change what you can do:

- `make nightly`, `make contracts`, `make api-gen`, `make locks`, `make build-usage`, `make backup` and `make restore` print "not yet wired". `make demo`, `make e2e`, `make seed-verify`, `make verify` and `make resilience` are wired; the golden numbers (9.8 to 10.4, 2-1, 67 records) are unit tests, and a live run prints its own.
- **No complete live acceptance pass on the committed chain.** The last 21-scene pass is 21 of 21 on the 6 October chain (Sonnet 5.5 on mid and strong, an Opus 5.5 critic) before the clean-up merges. The committed chain keeps Sonnet 5.5 and puts Nova Pro on the critic, and no run has been made on that combination. Two live runs on a Sonnet 4.5 chain (the economy profile, with Nova Pro as the judge and a judge budget of 20,000; ADR-62 committed it and ADR-65 replaced it) stopped early: at scene 9, where assessment kept 3 of 19 claims after Guard 4 so the case had no controls, and at scene 8, where the critic on Nova Pro passed its 20,000 budget (now 28,000). Runs on Haiku and Nova only stopped at scene 3 and scene 8. The prompts and thresholds were measured on Sonnet 5.5 and Opus 5.5 (D-95).
- **A manual output (a run completed from `handoff`) is less machine-verified than an agent's:** its claims are not matched against their sources; the record says so.
- **S1 pins the versions S3 holds in force** (`GET /internal/config/pins`, cached 5 s) and reads a published field set from S3; when S3 does not answer in a few seconds it pins the seed's versions and logs a warning naming the case. Proved on a real replica set, not yet in the composed stack.
- **The per-case token cap is enforced across jobs by a ledger: 160,000 enforced, 120,000 target** (ADR-50); a run that needs retries can use most of the cap.
- **Redis does nothing** (`make up-cache`); **S3 Vectors is not in the image** (vectors are in memory; each `api` start embeds the corpus).
- The black-box tests under `tests/integration` run in process. The live ones (`make e2e`) read a completed demo, and the twelve resilience drills have run live (`docs/architecture/resilience.md`); neither has been run on the committed model chain (D-95, D-100).
- **The assessment agent fails its eval gate** (`li_within_one` 0.70 against 0.85); eight of nine agents pass (`evals/results/2026-10-06/gate.json`). That run was on the 6 October chain (Sonnet 5.5 on mid and strong, Opus 5.5 as the critic and the judge), and there is no committed eval baseline for the committed chain: a run of one row per agent on a Sonnet 4.5 chain gave 7 of 9 gates at about USD 0.44, is not a baseline, and does not describe the committed chain.
- **The policy corpus and demo documents are synthetic**, 193 of 197 clauses generated (ADR-29). No OCR, no SharePoint.
- **Identity is a demo directory** with one shared password and one JWT key; no TLS; single-node Mongo and RabbitMQ.
- The AWS path (`ops/aws/README.md`) is a design; nothing was deployed.
- **Ad-hoc questions to the owner (ADR-73) and the round's composer fix (D-120) are not yet seen on the live stack**; they are unit-tested with fakes and the MSW mock. An answer that changes a value re-runs the agents only in review with no agent running; elsewhere it is flagged to the analyst.

## Appendix: the compose files and the `.env`

| File | Purpose |
|---|---|
| `docker-compose.yaml` | the whole stack; profile `obs` adds the two observability containers (otel-collector, OpenObserve), profile `cache` adds Redis |
| `compose.debug.yaml` | publishes RabbitMQ (:5672, :15672), MinIO (:9000, :9001), OPA (:8181), Redis and the harness health port (:9100) to the host. `make up-debug` |
| `compose.dev.yaml` | bind mounts, `uvicorn --reload`, the Vite dev server, DEBUG logs. `make up-dev` |
| `compose.test.yaml` | the test stack: in-memory vectors, dummy embeddings for `api`, AWS keys blanked for `api`; project `trustops-test`. Agents still call live Bedrock. The environment check validates it; no make target starts it today |

`.env` has three sections: (1) the AWS block (the only place a person must add anything), (2) optional host ports, commented out, (3) generated passwords and secrets. `.env.example` is the full reference for every variable, and `make compose-check` keeps it equal to the compose files. A host port is the number on your machine for a container; by default only the gateway publishes one. A generated password cannot change after the first start without `make down-v`, except `MONGO_APP_PASSWORD`, `MONGO_AUDIT_WRITER_PASSWORD` and `MONGO_EVALS_READER_PASSWORD` (`ops/runbooks/secrets-and-keys.md`).
