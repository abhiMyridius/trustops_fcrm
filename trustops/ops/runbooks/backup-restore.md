# Backup and restore

`make backup` and `make restore` print "not yet wired". This page gives the manual commands. They copy Mongo and MinIO out of the running stack and back in. All data is synthetic, but the archives still hold the audit chain and, if you dump `admin`, password hashes, so keep them out of the repo: write to a folder under your home directory, not under `ops/` (`.gitignore` does not exclude `ops/backups/`).

## What holds state

| Store | Volume | Contents | Backed up by |
|---|---|---|---|
| MongoDB 8, single-node replica set `rs0` | `trustops_mongo_data` | `fcrm_case`, `fcrm_decision`, `fcrm_harness`, `fcrm_policy`, `fcrm_scoring`, `fcrm_identity`, `fcrm_audit`, and `fcrm_evals` once an eval run was published | `mongodump`, one archive per database |
| MinIO | `trustops_minio_data` | buckets `uploads`, `canonical`, `packs` (versioned), `vectors`, `jobs` | `mc mirror` per bucket |
| RabbitMQ | `trustops_rabbit_data` | queued messages | **not backed up** (see below) |
| Redis, Prometheus, Grafana | `prometheus_data`, `grafana_data` | cache and dashboards | not backed up; nothing in them is needed to run the product |

The dumps do not include the `admin` database, so Mongo users and roles are not exported. They are recreated from `.env` by `ops/mongo/init/` on every `mongo` start, including the insert-only audit role.

## Back up

Quiesce first. RabbitMQ is not part of the backup, so a message in flight at dump time would be lost. Wait until the queues are empty, then stop the writers.

```bash
TS=$(date -u +%Y%m%dT%H%M%SZ); B="$HOME/trustops-backups/$TS"; mkdir -p "$B"

docker compose exec -T rabbitmq rabbitmqctl -q list_queues name messages | awk '$2 != 0'   # expect no output
docker compose stop harness api

for db in fcrm_case fcrm_decision fcrm_harness fcrm_policy fcrm_scoring fcrm_identity fcrm_audit; do
  docker compose exec -T mongo sh -c "mongodump --uri 'mongodb://localhost:27017/?directConnection=true' --username \"\$MONGO_INITDB_ROOT_USERNAME\" --password \"\$MONGO_INITDB_ROOT_PASSWORD\" --authenticationDatabase admin --db $db --archive=/tmp/$db.archive.gz --gzip"
  docker compose cp mongo:/tmp/$db.archive.gz "$B/$db.archive.gz"
  docker compose exec -T mongo rm -f /tmp/$db.archive.gz
done

for b in uploads canonical packs vectors jobs; do
  docker compose exec -T minio sh -c "export MC_CONFIG_DIR=/tmp/.mc MC_HOST_fcrm=\"http://\${MINIO_ROOT_USER}:\${MINIO_ROOT_PASSWORD}@localhost:9000\"; mc mirror --overwrite fcrm/$b /tmp/backup/$b"
done
docker compose cp minio:/tmp/backup "$B/minio"
docker compose exec -T minio rm -rf /tmp/backup

docker compose start api harness        # about 85 s until api is healthy again
ls -la "$B"
```

`fcrm_evals` exists only after `make evals EVALS_ARGS="--publish"`. If you published, dump it with the same command (add it to the list). The run files are also in `evals/results/`, in git. `packs` is versioned in MinIO; `mc mirror` copies the current version of each object, not the history.

## Restore

Restore into a stack that is up and healthy. It replaces the data.

```bash
B="$HOME/trustops-backups/<timestamp>"         # the folder from the backup

docker compose stop harness api

for db in fcrm_case fcrm_decision fcrm_harness fcrm_policy fcrm_scoring fcrm_identity fcrm_audit; do
  docker compose cp "$B/$db.archive.gz" mongo:/tmp/$db.archive.gz
  docker compose exec -T mongo sh -c "mongorestore --uri 'mongodb://localhost:27017/?directConnection=true' --username \"\$MONGO_INITDB_ROOT_USERNAME\" --password \"\$MONGO_INITDB_ROOT_PASSWORD\" --authenticationDatabase admin --archive=/tmp/$db.archive.gz --gzip --nsInclude '$db.*' --drop"
  docker compose exec -T mongo rm -f /tmp/$db.archive.gz
done

docker compose cp "$B/minio" minio:/tmp/backup
for b in uploads canonical packs vectors jobs; do
  docker compose exec -T minio sh -c "export MC_CONFIG_DIR=/tmp/.mc MC_HOST_fcrm=\"http://\${MINIO_ROOT_USER}:\${MINIO_ROOT_PASSWORD}@localhost:9000\"; mc mirror --overwrite /tmp/backup/$b fcrm/$b"
done
docker compose exec -T minio rm -rf /tmp/backup

# RabbitMQ: start from empty queues, so no message newer than the restore point meets older data
docker compose rm -sf rabbitmq
docker volume rm trustops_rabbit_data
docker compose up -d rabbitmq

docker compose up -d api harness
make ps
```

`mongorestore --drop` drops each collection before restoring it and recreates it with its indexes and validator from the archive. For `fcrm_audit` this recreates `audit_records` with its `$jsonSchema` validator and unique `{case_id, seq}` index. The restore runs as the Mongo root user, which is the only user that may write to the audit collections other than by insert.

Then check the chain of every case you care about. `$A` is an analyst token; [verify-the-audit-chain.md](verify-the-audit-chain.md) shows how to get one.

```bash
curl -s -H "Authorization: Bearer $A" http://localhost:8080/api/fcrm/audit/verify/FCRM-2026-0118; echo   # {"ok":true,"record_count":9,...}
```

Starting a stack whose Mongo was replaced while the audit chain was written by an older hash format fails readiness with `ChainVerifyFailedError` (D-29). Restore from a backup made by the same build.

## Checked and not checked

- Run against throwaway Mongo 8.0 and MinIO containers (the images the compose file pins), without authentication: `mongodump --db <db> --archive --gzip` then `mongorestore --nsInclude '<db>.*' --drop` restored the rows, a unique index and a `$jsonSchema` validator of a test database; `mc mirror` out of a bucket and back in restored an object. The `mongodump`, `mongorestore` and `mc` binaries are in the `mongo:8.0` and MinIO images.
- Not run: the authenticated forms against the project stack, `docker compose cp` of the archives, the RabbitMQ volume step, and a full restore followed by `make ps`. `docker compose exec -T mongo sh -c ...` with credentials expanded in the container is the pattern `ops/scripts/seed_reset.py` uses.
- Every fenced command block in this folder passes `bash -n` (syntax only, nothing executed); the loops are plain `for ... in` over a literal list and work in bash and zsh.
