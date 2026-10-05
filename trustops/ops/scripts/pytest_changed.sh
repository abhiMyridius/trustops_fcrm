#!/usr/bin/env bash
# pre-commit hook (.pre-commit-config.yaml `pytest-unit`): run the unit
# tests that mirror the staged source units (tests/unit/<src path>, ADR-21)
# plus any staged unit-test file. Runs every unit test when a shared file
# (tests/conftest.py, pytest.ini, src/backend/pyproject.toml, src/backend/uv.lock) changed.
# Written for the macOS system bash (3.2).
set -eo pipefail
cd "$(dirname "$0")/../.."   # the project root (trustops/), not the git root (ADR-25)
export PATH="${HOME}/.local/bin:${PATH}"

targets=""
run_all=0
while IFS= read -r file; do
  [ -n "${file}" ] || continue
  case "${file}" in
    tests/conftest.py|pytest.ini|src/backend/pyproject.toml|src/backend/uv.lock)
      run_all=1 ;;
    src/backend/services/*|src/backend/workers/*|src/backend/packages/*|src/backend/apps/*)
      unit="$(printf '%s' "${file}" | cut -d/ -f3-4)"      # e.g. services/scoring
      [ -d "tests/unit/${unit}" ] && targets="${targets}"$'\n'"tests/unit/${unit}" ;;
    tests/unit/*.py)
      targets="${targets}"$'\n'"${file}" ;;
  esac
done <<EOF
$(git diff --cached --name-only --relative --diff-filter=ACMR || true)
EOF

if [ "${run_all}" -eq 1 ]; then
  exec uv run --project src/backend --all-packages pytest -x tests/unit
fi
unique="$(printf '%s\n' "${targets}" | sed '/^$/d' | sort -u | tr '\n' ' ')"
if [ -z "${unique}" ]; then
  echo "pytest_changed: no unit with tests changed"
  exit 0
fi
# shellcheck disable=SC2086  # unique is a space-separated path list
exec uv run --project src/backend --all-packages pytest -x ${unique}
