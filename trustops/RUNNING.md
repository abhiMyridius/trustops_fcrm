# Running and checking TrustOps

How to set up the repo, run the checks, start the full stack and confirm that it works.

Every command runs from the repo root, the folder that holds the `Makefile`. Running `make` from anywhere else fails with `No rule to make target`.

```bash
cd ~/MacWorkspace/Hackathon/trustops_fcrm/trustops     # or wherever you cloned it
```

This file describes the state on 5 October 2026. If a step stops matching what you see, check `git log --oneline` first: every commit says what it adds and what it is still waiting on.

## Quick start: run the whole thing

The application is two parts. The backend is one Docker stack. The frontend is one Node dev server. Run them in two terminals. Both terminals start at the repo root.

**If the repo sits under `~/Downloads`:** macOS blocks Docker from mounting files there until you allow it (`mkdir /host_mnt/Users/<you>/Downloads: operation not permitted`). Either turn on Downloads Folder for Docker under System Settings, Privacy & Security, Files and Folders (or Full Disk Access), then restart Docker Desktop, or keep the repo outside `~/Downloads`, for example under `~/MacWorkspace`.

**Terminal 1: backend**

1. `make tools` installs the OPA binary. Once.
2. `make bootstrap` installs Python packages and pulls images. Once.
3. `make env` writes `.env` with local passwords. Once. Add AWS keys to it only if you want agents to run.
4. `make keys` writes the JWT keypair. Once.
5. Start the stack:

   ```bash
   make up
   ```

   The first build takes a few minutes. `make up` runs eight containers: `mongo`, `rabbitmq`, `minio`, `opa`, `api` (identity and S1–S5 in one process), `harness` (H1 caseflow and H2 agent-worker in one process), `gateway` and `web`. Only the gateway publishes a port on your Mac (8080). `make up-obs` adds Jaeger, Prometheus and Grafana, and `make up-debug` also publishes the infrastructure ports (see [What the compose files are](#what-the-compose-files-are)).
6. `make ps` should show 8 containers healthy.
7. `curl -s http://localhost:8080/healthz` should answer. The backend is up.

**Terminal 2: frontend**

8. `cd src/frontend` then `npm ci`. Once.
9. Choose the backend for the UI:
   - Live backend, the default. Run `npm run dev`. Vite proxies `/api` to the gateway on :8080, so the stack from terminal 1 must be up. Sign-in works; most screens still lack a backend route, see the last section.
   - Mock backend. Run `npm run dev:msw`. Every screen works against the in-browser mock. This is the one to use for the full click-through while the live join is unfinished.
10. Open http://localhost:5173. On the live backend the login page offers the seeded-user picker only when the identity service serves one (see below); otherwise it shows a username and password form.

**To stop:** `make down` in terminal 1, Ctrl-C in terminal 2.

**Why live mode is only partly useful yet, in plain words.** The backend starts and answers `curl` correctly, and the web now signs in against it (the login contract was aligned, ADR-30). Behind the login, most analyst screens have no backend route yet, and the backend holds no seeded cases. The full list is in the last section of this file, and `src/frontend/src/test/contract/paths.test.ts` keeps it honest: it fails when a route the web calls is neither in `docs/architecture/openapi/api.json` nor in its `PENDING_ROUTES` list.

## What works today

| Area | Status |
|---|---|
| Code checks: lint, format, types, unit tests, OPA policy tests | ✅ `make check` |
| Integration tests against a real MongoDB | ✅ `make test-int` |
| Web unit tests and type check | ✅ `npm test`, `npm run typecheck` |
| Backend stack: `api` (identity, S1–S5), `harness` (H1, H2), gateway, web, infra | ✅ `make up` starts 8 containers, all healthy, from empty volumes |
| Login and access policy on every service, checked with `curl` through the gateway | ✅ |
| Web app on its own, against the in-browser mock backend (`npm run dev:msw`) | ✅ every screen for all three personas |
| Web app against the live backend | ⏳ sign-in works; about a fifth of the web's routes have a backend route (17 of 83). See [Where the backend and the frontend stand](#where-the-backend-and-the-frontend-stand) |
| H3 ingestion and H4 context workers | ⏳ built and tested, but no start entry point yet, so they are not part of the harness |
| Driving case FCRM-2026-0151 end to end through the live stack | ⏳ the services do not yet pass events to each other, and S1 holds no seeded cases |
| Agents on live Bedrock only (no replay mode, ADR-27) | ⏳ needs AWS keys in `.env`; without them every agent run fails at the model call |
| `make demo`, `make seed-verify`, `make verify`, `make e2e`, `make evals` | ⏳ placeholders that print "pending" |

The demo figures (9.8 → 10.4, a 2–1 committee vote, 67 audit records) are currently proved by the unit and integration tests, not by a live run.

**In one sentence:** the backend and the frontend each run on their own, but they do not yet talk to each other. Section 5 shows how to run each, and the last section lists exactly where the seam is.

## 0. Prerequisites

- Docker Desktop, running
- [`uv`](https://docs.astral.sh/uv/), Node 22, `make`
- The pinned OPA binary. Install it into `~/.local/bin` with:

  ```bash
  make tools
  ```

Two things to check before the first run:

- **Another TrustOps stack already running.** Compose uses the project name `trustops`, so a second copy clashes with the first. Stop the other one with `docker compose -p trustops down`.
- **The repo is under `~/Downloads` on macOS.** macOS can block Docker from mounting files there. If `up` fails with a mount or permission error, give Docker access (System Settings → Privacy & Security → Files and Folders, or Full Disk Access), or move the repo outside Downloads.

## 1. One-time setup

```bash
make bootstrap   # uv sync the workspace, install git hooks, pull images
make env         # create .env from .env.example with generated local secrets (never overwrites)
make keys        # generate the dev RS256 JWT keypair into jwt_keys/
```

Every seeded demo user shares one password, `demo-password-2026`. To choose your own, run `make env DEMO_PASSWORD=<yours>` instead. This works only while `.env` does not exist yet.

### The `.env` file

`make env` writes a short `.env` of about 45 lines in three sections. Everything not in it uses a built-in default. `.env.example` is the full reference for every setting; you never edit or copy it.

| Section | What to do |
|---|---|
| **1. Live AI on Amazon Bedrock** | Needed for any agent run. Fill in `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` (`AWS_DEFAULT_REGION` is already `us-east-1`; uncomment `AWS_SESSION_TOKEN` only for SSO or temporary keys). These are the standard boto3 and AWS CLI names. The agents call Bedrock through Pydantic AI's Bedrock provider, and the vector store calls S3 Vectors through aioboto3. **There is no replay mode (ADR-27):** without AWS keys every agent job fails at the Bedrock call. The services, login and the web app still work without keys; only agent runs need them |
| **2. Host ports** | Optional. Change a port only if `up` reports `port is already allocated`: uncomment that line and choose a free port |
| **3. Generated** | Leave as is. These are local passwords. The databases are created with them on the first start, so changing them later needs `make down-v`, which deletes local data |

**About ports.** A host port is the number on your Mac where you reach a container, for example `http://localhost:8080` for the app. By default only the gateway publishes one. The `api` and `harness` containers have no host port at all; you reach the API only through the gateway on 8080. The infrastructure ports (Mongo, RabbitMQ, MinIO, OPA) are published only by `make up-debug`, and the dashboards' ports only by `make up-obs`. A port clashes only when another program on your Mac already uses the same number.

**MinIO** is a small S3-compatible storage server running locally. It holds uploaded documents, the extracted text, frozen committee packs and vector-index data. On AWS the same code uses real S3 instead.

If you created a `.env` before this change, keep it: it still works. To switch to the short form before your first `make up`, delete it and run `make env` again. If the stack has already run once, run `make down-v` first, because the databases already use the old passwords.

Never commit `.env` or `jwt_keys/`.

## 2. Check the code (no stack needed)

```bash
make check       # ruff, format check, mypy, OPA check + 54 Rego tests, 2,561 unit tests, env contract (~2 min)
make test-int    # 235 integration tests against a real MongoDB replica set (needs Docker)
```

To point the integration tests at a replica set you already run, set `FCRM_TEST_MONGO_URI` first, for example:

```bash
FCRM_TEST_MONGO_URI='mongodb://127.0.0.1:27217/?replicaSet=rs0' make test-int
```

Then run the web checks:

```bash
cd src/frontend
npm ci
npm test            # vitest unit tests
npm run typecheck
npm run lint
cd ../..
```

Expected result: every suite is green, with a few skips whose reasons are written into the tests.

## 3. Start the full stack

```bash
make up          # 8 containers: mongo rabbitmq minio opa api harness gateway web
make ps          # expect 8 containers healthy
```

The first build takes a few minutes. Mongo and MinIO initialise themselves on every start (replica set, users and indexes; buckets), so there are no init containers: a container is `healthy` only once it is initialised.

Other ways to start it:

| Command | Adds |
|---|---|
| `make up-obs` | otel-collector, Jaeger (:16686), Prometheus (:9090), Grafana (:3000); the api and harness then export traces and metrics (12 containers) |
| `make up-cache` | Redis, used for SSE fan-out across api replicas through `REDIS_URL` |
| `make up-debug` | publishes Mongo (:27017), RabbitMQ (:5672, :15672), MinIO (:9000, :9001), OPA (:8181) and the harness health port (:9100) to your Mac |
| `make up-dev` | live reload against your source and the Vite dev server (see below) |

**Port clashes.** If you see `port is already allocated`, another local stack is using the same host port. Set the matching `*_HOST_PORT` in `.env`, for example `GATEWAY_HOST_PORT=9080`, and run the command again. By default only the gateway port can clash; `make up-debug` and `make up-cache` publish more.

### What the compose files are

| File | Purpose |
|---|---|
| `docker-compose.yaml` | The whole stack. The default profile is the 8 containers; profile `obs` adds the four observability containers and profile `cache` adds Redis |
| `compose.debug.yaml` | Publishes the infrastructure and harness health ports to your Mac. `make up-debug` |
| `compose.dev.yaml` | Live reload against your source: bind mounts, `uvicorn --reload`, the Vite dev server, DEBUG logs. `make up-dev` |
| `compose.test.yaml` | The test stack: in-memory vectors and dummy embeddings for S3, AWS keys blanked for the api, its own project `trustops-test`. Agents still call live Bedrock |

## 4. Confirm it works

```bash
G=http://localhost:8080          # or your GATEWAY_HOST_PORT

curl -s $G/healthz; echo         # gateway is up

login() {
  curl -s -X POST $G/api/fcrm/auth/login -H 'content-type: application/json' \
    -d "{\"email\":\"$1\",\"password\":\"demo-password-2026\"}" |
  python3 -c 'import sys, json; print(json.load(sys.stdin)["access_token"])'
}
A=$(login riyer)                 # Rahul Iyer, FCRM analyst
P=$(login pnair)                 # Priya Nair, product owner
C=$(login amendes)               # Ana Mendes, risk committee
```

| Check | Command | Expect |
|---|---|---|
| The analyst reads the active scoring model | `curl -s -H "Authorization: Bearer $A" $G/api/fcrm/scoring/models/active` | `200` with the model JSON |
| The demo case's audit chain verifies | `curl -s -H "Authorization: Bearer $A" $G/api/fcrm/audit/verify/FCRM-2026-0151` | `200`, chain valid |
| The committee can read the audit trail | `curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $C" $G/api/fcrm/audit/records/FCRM-2026-0151` | `200` |
| The product owner never sees scores | `curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $P" $G/api/fcrm/scoring/models/active` | `403` |
| No token is rejected | `curl -s -o /dev/null -w '%{http_code}\n' $G/api/fcrm/scoring/models/active` | `401` |
| Nobody but a service account can write audit records | `curl -s -o /dev/null -w '%{http_code}\n' -X POST -H "Authorization: Bearer $A" -H 'content-type: application/json' -d '{}' $G/api/fcrm/audit/records` | `403` |

Other logins: `tgold` and `skim` are product owners, `mshah` and `dbrooks` are analysts, and `jokafor` and `lchen` are committee members. You can also log in with the full seed id, such as `u-riyer`.

Logs for one service: `make logs svc=case`. Leave `svc` empty to follow every service.

### Dashboards

| Tool | URL | Login |
|---|---|---|
| Grafana | http://localhost:3000 | `admin` / `GF_SECURITY_ADMIN_PASSWORD` from `.env` |
| Jaeger (traces) | http://localhost:16686 | none |
| Prometheus | http://localhost:9090 | none |

## 5. Run the web app

There are three ways to see the UI. Pick by what you want to look at.

| Way | URL | Backend | Use it for |
|---|---|---|---|
| **A. Mock, dev server** (`npm run dev:msw`) | http://localhost:5173 | MSW in the browser, fed by the P7 seed | Every screen, every persona, the full 0151 click-through. Needs no Docker |
| **B. Live, packaged** | http://localhost:8080 | The real services through the gateway | Checking what the backend really serves. This is what `make up` gives you |
| **C. Live, dev server** (`npm run dev`) | http://localhost:5173 | The real services, proxied by Vite to :8080 | Working on the FE against the real BE with hot reload |

On the Monitor page the badge says how updates arrive: "live stream", "reconnecting" or "polling". The mock mode has no stream, so it polls.

### A. Mock mode

```bash
cd src/frontend
npm ci                           # first time only
npm run dev:msw                  # http://localhost:5173
```

`dev:msw` sets `VITE_API_MODE=msw`. The login page shows a picker of seeded users and collects no password. The URL prefix chooses the persona: `/` is the product owner, `/analyst` the FCRM analyst, `/committee` the risk committee. vitest and the Playwright scenes in `e2e/scenes` use this mode too; a production build never contains it.

### B. Live mode, packaged (the web image behind the gateway)

```bash
make up
open http://localhost:8080
```

The web image is built with `VITE_API_MODE=live` and the MSW worker file is removed from it, so every request goes to the real services. The login page asks the identity service for the demo picker; the service answers it only when `DEMO_USERS_ENABLED=true` and `ENV` is `local`, `test` or `ci`, and answers 404 anywhere else, in which case the page shows the username and password form (`riyer` / `demo-password-2026`).

### C. Live mode on the dev server

Start the backend (step 3), then point the dev server at it:

```bash
cd src/frontend
npm run dev                      # live is the default; Vite proxies /api/* to http://localhost:8080
```

The login page uses the demo picker when identity serves one, otherwise the username/password form: log in with a seed id and the demo password (`riyer` / `demo-password-2026`). `VITE_DEMO_USERS=false` in `src/frontend/.env.local` (ignored by git) skips the picker request altogether.

If Vite says `Port 5173 is already in use`, an earlier `npm run dev` is still running, often from before a folder move. Stop it with `kill $(lsof -tnP -iTCP:5173 -sTCP:LISTEN)` and start again. The dev server uses `strictPort`, so it never picks another port.

## 6. Stop

```bash
make down        # stop the stack and keep its data
make down-v      # stop the stack and delete every local volume
```

## Where the backend and the frontend stand

The frontend was built against the MSW mock contract, which is the full design: 78 endpoints. The backend serves 33 routes today. Both sides are real code with tests, but they were built in parallel and the join has not been made. This table is what the live app will and will not find, screen by screen, as of 5 October 2026.

| Web area | What the web calls | What the backend serves | Live today? |
|---|---|---|---|
| Login | `POST auth/login` with `{username, password}`, `GET auth/demo-users`, `auth/me`, `auth/refresh`, `auth/logout` | The same, plus `GET auth/users?role=` (roster). `login` takes `username` or `email`; `demo-users` and an empty password work only in a local environment with `DEMO_USERS_ENABLED=true` (ADR-30) | ✅ |
| PO: my queue, case list, create case | `GET dashboard`, `GET/POST cases`, `GET cases/{id}` | `GET/POST cases`, `GET cases/{id}`, `GET cases/{id}/stream`. No `dashboard` | Partial. S1 holds **no seeded cases**, so the list is empty until a case is created in the UI |
| PO: intake fields, review, submit | `PUT cases/{id}/intake/values/{field}`, `intake/categories`, `review`, `validate`, `submit` | `PUT cases/{id}/fields/{field}`, `PUT cases/{id}/title`, `POST cases/{id}/submit`, `GET versions`, `versions/diff` | Partial. Submit and title match; the field-write path differs; review and validate are missing |
| PO: documents, inbox, clarifications, conditions, outcome, activity, integrations | 10 routes | None | ❌ |
| Analyst: queue, workbench, screening, completeness, policy, risk, items, acknowledge, gaps, recommendation, lock and unlock, handoff, close, harness, reflags | 25 routes under `cases/{id}/…`, `queue`, `dashboard/analyst`, `reflags` | None. The gateway sends them to S1, which 404s | ❌ The whole analyst workbench |
| Scoring | `GET scoring/models/{v}`, `scoring/routing-rules/{v}`, `POST scoring/simulate` | `GET models/active`, `models/{v}`, `routing/active`, `POST score` | Partial. The model read works; routing and simulate have different paths |
| Policy search and config | `POST policy/search`, `policy/config/*` | `POST policy/search` only | Partial. Search works (4 clauses indexed, not 197); config screens are missing |
| Audit | `GET audit/records`, `verify/{case}`, `examiner/ask`, `overrides`, `agent-quality`, `versions/{case}` | `GET records/{case}`, `verify/{case}`, `POST records` (service role only) | Partial. Verify works; the records list and the three analytics views differ or are missing |
| Committee: queue, packs, votes, threads, outcome | `committee/queue`, `packs`, `cases/{id}/packs[/latest]`, `packs/{id}/release`, `packs/{id}/votes`, `packs/{id}/threads`, `threads/{id}/answer`, `cases/{id}/outcome` | All of them, under the web's names, plus `GET packs/{id}`, `GET packs/{id}/outcome` and H1's `POST packs`, `packs/{id}/supersede`, `packs/{id}/revote`, `POST outcomes` | ✅ S5 serves the whole committee surface (votes hidden until all three are cast, 1-1-1 is `no_majority` and opens round two, threads, pack release with four checks) and publishes its events through its outbox. The queue is empty until a pack is released; H1 does not call S5 yet |
| Live updates | `GET cases/{id}/stream` as an `EventSource` with `?access_token=` (the gateway forwards it as the header) | `GET cases/{id}/stream`, bearer token required | Partial. The app connects, refreshes its session before each reconnect and polls while the stream is not live; the backend's frame format is still to be aligned |

Behind the HTTP seam there is a second gap inside the backend: H1 does not call S5 yet (S5 now publishes its events), S1 never updates a case's state, and the H3/H4 workers have no process. So even with every route matched, a case would not yet move from intake to a decision. The order of work to close the gap is:

1. ~~Align the login payload so the live UI can sign in.~~ Done (ADR-30).
2. Seed S1 with the 80 cases so the PO and analyst queues are not empty.
3. Add the analyst workbench routes to S1, or a thin read model over H1's `case_runs`, since those 25 routes are the bulk of the missing surface.
4. Make S5 publish its events and S1 consume state changes, so the case actually moves.
5. Rename the handful of mismatched paths (field writes, routing rules, audit records, committee queue) on whichever side is cheaper.
6. Give H3 and H4 a `__main__` so documents can be ingested, and wire replay mode so the stack runs without AWS keys.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `make: *** No rule to make target 'bootstrap'` | You are not in the repo root. `cd` into the folder that holds the `Makefile` |
| `No resource found to remove for project "trustops"` | Harmless: no other TrustOps stack was running |
| `port is already allocated` | Another local stack holds the port. Set the `*_HOST_PORT` values in `.env` |
| `error while creating mount source path ... mkdir /host_mnt/Users/<you>/Downloads: operation not permitted` | macOS is blocking Docker's access to `~/Downloads`. Grant Docker the Downloads Folder (or Full Disk Access) in System Settings and restart Docker, or move the repo out of Downloads. See the quick start |
| `audit` keeps restarting with `ChainVerifyFailedError` | The audit records in the Mongo volume were written under an older hash format (D-29). Run `make down-v`, then start again, to reseed |
| `api` fails at start, asking for `DEMO_USERS_PASSWORD_HASH` | `.env` predates D-25. Move it aside and run `make env` again |
| Every API call returns `503` | OPA is not reachable, and the services fail closed by design. Check with `make ps` and `make logs svc=opa` |
| Every screen shows an error on the dev server | `npm run dev` is the live mode and proxies to the gateway on :8080: start the stack (`make up`) or use `npm run dev:msw` for the mock |
| `Port 5173 is already in use` | A previous `npm run dev` is still running (it survives a folder move). `kill $(lsof -tnP -iTCP:5173 -sTCP:LISTEN)` and start again |
| The login page shows a password form instead of the demo picker | Identity serves the picker only when `DEMO_USERS_ENABLED=true` and `ENV` is `local`, `test` or `ci` (otherwise `GET auth/demo-users` is a 404). Use the form with a seed id and the demo password |
| Live PO queue is empty | S1 has no seeded cases yet (`make seed` is pending). Create a case from the UI, or use mock mode to see the 80 seed cases |
| Agent jobs fail with a Bedrock or credentials error | Agents call live Bedrock only (ADR-27). Fill in the AWS keys in `.env` |
| `ports are not available ... 127.0.0.1:6379 ... address already in use` | Only `make up-cache` with `make up-debug` publishes Redis. A Homebrew `redis-server` holds the port: `brew services stop redis`, or set `REDIS_HOST_PORT=6380` in `.env` |
