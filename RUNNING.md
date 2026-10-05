# Running and checking TrustOps

How to set up the repo, run the checks, start the full stack and confirm that it works.

Every command runs from the repo root, the folder that holds the `Makefile`. Running `make` from anywhere else fails with `No rule to make target`.

```bash
cd ~/Downloads/Hackathon/trustops     # or wherever you cloned it
```

This file describes the state on 5 October 2026. If a step stops matching what you see, check `git log --oneline` first: every commit says what it adds and what it is still waiting on.

## What works today

| Area | Status |
|---|---|
| Code checks: lint, format, types, unit tests, OPA policy tests | ✅ `make check` |
| Integration tests against a real MongoDB | ✅ `make test-int` |
| Web unit tests and type check | ✅ `npm test`, `npm run typecheck` |
| Full stack: identity, S1–S5, H1, H2, gateway, web, infra, observability | ✅ boots, 19 containers healthy |
| Login and access policy on every service | ✅ checked live through the gateway |
| H3 ingestion and H4 context workers as containers | ⏳ built and tested, but no start entry point yet; run them at scale 0 |
| Driving case FCRM-2026-0151 end to end through the live stack | ⏳ the services do not yet pass events to each other |
| `make demo`, `make seed-verify`, `make verify`, `make e2e` | ⏳ placeholders that print "pending" |

The demo figures (9.8 → 10.4, a 2–1 committee vote, 67 audit records) are currently proved by the unit and integration tests, not by a live run.

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
| **1. Live AI on Amazon Bedrock** | Optional. Leave it empty and the agents run on recorded replays: no cost, no cloud. To go live, fill in `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` (`AWS_DEFAULT_REGION` is already `us-east-1`; uncomment `AWS_SESSION_TOKEN` only for SSO or temporary keys) and set `FCRM_LLM_MODE=live`. These are the standard boto3 and AWS CLI names. The agents call Bedrock through Pydantic AI's Bedrock provider, and the vector store calls S3 Vectors through aioboto3 |
| **2. Host ports** | Optional. Change a port only if `up` reports `port is already allocated`: uncomment that line and choose a free port |
| **3. Generated** | Leave as is. These are local passwords. The databases are created with them on the first start, so changing them later needs `make down-v`, which deletes local data |

**About ports.** A host port is the number on your Mac where you reach a container, for example `http://localhost:8080` for the app. The six API services have no host port at all; you reach them only through the gateway on 8080. The other ports exist for the infrastructure and the dashboards. They clash only when another program on your Mac already uses the same number.

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
cd src/web
npm ci
npm test            # vitest unit tests
npm run typecheck
npm run lint
cd ../..
```

Expected result: every suite is green, with a few skips whose reasons are written into the tests.

## 3. Start the full stack

The H3 ingestion and H4 context workers have no start entry point yet. Left in, they crash-loop and `make up` waits on them until it times out. Start the stack without them:

```bash
docker compose -f docker-compose.yaml -f compose.obs.yaml up -d --build \
  --scale ingestion-worker=0 --scale context-worker=0

make ps          # expect 19 containers healthy; otel-collector has no health check by design
```

The first build takes a few minutes.

**Port clashes.** If you see `port is already allocated`, another local stack is using the same host ports. Set the matching `*_HOST_PORT` values in `.env`, for example `GATEWAY_HOST_PORT=9080`, and run the command again.

### What the compose files are

| File | Purpose |
|---|---|
| `docker-compose.yaml` | The base stack, with production-style images |
| `compose.obs.yaml` | Observability (otel-collector, Jaeger, Prometheus, Grafana). Always included by the Makefile |
| `compose.dev.yaml` | Live reload against your source: bind mounts, `uvicorn --reload`, the Vite dev server, DEBUG logs. Start with `make up-dev` |
| `compose.test.yaml` | A deterministic test stack: recorded model replays, in-memory vectors, AWS keys blanked, its own project `trustops-test` |

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

## 5. Look at the web app

The full click-through is not wired to the live services yet. Run the web app against its built-in seed data instead:

```bash
cd src/web
npm run dev                      # http://localhost:5173
```

The URL prefix chooses the persona: `/` is the product owner, `/analyst` the FCRM analyst, `/committee` the risk committee.

## 6. Stop

```bash
make down        # stop the stack and keep its data
make down-v      # stop the stack and delete every local volume
```

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `make: *** No rule to make target 'bootstrap'` | You are not in the repo root. `cd` into the folder that holds the `Makefile` |
| `No resource found to remove for project "trustops"` | Harmless: no other TrustOps stack was running |
| `make up` waits, then times out | `ingestion-worker` and `context-worker` crash-loop (no start entry point yet). Use the scale-0 command in step 3 |
| `port is already allocated` | Another local stack holds the port. Set the `*_HOST_PORT` values in `.env` |
| Mount or permission error on start | macOS is blocking Docker's access to `~/Downloads`. See step 0 |
| `audit` keeps restarting with `ChainVerifyFailedError` | The audit records in the Mongo volume were written under an older hash format (D-29). Run `make down-v`, then start again, to reseed |
| `identity` fails at start, asking for `DEMO_USERS_PASSWORD_HASH` | `.env` predates D-25. Move it aside and run `make env` again |
| Every API call returns `503` | OPA is not reachable, and the services fail closed by design. Check with `make ps` and `make logs svc=opa` |
