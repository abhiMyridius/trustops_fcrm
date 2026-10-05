#!/usr/bin/env bash
# Initialiser for the buckets, run by ops/minio/entrypoint.sh inside the `minio` container (design A §11.1).
#
# Creates the five buckets and turns on versioning for `packs` (frozen
# committee packs: an overwrite must never lose the version a vote was cast
# on). Idempotent: existing buckets are kept, versioning re-asserted.
#
#   uploads    original PO documents (never read by an agent)
#   canonical  Guard-1-masked canonical Markdown per document
#   packs      frozen pack snapshots (versioned)
#   vectors    vector manifests and memory-backend fixtures (H4)
#   jobs       large agent.run payloads and raw outputs for handoff (H1/H2)
#
# Credentials come in through the environment and are handed to mc as
# MC_HOST_fcrm, never on a command line.
set -euo pipefail

: "${MINIO_ROOT_USER:?MINIO_ROOT_USER is required}"
: "${MINIO_ROOT_PASSWORD:?MINIO_ROOT_PASSWORD is required}"

MINIO_URL_HOST="${MINIO_URL_HOST:-minio:9000}"
BUCKETS=(
  "${S3_BUCKET_UPLOADS:-uploads}"
  "${S3_BUCKET_CANONICAL:-canonical}"
  "${S3_BUCKET_PACKS:-packs}"
  "${S3_BUCKET_VECTORS:-vectors}"
  "${S3_BUCKET_JOBS:-jobs}"
)
VERSIONED_BUCKET="${S3_BUCKET_PACKS:-packs}"

export MC_CONFIG_DIR="${MC_CONFIG_DIR:-/tmp/.mc}"
export MC_HOST_fcrm="http://${MINIO_ROOT_USER}:${MINIO_ROOT_PASSWORD}@${MINIO_URL_HOST}"
mc_() { mc --config-dir "${MC_CONFIG_DIR}" "$@"; }

for attempt in $(seq 1 30); do
  if mc_ ready fcrm >/dev/null 2>&1; then
    break
  fi
  if [ "${attempt}" -eq 30 ]; then
    echo "minio-init: MinIO not ready after 30 attempts" >&2
    exit 1
  fi
  sleep 1
done

for bucket in "${BUCKETS[@]}"; do
  mc_ mb --ignore-existing "fcrm/${bucket}"
  # Private buckets only: every object is reached through a presigned URL.
  mc_ anonymous set none "fcrm/${bucket}" >/dev/null
done

mc_ version enable "fcrm/${VERSIONED_BUCKET}"

echo "minio-init: buckets"
mc_ ls fcrm
echo "minio-init: versioning on ${VERSIONED_BUCKET}"
mc_ version info "fcrm/${VERSIONED_BUCKET}"
