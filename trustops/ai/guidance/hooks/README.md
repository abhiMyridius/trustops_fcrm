# Claude Code hooks

The scripts in this folder are the local stand-in for CI (design C 5.4; ADR-15): they guard a Claude Code session, they do not run on their own. Nothing in the repository registers them: no `.claude/settings.json` is tracked, because one would change every contributor's session. What is enforced for every contributor is the git `pre-commit` and `commit-msg` hooks that `make bootstrap` installs; these scripts are an extra layer for people who want it.

| Script | Event and matcher | What it does |
|---|---|---|
| `protect_paths.py` | `PreToolUse` on `Edit\|Write\|MultiEdit\|NotebookEdit` and on `Bash` | refuses edits to generated files (`seed.json`, the web's generated client), to `.env*` and `AEO/secrets`, and to existing rows of the append-only tables in `docs/governance` |
| `commit_guard.py` | `PreToolUse` on `Bash` | blocks `git commit` unless `.cache/check.ok` is newer than every changed file and the message follows `commit-convention.md` |
| `fmt_py.sh`, `fmt_ts.sh` | `PostToolUse` on `Edit\|Write\|MultiEdit` | ruff (and mypy in the background) on a Python file, oxlint and `tsc` on a web file; each skips the other's extensions |
| `stop_gate.py` | `Stop` | runs `make check` before a turn may end (about 5 minutes) and writes `.cache/check.ok` when it is green |
| `subagent_report.py` | `SubagentStop` | a subagent's last message must carry `Changed`, `Failed` and `Decided` |
| `stage_context.py` | `UserPromptSubmit` | prepends the SDLC stage, the open defects and the last nightly status |

## Enable them

`settings.example.json` holds the registrations. It is an example: nothing reads it.

1. Copy its `hooks` object (not the `_comment` key) into `trustops/.claude/settings.local.json` to enable them for yourself only, or into `trustops/.claude/settings.json` to enable them for everyone who opens the folder. Prefer the local file.
2. Start Claude Code in `trustops/`, the folder that holds the `Makefile`: the example addresses the scripts as `$CLAUDE_PROJECT_DIR/ai/guidance/hooks/...`, and `fmt_*.sh` resolve the repository from the same variable.
3. Run `/hooks` in the session to see what is registered. The scripts need `python3` on the `PATH` (they are stdlib only and run under the system Python), and `make check` needs the project environment (`make bootstrap`).

Two of them are heavy. `stop_gate.py` runs the whole commit gate at the end of every turn, and `commit_guard.py` then refuses a commit made before it has gone green; if that is too slow, register only the `PreToolUse` guards and run `make check` yourself before committing. Each script also runs on its own (`python3 ai/guidance/hooks/protect_paths.py < payload.json`; see the docstring at the top of each).

## What is and is not proved

`tests/unit/ai/guidance/test_hooks_settings_example.py` checks that the example is valid JSON, that every script it names exists and is executable, that every script in this folder is registered once, and that the guards that are pure (`protect_paths.py`, `commit_guard.py`, `stage_context.py`) answer a plain payload. It does not run Claude Code, so that a registration fires in a session is not proved by anything in the repository.
