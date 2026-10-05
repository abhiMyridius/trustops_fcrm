#!/usr/bin/env bash
# Entrypoint wrapper for the `mongo` container (D1, design A §11.1).
#
# 1. MongoDB refuses to run a replica set with authorization enabled unless
#    the members share a keyFile, even with one member, so a fresh random
#    keyFile is written (a single-node set has no peer that must agree).
# 2. The official entrypoint then starts mongod (creating the root user from
#    MONGO_INITDB_ROOT_* on first boot, on a temporary instance that has no
#    replica set) and drops to the mongodb user.
# 3. Once the real mongod answers, ops/mongo/init/run.sh runs: replica set,
#    users (insert-only audit writer), validators and indexes. Every script
#    there is idempotent, so this is safe on every container start.
# 4. Only then is the readiness marker written, which is what the
#    healthcheck waits for: `healthy` means "initialised", so dependants need
#    no separate init container.
set -euo pipefail

RUN_DIR=/run/fcrm-mongo
KEYFILE="${RUN_DIR}/keyfile"
READY_MARKER="${RUN_DIR}/init.done"
INIT_TIMEOUT_S=120

install -d -m 0700 -o mongodb -g mongodb "${RUN_DIR}"
rm -f "${READY_MARKER}"
head -c 756 /dev/urandom | base64 -w 0 > "${KEYFILE}"
chown mongodb:mongodb "${KEYFILE}"
chmod 0400 "${KEYFILE}"

docker-entrypoint.sh mongod \
  --replSet rs0 \
  --bind_ip_all \
  --keyFile "${KEYFILE}" \
  "$@" &
mongod_pid=$!
# Forward a stop request so mongod shuts down cleanly (docker stop sends TERM
# to this script, which is PID 1).
trap 'kill -TERM "${mongod_pid}" 2>/dev/null || true' TERM INT

# The root user is created on a temporary mongod that has no replica set;
# wait for the real one, which accepts the root credentials and reports
# itself as a replica-set member: `isreplicaset` before rs.initiate has run
# (first boot), `setName` once the set exists (every later start).
for attempt in $(seq 1 "${INIT_TIMEOUT_S}"); do
  if ! kill -0 "${mongod_pid}" 2>/dev/null; then
    echo "mongo: mongod exited before it became ready" >&2
    exit 1
  fi
  if mongosh --quiet --norc "mongodb://localhost:27017/admin?directConnection=true" \
      --username "${MONGO_INITDB_ROOT_USERNAME}" \
      --password "${MONGO_INITDB_ROOT_PASSWORD}" \
      --authenticationDatabase admin \
      --eval "const h = db.hello(); quit(h.isreplicaset === true || typeof h.setName === 'string' ? 0 : 1)" >/dev/null 2>&1; then
    break
  fi
  if [ "${attempt}" -eq "${INIT_TIMEOUT_S}" ]; then
    echo "mongo: not ready for init after ${INIT_TIMEOUT_S}s" >&2
    kill -TERM "${mongod_pid}" 2>/dev/null || true
    exit 1
  fi
  sleep 1
done

MONGO_HOST=localhost:27017 bash /opt/fcrm/init/run.sh
touch "${READY_MARKER}"

wait "${mongod_pid}"
