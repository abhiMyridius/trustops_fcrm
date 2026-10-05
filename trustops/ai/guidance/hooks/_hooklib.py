"""Shared helpers for the Claude Code hook scripts in this folder.

Stdlib only and Python 3.9 compatible: hooks run under whatever ``python3`` is
on the PATH (macOS ships 3.9), not the uv environment.

Protocol assumed (Claude Code hooks, as documented for command hooks):

* the hook receives one JSON object on stdin with at least
  ``hook_event_name`` and ``cwd``; tool hooks add ``tool_name`` and
  ``tool_input`` (``file_path``/``content``/``old_string``/``new_string``/
  ``edits`` for Edit, Write and MultiEdit; ``command`` for Bash); Stop and
  SubagentStop add ``stop_hook_active`` and ``transcript_path``;
* exit 0 = proceed (stdout of a UserPromptSubmit hook is added as context);
* exit 2 = block; stderr is shown to Claude as the reason.

Every blocking hook here also writes ``{"decision": "block", "reason": ...}``
(and, for PreToolUse, the ``hookSpecificOutput.permissionDecision = "deny"``
form) to stdout, so a runner that reads JSON instead of exit codes gets the
same answer. Fields are read defensively because the payload has changed
across Claude Code versions.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Any, NoReturn, Optional

logger = logging.getLogger(__name__)

REPO_MARKERS = ("BUILD-PLAN.md", "CLAUDE.md")


def read_payload() -> dict[str, Any]:
    """Read the hook payload from stdin; an empty or invalid body gives {}."""
    try:
        raw = sys.stdin.read()
    except OSError as exc:
        logger.warning("could not read stdin: %s", exc)
        return {}
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("stdin is not JSON: %s", exc)
        return {}
    return data if isinstance(data, dict) else {}


def tool_input(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the tool_input mapping (older payloads used `input`)."""
    value = payload.get("tool_input", payload.get("input", {}))
    return value if isinstance(value, dict) else {}


def repo_root(payload: Optional[dict[str, Any]] = None) -> Path:
    """Find the repository root: CLAUDE_PROJECT_DIR, else walk up from cwd."""
    env_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    starts = [Path(env_dir)] if env_dir else []
    if payload and isinstance(payload.get("cwd"), str):
        starts.append(Path(payload["cwd"]))
    starts.append(Path.cwd())
    for start in starts:
        for candidate in [start, *start.resolve().parents]:
            if all((candidate / marker).exists() for marker in REPO_MARKERS):
                return candidate
    return Path.cwd()


def relative_to_repo(path_str: str, root: Path) -> str:
    """Repository-relative POSIX path, or the absolute path if outside."""
    path = Path(path_str)
    if not path.is_absolute():
        path = root / path
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def block(reason: str, event: str = "PreToolUse") -> NoReturn:
    """Block the action: JSON on stdout, reason on stderr, exit 2."""
    out: dict[str, Any] = {"decision": "block", "reason": reason}
    if event == "PreToolUse":
        out["hookSpecificOutput"] = {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    sys.stdout.write(json.dumps(out) + "\n")
    sys.stderr.write(reason + "\n")
    sys.exit(2)


def allow(note: Optional[str] = None) -> NoReturn:
    """Let the action proceed, optionally with a note on stderr."""
    if note:
        sys.stderr.write(note + "\n")
    sys.exit(0)


def find_uv() -> Optional[str]:
    """Locate uv on PATH or in the user-space install location."""
    found = shutil.which("uv")
    if found:
        return found
    candidate = Path.home() / ".local" / "bin" / "uv"
    return str(candidate) if candidate.exists() else None


def tool_command(root: Path, tool: str) -> Optional[list[str]]:
    """Command prefix for a dev tool: uv run, then src/backend/.venv, then PATH."""
    uv = find_uv()
    if uv and (root / "src" / "backend" / "pyproject.toml").exists():
        return [uv, "run", "--project", str(root / "src" / "backend"), "--quiet", tool]
    venv_tool = root / "src" / "backend" / ".venv" / "bin" / tool
    if venv_tool.exists():
        return [str(venv_tool)]
    on_path = shutil.which(tool)
    return [on_path] if on_path else None


def cache_dir(root: Path) -> Path:
    """The git-ignored .cache folder at the repository root."""
    path = root / ".cache"
    path.mkdir(parents=True, exist_ok=True)
    return path
