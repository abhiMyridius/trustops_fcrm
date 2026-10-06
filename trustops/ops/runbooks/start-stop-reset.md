# Start, stop and reset

Run everything from the repo root. The compose project is named `trustops`; its volumes are `mongo_data`, `rabbit_data`, `minio_data`, `prometheus_data` and `grafana_data` (`docker-compose.yaml`).

## Start

```bash
make up          # env + keys, build the two images, start 8 containers, wait for health (up to 600 s)
make ps          # every container should say healthy
curl -s http://localhost:8080/healthz; echo
```

- The first build takes a few minutes. After that `make up` re-uses the images.
- `api` takes about 85 seconds to become healthy. S3 (policy) embeds the 197 corpus clauses on Bedrock when it starts (commit `5a02c09`). `make up` waits for it; do not read a slow `api` as a failure until `make logs svc=api` shows an error.
- `make up` creates `.env` and `jwt_keys/` if they are missing and never overwrites them. Put the AWS keys in `.env` before the first `make up`: embeddings and every agent call go to Bedrock. How `api` behaves at start with no keys was not tested by the lane that wrote this page; see [secrets-and-keys.md](secrets-and-keys.md).
- Other starts: `make up-obs` (adds Jaeger, Prometheus, Grafana, the collector), `make up-cache` (Redis for SSE fan-out), `make up-debug` (publishes infrastructure ports to the host), `make up-dev` (source bind mounts and the Vite dev server). Each is a separate `docker compose up -d` with its own file and profile set; `make down` stops every profile.

## Stop

| Command | Containers | Data |
|---|---|---|
| `make down` | stopped and removed (every profile) | kept in the volumes |
| `make down-v` | stopped and removed | **all five volumes deleted**: every case, the audit chain, uploads, RabbitMQ queues |
| `make reset` | `down-v` then `up` | deleted, then re-seeded from the P7 seed on first start |

`make down-v` is the only way to change a generated password in `.env`, because the databases are created with the passwords of the first start.

## Reload the seed without wiping the volumes

```bash
make seed        # drops the app databases, empties the audit collections, replays the Mongo init, restarts api and harness, stages the demo PDFs
```

- It refuses to run when `ENV` is `prod` or `production` (`ops/scripts/seed_reset.py`).
- It drops `fcrm_case`, `fcrm_decision`, `fcrm_harness`, `fcrm_policy`, `fcrm_scoring` and `fcrm_identity`, and empties `audit_records`, `proposals`, `verify_runs` and `idempotency_keys` in `fcrm_audit` (as the Mongo root user, which alone may). It does not touch `fcrm_evals` or the MinIO buckets other than copying the three demo PDFs to `uploads/_fixtures/`.
- Afterwards `api` and `harness` restart and `SEED_ON_EMPTY` reloads every collection: 80 cases, the 65 closed cases' nine-record audit chains, users, policy corpus. Expect another ~85 s while `api` embeds the corpus.

## Run the demo case again

```bash
make demo-reset  # deletes case FCRM-2026-0151's documents, uploads, audit records and Caseflow run; the other 79 cases stay
```

After it, the PO can upload the demo files again; the first write opens a new run (ADR-39). The staged PDFs under `uploads/_fixtures/` survive, so the "Use the demo's two files" button keeps working.

## Look at it

```bash
make logs svc=harness     # follow one service (api, harness, gateway, web, mongo, rabbitmq, minio, opa); empty svc follows all
make dlq                  # dead-letter queues and their depth
docker compose exec -T rabbitmq rabbitmqctl -q list_queues name messages consumers   # every queue
```

## When the laptop sleeps

Docker Desktop pauses the VM. On wake, containers resume but Bedrock calls that were in flight have timed out, and RabbitMQ may redeliver. Run `make ps`, then `make dlq`. If a `*.dlq` queue holds messages, follow [dlq-replay.md](dlq-replay.md). If a case sits still with empty DLQs, restart the harness (`docker compose restart harness`, see [restart-a-worker.md](restart-a-worker.md)).

## Checked and not checked

- Checked: every `make` target above exists, and `make -n up|down|down-v|reset|seed|demo-reset|ps|logs` expand to the commands described. The behaviour of `seed_reset.py` is documented in its own docstring and tested by `tests/integration/live_mongo/test_seed_reset_live.py` (collected, not run by the lane that wrote this page).
- Needs the live stack to confirm: the 85-second figure (read from the commit message of `5a02c09`, not re-measured), the 600 s wait being enough on a cold build, and the "first write opens a new run" behaviour after `make demo-reset`.
