#!/usr/bin/env bash
# PostToolUse hook for Edit|Write|MultiEdit on *.py (design C §5.4).
#
# Reads the hook payload on stdin, and for a Python file under the repo:
#   1. ruff format <file>
#   2. ruff check --fix <file>; remaining errors -> exit 2 with them on stderr
#      (Claude sees them and fixes the file; the edit itself already happened)
#   3. mypy on the file's unit (nearest pyproject.toml under src/) in the
#      background, result appended to .cache/mypy.txt
# Tools resolve as: `uv run --project src`, then src/.venv/bin, then PATH;
# a missing tool is skipped with a note, never a failure.
#
# Standalone: echo '{"tool_name":"Write","tool_input":{"file_path":"src/x.py"}}' | ai/guidance/hooks/fmt_py.sh
set -u

payload="$(cat)"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="${CLAUDE_PROJECT_DIR:-$(cd "$here/../../.." && pwd)}"

file="$(printf '%s' "$payload" | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except ValueError:
    sys.exit(0)
args = data.get("tool_input") or data.get("input") or {}
sys.stdout.write(str(args.get("file_path") or args.get("path") or ""))
' 2>/dev/null)"

case "$file" in
  *.py) ;;
  *) exit 0 ;;
esac
case "$file" in
  /*) ;;
  *) file="$root/$file" ;;
esac
[ -f "$file" ] || exit 0

resolve() {  # resolve <tool> -> prints the command prefix or nothing
  local tool="$1" uv=""
  uv="$(command -v uv 2>/dev/null || true)"
  [ -z "$uv" ] && [ -x "$HOME/.local/bin/uv" ] && uv="$HOME/.local/bin/uv"
  if [ -n "$uv" ] && [ -f "$root/src/pyproject.toml" ]; then
    printf '%s run --project %s --quiet %s' "$uv" "$root/src" "$tool"; return
  fi
  if [ -x "$root/src/.venv/bin/$tool" ]; then printf '%s' "$root/src/.venv/bin/$tool"; return; fi
  command -v "$tool" 2>/dev/null || true
}

ruff_cmd="$(resolve ruff)"
if [ -z "$ruff_cmd" ]; then
  echo "fmt_py: ruff not available; skipped $file" >&2
  exit 0
fi

cd "$root" || exit 0
$ruff_cmd format --quiet "$file" >/dev/null 2>&1
lint_out="$($ruff_cmd check --fix --quiet "$file" 2>&1)"
lint_rc=$?

mypy_cmd="$(resolve mypy)"
if [ -n "$mypy_cmd" ]; then
  unit="$(dirname "$file")"
  while [ "$unit" != "$root" ] && [ "$unit" != "/" ] && [ ! -f "$unit/pyproject.toml" ]; do
    unit="$(dirname "$unit")"
  done
  if [ -f "$unit/pyproject.toml" ] && [ "$unit" != "$root/src" ]; then
    mkdir -p "$root/.cache"
    (
      out="$($mypy_cmd "$unit" 2>&1 | tail -n 20)"
      printf '%s %s\n%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${unit#"$root"/}" "$out" >> "$root/.cache/mypy.txt"
    ) >/dev/null 2>&1 &
  fi
fi

if [ $lint_rc -ne 0 ]; then
  {
    echo "ruff check still reports problems in ${file#"$root"/} after --fix:"
    printf '%s\n' "$lint_out" | head -n 40
  } >&2
  exit 2
fi
exit 0
