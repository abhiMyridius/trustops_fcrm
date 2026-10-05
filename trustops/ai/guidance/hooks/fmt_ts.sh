#!/usr/bin/env bash
# PostToolUse hook for Edit|Write|MultiEdit on *.ts, *.tsx, *.css (design C §5.4).
#
# For a file under src/web:
#   1. oxlint --fix <file> (ts/tsx only; oxlint does not lint CSS)
#   2. tsc -b --incremental for the web project (ts/tsx only)
# Errors left after --fix, or type errors, -> exit 2 with the first 40 lines
# on stderr. Tools resolve from src/web/node_modules/.bin, then PATH; if
# neither has them (no Node on the host yet), the step is skipped with a note.
#
# Standalone: echo '{"tool_name":"Edit","tool_input":{"file_path":"src/web/src/app/App.tsx"}}' | ai/guidance/hooks/fmt_ts.sh
set -u

payload="$(cat)"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="${CLAUDE_PROJECT_DIR:-$(cd "$here/../../.." && pwd)}"
web="$root/src/web"

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
  *.ts|*.tsx|*.css) ;;
  *) exit 0 ;;
esac
case "$file" in
  /*) ;;
  *) file="$root/$file" ;;
esac
[ -f "$file" ] || exit 0
case "$file" in
  "$web"/*) ;;
  *) exit 0 ;;
esac

bin() {  # bin <tool> -> path or nothing
  if [ -x "$web/node_modules/.bin/$1" ]; then printf '%s' "$web/node_modules/.bin/$1"; return; fi
  command -v "$1" 2>/dev/null || true
}

case "$file" in
  *.css) exit 0 ;;  # nothing to lint for CSS with the current toolchain; tokens are checked by tests
esac

problems=""
oxlint="$(bin oxlint)"
if [ -n "$oxlint" ]; then
  out="$(cd "$web" && "$oxlint" --fix "$file" 2>&1)"
  rc=$?
  [ $rc -ne 0 ] && problems="$problems
oxlint (${file#"$root"/}):
$out"
else
  echo "fmt_ts: oxlint not available; skipped lint for ${file#"$root"/}" >&2
fi

tsc="$(bin tsc)"
if [ -n "$tsc" ] && [ -f "$web/tsconfig.json" ]; then
  out="$(cd "$web" && "$tsc" -b --incremental --pretty false 2>&1)"
  rc=$?
  [ $rc -ne 0 ] && problems="$problems
tsc -b:
$out"
else
  echo "fmt_ts: tsc or src/web/tsconfig.json not available; skipped type check" >&2
fi

if [ -n "$problems" ]; then
  printf '%s\n' "$problems" | sed '/^$/d' | head -n 40 >&2
  exit 2
fi
exit 0
