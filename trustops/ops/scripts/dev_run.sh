#!/usr/bin/env bash
# Dev entrypoint used by compose.dev.yaml (design A §11.1 ENTRYPOINT notes).
#
#   dev_run.sh http   <module:app> <port>   uvicorn --reload       (S1–S5, X1)
#   dev_run.sh worker <module>              python -m <module>,     (H1–H4)
#                                           restarted on change when
#                                           watchfiles is installed
#
# The repo's src/backend is bind-mounted at /workspace/src. Every unit folder
# (services/*, workers/*, packages/*, apps/*) is put on PYTHONPATH ahead of the
# image's site-packages, so the mounted source shadows the copy baked into
# /app/.venv and an edit on the host is picked up without a rebuild. New
# third-party dependencies still need `docker compose build <svc>`.
set -euo pipefail

SRC_ROOT="${DEV_SRC_ROOT:-/workspace/src}"
PYTHON="${DEV_PYTHON:-python}"

pythonpath=""
for unit in "${SRC_ROOT}"/services/* "${SRC_ROOT}"/workers/* "${SRC_ROOT}"/packages/* "${SRC_ROOT}"/apps/*; do
  [ -d "${unit}" ] || continue
  pythonpath="${pythonpath:+${pythonpath}:}${unit}"
done
export PYTHONPATH="${pythonpath}${PYTHONPATH:+:${PYTHONPATH}}"

mode="${1:?usage: dev_run.sh http <module:app> <port> | worker <module>}"
shift

case "${mode}" in
  http)
    app="${1:?module:app required}"
    port="${2:?port required}"
    exec "${PYTHON}" -m uvicorn "${app}" \
      --host 0.0.0.0 --port "${port}" \
      --reload --reload-dir "${SRC_ROOT}" \
      --proxy-headers --forwarded-allow-ips '*'
    ;;
  worker)
    module="${1:?module required}"
    if "${PYTHON}" -c "import watchfiles" >/dev/null 2>&1; then
      exec "${PYTHON}" -m watchfiles --filter python "${PYTHON} -m ${module}" "${SRC_ROOT}"
    fi
    echo "dev_run.sh: watchfiles not installed in the image; running ${module} without reload" >&2
    exec "${PYTHON}" -m "${module}"
    ;;
  *)
    echo "dev_run.sh: unknown mode ${mode}" >&2
    exit 2
    ;;
esac
