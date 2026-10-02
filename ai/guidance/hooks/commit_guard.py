"""PreToolUse hook on Bash: gate `git commit` (G-C1, design C §5.4-5.5).

When the Bash command runs ``git commit`` this hook blocks unless:

1. ``.cache/check.ok`` exists and is newer than every changed file in the
   working tree (``make check`` or the Stop hook writes it when green);
2. the message follows ``ai/guidance/commit-convention.md``:
   ``type(scope): summary [D-xx, ADR-nn]`` as the first line, at least one
   ``D-xx`` or ``ADR-nn`` reference, and ``Stage:`` and ``Gate:`` trailers.

The message is read from ``-m``/``--message`` (repeatable), ``-F``/``--file``,
or a ``$(cat <<'EOF' ... EOF)`` heredoc. ``--amend --no-edit`` skips the
message check. Every other Bash command passes through untouched.

Run standalone: ``python3 ai/guidance/hooks/commit_guard.py < payload.json``.
"""

from __future__ import annotations

import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _hooklib import allow, block, read_payload, repo_root, tool_input  # noqa: E402

TYPES = (
    "feat",
    "fix",
    "test",
    "docs",
    "ops",
    "ai",
    "eval",
    "refactor",
    "chore",
    "seed",
)
STAGES = (
    "requirements",
    "design",
    "development",
    "testing",
    "deployment",
    "operations",
)
HEADER_RE = re.compile(
    rf"^(?P<type>{'|'.join(TYPES)})"
    r"\((?P<scope>[a-z0-9][a-z0-9-]*)\)!?: (?P<summary>\S.*)$"
)
REF_RE = re.compile(r"\b(D-\d{2,3}[a-z]?|ADR-\d{2})\b")
STAGE_RE = re.compile(
    rf"^Stage:\s*(?P<stage>{'|'.join(STAGES)})\b", re.IGNORECASE | re.MULTILINE
)
GATE_RE = re.compile(r"^Gate:\s*\S", re.MULTILINE)
DEF_RE = re.compile(r"\bDEF-\d{3}\b")
GIT_COMMIT_RE = re.compile(r"(^|[;&|(]\s*|\s)git(\s+-C\s+\S+|\s+-c\s+\S+)*\s+commit\b")
HEREDOC_RE = re.compile(
    r"<<-?\s*['\"]?(?P<tag>\w+)['\"]?\s*\n(?P<body>.*?)\n\s*(?P=tag)\s*(\n|$)",
    re.DOTALL,
)
SUMMARY_MAX = 72
HEADER_MAX = 100


def extract_message(command: str, root: Path) -> Optional[str]:
    """The commit message given on the command line, or None."""
    heredoc = HEREDOC_RE.search(command)
    if heredoc and re.search(r"(-m|--message|-F|--file)[=\s]+\"?\$\(cat", command):
        return heredoc.group("body")
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return None
    messages = []
    for i, token in enumerate(tokens):
        if token in ("-m", "--message") and i + 1 < len(tokens):
            messages.append(tokens[i + 1])
        elif token.startswith("--message="):
            messages.append(token.split("=", 1)[1])
        elif token.startswith("-m") and len(token) > 2 and not token.startswith("--"):
            messages.append(token[2:])
        elif token in ("-F", "--file") and i + 1 < len(tokens):
            path = Path(tokens[i + 1])
            path = path if path.is_absolute() else root / path
            if path.exists():
                messages.append(path.read_text(encoding="utf-8"))
    return "\n\n".join(messages) if messages else None


def message_problems(message: str) -> list[str]:
    """Every way the message breaks the commit convention."""
    lines = message.strip().splitlines()
    header = lines[0].strip() if lines else ""
    problems = []
    match = HEADER_RE.match(header)
    if not match:
        problems.append(
            "first line must be `type(scope): summary [D-xx, ADR-nn]` with type in "
            f"{', '.join(TYPES)}; got `{header[:80]}`"
        )
    else:
        summary = re.sub(r"\s*\[[^\]]*\]\s*$", "", match.group("summary"))
        if len(summary) > SUMMARY_MAX:
            problems.append(
                f"summary is {len(summary)} characters; keep it to {SUMMARY_MAX}"
            )
    if len(header) > HEADER_MAX:
        problems.append(
            f"first line is {len(header)} characters; keep it under {HEADER_MAX}"
        )
    if not REF_RE.search(message):
        problems.append(
            "reference at least one decision: D-xx or ADR-nn "
            "(in the [..] on line 1 or a Refs: trailer)"
        )
    if not STAGE_RE.search(message):
        stages = ", ".join(s.capitalize() for s in STAGES)
        problems.append(f"add a `Stage:` trailer, one of: {stages}")
    if not GATE_RE.search(message):
        problems.append(
            "add a `Gate:` trailer naming the gate passed, e.g. `Gate: G-C1 make check`"
        )
    return problems


def changed_files(root: Path) -> list[Path]:
    """Files changed, staged or untracked in the working tree."""
    proc = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"],
        capture_output=True,
        text=True,
        check=False,
    )
    files = []
    for line in proc.stdout.splitlines():
        name = line[3:].split(" -> ")[-1].strip().strip('"')
        path = root / name
        if path.is_file():
            files.append(path)
    return files


def freshness_problem(root: Path) -> Optional[str]:
    """Why .cache/check.ok does not cover the current tree, or None."""
    marker = root / ".cache" / "check.ok"
    if not marker.exists():
        return (
            "no .cache/check.ok: run `make check` (it writes the marker when green) "
            "before committing"
        )
    stamp = marker.stat().st_mtime
    newer = [p for p in changed_files(root) if p.stat().st_mtime > stamp]
    if newer:
        names = ", ".join(sorted(p.relative_to(root).as_posix() for p in newer)[:5])
        return (
            f"files changed after the last green `make check` ({names}); "
            "run it again before committing"
        )
    return None


def main() -> None:
    """Entry point."""
    payload = read_payload()
    if payload.get("tool_name") not in (None, "Bash"):
        allow()
    command = str(tool_input(payload).get("command", ""))
    if not GIT_COMMIT_RE.search(command):
        allow()
    root = repo_root(payload)
    reasons = []
    stale = freshness_problem(root)
    if stale:
        reasons.append(stale)
    no_edit = "--no-edit" in command and "--amend" in command
    message = extract_message(command, root)
    if message is None and not no_edit:
        reasons.append(
            "pass the commit message with -m, -F or a heredoc "
            "so the convention can be checked"
        )
    elif message is not None:
        reasons.extend(message_problems(message))
        if message.startswith("fix(") and not DEF_RE.search(message):
            sys.stderr.write(
                "note: a fix commit should reference its DEF-nnn row "
                "(make gate enforces it)\n"
            )
    if reasons:
        block(
            "commit blocked (G-C1):\n- "
            + "\n- ".join(reasons)
            + "\nSee ai/guidance/commit-convention.md."
        )
    allow()


if __name__ == "__main__":
    main()
