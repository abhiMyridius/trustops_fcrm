"""``ai/guidance/hooks/settings.example.json``: the registrations of the hook scripts.

No ``.claude/settings.json`` is tracked (one would change every contributor's Claude
Code session), so the example is how a contributor enables the hooks. These tests
keep it honest: it parses, every script it names exists and is executable, every
script in the folder is registered, and the guards that are pure answer a plain
payload. Whether Claude Code fires a registration is not something this repository
can prove.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[4]
HOOKS = ROOT / "ai" / "guidance" / "hooks"
EXAMPLE = HOOKS / "settings.example.json"
SCRIPT_RE = re.compile(r"\$CLAUDE_PROJECT_DIR/ai/guidance/hooks/(?P<name>[\w.]+)")
EVENTS = {
    "PreToolUse",
    "PostToolUse",
    "Stop",
    "SubagentStop",
    "UserPromptSubmit",
}


def _registrations() -> list[tuple[str, str, dict[str, Any]]]:
    """``(event, matcher, hook)`` for every hook the example registers."""
    document = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    return [
        (event, group.get("matcher", ""), hook)
        for event, groups in document["hooks"].items()
        for group in groups
        for hook in group["hooks"]
    ]


def _script_of(hook: dict[str, Any]) -> str:
    match = SCRIPT_RE.search(hook["command"])
    assert match is not None, hook["command"]
    return match.group("name")


def test_the_example_is_valid_json_with_only_known_events() -> None:
    document = json.loads(EXAMPLE.read_text(encoding="utf-8"))

    assert set(document["hooks"]) <= EVENTS
    assert "Stop" in document["hooks"]
    assert "PreToolUse" in document["hooks"]


@pytest.mark.parametrize(
    ("event", "matcher", "hook"),
    _registrations(),
    ids=lambda v: v if isinstance(v, str) else _script_of(v),
)
def test_every_registered_script_exists_and_is_executable(
    event: str, matcher: str, hook: dict[str, Any]
) -> None:
    script = HOOKS / _script_of(hook)

    assert hook["type"] == "command"
    assert script.is_file(), f"{event}: {script.name} is registered but absent"
    assert os.access(script, os.X_OK), f"{script.name} is not executable"
    if matcher:
        re.compile(matcher)  # a matcher is a regular expression


def test_every_hook_has_a_timeout_in_seconds() -> None:
    for _event, _matcher, hook in _registrations():
        timeout = hook["timeout"]
        assert isinstance(timeout, int)
        assert 0 < timeout <= 900


def test_every_script_in_the_folder_is_registered_and_the_library_is_not() -> None:
    scripts = {
        p.name
        for p in HOOKS.iterdir()
        if p.is_file() and p.suffix in {".py", ".sh"} and not p.name.startswith("_")
    }
    registered = {_script_of(hook) for _e, _m, hook in _registrations()}

    assert registered == scripts
    assert "_hooklib.py" not in registered


def test_the_blocking_gates_are_where_the_design_puts_them() -> None:
    by_script: dict[str, set[str]] = {}
    for event, _matcher, hook in _registrations():
        by_script.setdefault(_script_of(hook), set()).add(event)

    assert by_script["stop_gate.py"] == {"Stop"}
    assert by_script["subagent_report.py"] == {"SubagentStop"}
    assert by_script["stage_context.py"] == {"UserPromptSubmit"}
    assert by_script["commit_guard.py"] == {"PreToolUse"}
    assert by_script["protect_paths.py"] == {"PreToolUse"}
    assert by_script["fmt_py.sh"] == by_script["fmt_ts.sh"] == {"PostToolUse"}


def test_the_example_is_not_a_live_registration() -> None:
    """The example sits beside the scripts, under ``ai/``: Claude Code reads
    ``.claude/settings*.json`` only, so nothing here changes a session."""
    assert EXAMPLE.parent == HOOKS
    assert ".claude" not in EXAMPLE.relative_to(ROOT).parts


def _run(script: str, payload: dict[str, Any]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(HOOKS / script)],
        input=json.dumps({"cwd": str(ROOT), **payload}),
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
        cwd=ROOT,
    )


def test_protect_paths_lets_an_ordinary_edit_through_and_refuses_a_secret() -> None:
    ordinary = _run(
        "protect_paths.py",
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Edit",
            "tool_input": {"file_path": str(ROOT / "README.md")},
        },
    )
    secret = _run(
        "protect_paths.py",
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": str(ROOT / ".env"), "content": "X=1"},
        },
    )

    assert ordinary.returncode == 0, ordinary.stderr
    assert secret.returncode == 2
    assert ".env" in secret.stderr


def test_commit_guard_ignores_a_command_that_is_not_a_commit() -> None:
    result = _run(
        "commit_guard.py",
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "git status --short"},
        },
    )

    assert result.returncode == 0, result.stderr


def test_stage_context_answers_a_bare_prompt_without_blocking() -> None:
    result = _run("stage_context.py", {"hook_event_name": "UserPromptSubmit"})

    assert result.returncode == 0, result.stderr
