"""Stop hook: the commit gate G-C1 runs before Claude may finish a turn.

If the root Makefile has a ``check`` target, runs ``make check``. Otherwise
(until every lane has wired it) runs the equivalent steps directly:

* ``ruff check`` and ``ruff format --check`` over ``src`` and ``tests``;
* ``mypy`` on every unit under ``src/{packages,services,workers}`` that has a
  ``pyproject.toml`` (config from the root ``mypy.ini``);
* ``pytest -q tests/unit/<unit path>`` per unit, under
  ``uv run --package <name>`` so each unit's own dependencies are installed
  (an unscoped pytest would collect other units' imports and fail);
* ``ai/_tools/compute_locks.py --check`` (registry locks match ai/).

A step whose tool is not installed is reported as skipped, not failed. On
failure: ``{"decision": "block", "reason": <first 40 lines>}`` and exit 2, so
Claude keeps fixing. On success: touch ``.cache/check.ok`` (read by
commit_guard.py) and append one line to ``.cache/session-log.jsonl``.
After three consecutive blocks inside one stop cycle (``stop_hook_active``)
it lets the turn end with a warning, so a broken environment cannot loop.

Run standalone: ``python3 ai/guidance/hooks/stop_gate.py <<< '{}'``.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _hooklib import (
    allow,
    block,
    cache_dir,
    find_uv,
    read_payload,
    repo_root,
    tool_command,
)  # noqa: E402

STEP_TIMEOUT_S = 300
MAX_LINES = 40
MAX_CONSECUTIVE_BLOCKS = 3
UNIT_PARENTS = ("packages", "services", "workers")
NAME_RE = re.compile(r'^\s*name\s*=\s*"(?P<name>[^"]+)"', re.MULTILINE)


class Step:
    """One gate step: a label, a command and its result."""

    def __init__(self, label: str, command: Optional[list[str]]) -> None:
        self.label = label
        self.command = command
        self.status = "skipped" if command is None else "pending"
        self.output = ""

    def run(self, cwd: Path) -> None:
        """Execute the command, capturing status and output."""
        if self.command is None:
            return
        try:
            proc = subprocess.run(
                self.command,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=STEP_TIMEOUT_S,
                check=False,
            )
        except subprocess.TimeoutExpired:
            self.status, self.output = "failed", f"timed out after {STEP_TIMEOUT_S} s"
            return
        except OSError as exc:
            self.status, self.output = "skipped", f"could not start: {exc}"
            return
        self.output = (proc.stdout + proc.stderr).strip()
        if proc.returncode == 0:
            self.status = "passed"
        elif "No module named" in self.output and "yaml" in self.output:
            self.status = "skipped"
        else:
            self.status = "failed"


def has_make_check(root: Path) -> bool:
    """True if the root Makefile defines a `check` target."""
    makefile = root / "Makefile"
    if not makefile.exists():
        return False
    return (
        re.search(r"^check\s*:", makefile.read_text(encoding="utf-8"), re.MULTILINE)
        is not None
    )


def units(root: Path) -> list[Path]:
    """Every Python unit directory (has a pyproject.toml) under src/."""
    found = []
    for parent in UNIT_PARENTS:
        base = root / "src" / parent
        if base.is_dir():
            found += sorted(
                p for p in base.iterdir() if (p / "pyproject.toml").exists()
            )
    return found


def import_packages(unit: Path) -> list[str]:
    """Top-level import packages of a unit (fcrm_* and cloned evoq_* folders)."""
    return sorted(
        p.name
        for p in unit.iterdir()
        if p.is_dir()
        and p.name.startswith(("fcrm_", "evoq_"))
        and (p / "__init__.py").exists()
    )


def unit_name(unit: Path) -> Optional[str]:
    """The [project] name from a unit's pyproject.toml."""
    match = NAME_RE.search((unit / "pyproject.toml").read_text(encoding="utf-8"))
    return match.group("name") if match else None


def unit_tool(
    root: Path, uv: Optional[str], name: Optional[str], tool: str
) -> Optional[list[str]]:
    """Command prefix that runs `tool` with one unit's own dependencies.

    `uv run --package <name>` installs that unit (and its dev group) into the
    workspace venv first; without it a fresh venv has no workspace members and
    `mypy -p fcrm_x` or the unit's tests cannot import the package.
    """
    if uv and name:
        return [uv, "run", "--project", "src", "--quiet", "--package", name, tool]
    return tool_command(root, tool)


def direct_steps(root: Path) -> list[Step]:
    """The make-check equivalent, built from what exists in the tree."""
    targets = [p for p in ("src", "tests") if (root / p).exists()]
    ruff = tool_command(root, "ruff")
    steps = [
        Step("ruff check", ruff + ["check", *targets] if ruff else None),
        Step(
            "ruff format --check",
            ruff + ["format", "--check", *targets] if ruff else None,
        ),
    ]
    uv = find_uv()
    for unit in units(root):
        rel = unit.relative_to(root).as_posix()
        name = unit_name(unit)
        packages = import_packages(unit)
        mypy = unit_tool(root, uv, name, "mypy")
        if mypy and packages and (root / "mypy.ini").exists():
            # Package mode: path mode with mypy.ini's mypy_path=src finds each
            # file twice (packages.contracts.fcrm_contracts vs fcrm_contracts).
            args = [arg for pkg in packages for arg in ("-p", pkg)]
            steps.append(Step(f"mypy {rel}", mypy + args))
        tests = root / "tests" / "unit" / unit.relative_to(root / "src")
        if tests.is_dir() and any(tests.rglob("test_*.py")):
            test_rel = tests.relative_to(root).as_posix()
            pytest = unit_tool(root, uv, name, "pytest")
            command = pytest + ["-q", test_rel] if pytest else None
            steps.append(Step(f"pytest {test_rel}", command))
    locks = root / "ai" / "_tools" / "compute_locks.py"
    if locks.exists():
        python = (
            [uv, "run", "--project", "src", "--quiet", "python"]
            if uv
            else [sys.executable]
        )
        steps.append(
            Step(
                "ai registry locks",
                python + [locks.relative_to(root).as_posix(), "--check"],
            )
        )
    return steps


def summarise(steps: list[Step]) -> str:
    """First MAX_LINES lines of a failure report."""
    failed = sum(s.status == "failed" for s in steps)
    lines = [f"make check equivalent failed ({failed} step(s)):"]
    for step in steps:
        lines.append(f"[{step.status}] {step.label}")
    for step in steps:
        if step.status == "failed":
            lines.append(f"--- {step.label} ---")
            lines.extend(step.output.splitlines()[:MAX_LINES])
    return "\n".join(lines[:MAX_LINES])


def counter(root: Path, reset: bool = False) -> int:
    """Consecutive-block counter in .cache; returns the new value."""
    path = cache_dir(root) / "stop_gate.blocks"
    if reset:
        path.write_text("0", encoding="utf-8")
        return 0
    try:
        value = int(path.read_text(encoding="utf-8").strip() or "0") + 1
    except (OSError, ValueError):
        value = 1
    path.write_text(str(value), encoding="utf-8")
    return value


def record_success(
    root: Path, payload: dict[str, Any], steps: list[Step], started: float
) -> None:
    """Touch .cache/check.ok and append the session-log line."""
    cache = cache_dir(root)
    (cache / "check.ok").write_text(
        time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), encoding="utf-8"
    )
    status = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=False,
    )
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "session_id": payload.get("session_id"),
        "event": payload.get("hook_event_name", "Stop"),
        "duration_s": round(time.time() - started, 1),
        "files_touched": len(status.stdout.splitlines()),
        "steps": {s.label: s.status for s in steps},
    }
    with (cache / "session-log.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")


def main() -> None:
    """Entry point."""
    payload = read_payload()
    root = repo_root(payload)
    started = time.time()
    if has_make_check(root):
        steps = [Step("make check", ["make", "check"])]
    else:
        steps = direct_steps(root)
    for step in steps:
        step.run(root)
    if any(s.status == "failed" for s in steps):
        blocks = counter(root)
        reason = summarise(steps)
        if payload.get("stop_hook_active") and blocks > MAX_CONSECUTIVE_BLOCKS:
            counter(root, reset=True)
            allow(
                f"stop_gate: still failing after {MAX_CONSECUTIVE_BLOCKS} attempts; "
                f"letting the turn end.\n{reason}"
            )
        block(reason, event="Stop")
    counter(root, reset=True)
    record_success(root, payload, steps, started)
    skipped = [s.label for s in steps if s.status == "skipped"]
    allow(
        f"stop_gate: green; skipped (tool missing): {', '.join(skipped)}"
        if skipped
        else None
    )


if __name__ == "__main__":
    main()
