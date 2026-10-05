#!/usr/bin/env bash
# Entrypoint wrapper for the `minio` container (D2, design A §11.1).
#
# Starts the MinIO server, runs ops/minio/init.sh against it once it answers
# (the buckets and packs versioning; idempotent, so safe on every start), then
# writes the readiness marker the healthcheck waits for. `healthy` therefore
# means "buckets exist", so dependants need no separate init container.
set -euo pipefail

READY_MARKER=/tmp/fcrm-minio-init.done
rm -f "${READY_MARKER}"

minio "$@" &
minio_pid=$!
trap 'kill -TERM "${minio_pid}" 2>/dev/null || true' TERM INT

# init.sh waits for the server itself (30 attempts) and exits non-zero if it
# never answers; set -e then ends this script and the container restarts.
MINIO_URL_HOST=localhost:9000 bash /opt/fcrm/init.sh
touch "${READY_MARKER}"

wait "${minio_pid}"
