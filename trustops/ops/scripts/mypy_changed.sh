#!/usr/bin/env bash
# pre-commit hook (.pre-commit-config.yaml `mypy`): type-check the units
# whose Python files are staged; every unit when nothing under src/backend/ is
# staged but the hook still runs. mypy.ini decides strictness per package.
# Written for the macOS system bash (3.2): no mapfile, no empty-array
# expansion under `set -u`.
set -eo pipefail
cd "$(dirname "$0")/../.."   # the project root (trustops/), not the git root (ADR-25)
export PATH="${HOME}/.local/bin:${PATH}"

staged="$(git diff --cached --name-only --relative --diff-filter=ACMR -- 'src/backend/*.py' || true)"

args=""
for unit in src/backend/services/* src/backend/workers/* src/backend/packages/* src/backend/apps/*; do
  [ -d "${unit}" ] || continue
  if [ -n "${staged}" ] && ! printf '%s\n' "${staged}" | grep -q "^${unit}/"; then
    continue
  fi
  for pkg in "${unit}"/fcrm_* "${unit}"/evoq_*; do
    [ -f "${pkg}/__init__.py" ] && args="${args} -p $(basename "${pkg}")"
  done
done

if [ -z "${args}" ]; then
  echo "mypy_changed: no Python unit changed"
  exit 0
fi
# shellcheck disable=SC2086  # args is a list of "-p name" pairs
exec uv run --project src/backend --all-packages mypy ${args}
