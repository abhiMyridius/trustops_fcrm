"""``ops/scripts/seed_reset.py``: command construction and refusal logic, with no
Docker (``run_step`` is replaced or ``subprocess.run`` is faked)."""

from __future__ import annotations

import ast
import importlib.util
import re
import subprocess
from pathlib import Path
from typing import Any, Optional

import pytest

ROOT = Path(__file__).resolve().parents[4]
_SPEC = importlib.util.spec_from_file_location(
    "seed_reset", ROOT / "ops" / "scripts" / "seed_reset.py"
)
assert _SPEC is not None
assert _SPEC.loader is not None
seed_reset = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(seed_reset)

COMPOSE = ["docker", "compose", "-f", "docker-compose.yaml"]
CASE = "FCRM-2026-0151"


class _Calls:
    """Records ``run_step`` calls instead of running Docker."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], str, Optional[bytes]]] = []

    def __call__(
        self,
        command: Any,
        description: str,
        *,
        stdin: Optional[bytes] = None,
        cwd: Any = None,
    ) -> str:
        self.calls.append((list(command), description, stdin))
        return ""


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> _Calls:
    recorder = _Calls()
    monkeypatch.setattr(seed_reset, "run_step", recorder)
    return recorder


# ---------------------------------------------------------------- refusal
@pytest.mark.parametrize("env", ["prod", "PROD", " production ", "Production"])
def test_a_production_environment_is_refused(env: str) -> None:
    with pytest.raises(seed_reset.RefusedError, match="refusing"):
        seed_reset.refuse_in_prod(env)


@pytest.mark.parametrize("env", [None, "", "local", "test", "ci", "staging", "dev"])
def test_every_other_environment_is_allowed(env: Optional[str]) -> None:
    seed_reset.refuse_in_prod(env)


@pytest.mark.parametrize("flag", ["--all", "--demo-case", "--stage-fixtures"])
def test_main_refuses_in_prod_before_running_anything(
    flag: str, calls: _Calls, caplog: pytest.LogCaptureFixture
) -> None:
    assert seed_reset.main([flag, "--env", "prod"]) == 1
    assert calls.calls == []
    assert "refusing to reset data when ENV=prod" in caplog.text


def test_main_reads_the_environment_from_env_by_default(
    calls: _Calls, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ENV", "prod")
    assert seed_reset.main(["--demo-case"]) == 1
    monkeypatch.setenv("ENV", "local")
    assert seed_reset.main(["--demo-case"]) == 0
    assert len(calls.calls) == 3


def test_exactly_one_mode_is_required() -> None:
    with pytest.raises(SystemExit):
        seed_reset.main([])
    with pytest.raises(SystemExit):
        seed_reset.main(["--all", "--demo-case"])


# ------------------------------------------------------ command construction
def test_exec_command_runs_a_shell_in_the_service_without_a_tty() -> None:
    command = seed_reset.exec_command(COMPOSE, "mongo", "echo hi")
    assert command == [*COMPOSE, "exec", "-T", "mongo", "bash", "-c", "echo hi"]


def test_the_compose_command_is_split_like_a_shell_would() -> None:
    assert seed_reset.DEFAULT_COMPOSE.startswith("docker compose -f ")
    assert seed_reset.DEFAULT_COMPOSE.endswith("docker-compose.yaml")


def test_mongo_credentials_are_left_to_the_container_shell() -> None:
    shell = seed_reset.MONGOSH_SHELL
    assert "$MONGO_INITDB_ROOT_USERNAME" in shell
    assert "$MONGO_INITDB_ROOT_PASSWORD" in shell
    assert "--authenticationDatabase admin" in shell
    assert '--eval "$(cat)"' in shell
    assert "mongodb://localhost:27017" in shell


def test_minio_credentials_are_left_to_the_container_shell() -> None:
    for shell in (
        seed_reset.minio_demo_shell(CASE),
        seed_reset.minio_stage_shell("a.pdf"),
    ):
        assert "${MINIO_ROOT_USER}" in shell
        assert "${MINIO_ROOT_PASSWORD}" in shell
        assert "set -euo pipefail" in shell


def test_the_all_script_drops_the_six_app_databases_and_only_empties_audit() -> None:
    script = seed_reset.mongo_all_script()
    listed = re.search(r"for \(const name of \[(.*?)\]\) \{\n  const result", script)
    assert listed is not None
    dropped = re.findall(r'"([a-z_]+)"', listed.group(1))
    assert dropped == [
        "fcrm_case",
        "fcrm_decision",
        "fcrm_harness",
        "fcrm_policy",
        "fcrm_scoring",
        "fcrm_identity",
    ]
    assert script.count("dropDatabase") == 1
    assert "fcrm_audit" not in dropped
    assert 'db.getSiblingDB("fcrm_audit")' in script
    assert "deleteMany({})" in script
    for collection in ("audit_records", "proposals"):
        assert f'"{collection}"' in script


def test_the_all_script_carries_no_secret_and_never_drops_the_admin_db() -> None:
    script = seed_reset.mongo_all_script()
    assert "admin" not in script
    assert "password" not in script.lower()


def test_the_demo_script_sweeps_every_database_for_the_case_id() -> None:
    script = seed_reset.mongo_demo_script(CASE)
    assert f'const caseId = "{CASE}"' in script
    for database in (*seed_reset.APP_DATABASES, "fcrm_audit"):
        assert f'"{database}"' in script
    assert "{ case_id: caseId }" in script
    assert "dropDatabase" not in script
    assert 'startsWith("system.")' in script


def test_the_demo_script_reaches_s5_rows_that_carry_no_case_id() -> None:
    script = seed_reset.mongo_demo_script(CASE)
    assert 'decision.packs.distinct("pack_id", { case_id: caseId })' in script
    for needle in (
        '["votes", { pack_id: { $in: packIds } }]',
        '["questions", { "question.pack_id": { $in: packIds } }]',
        '["answers", { "answer.pack_id": { $in: packIds } }]',
        '["outcomes", { "outcome.case_id": caseId }]',
    ):
        assert needle in script
    # S5 is cleared by pack before the generic sweep removes the packs.
    assert script.index("const byPack") < script.index("for (const dbName")


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "FCRM-2026-151",
        'x"; db.dropDatabase(); //',
        "FCRM-2026-0151 ",
        "fcrm-2026-0151",
    ],
)
def test_a_case_id_that_is_not_a_case_id_is_never_put_in_a_script(bad: str) -> None:
    for build in (
        seed_reset.mongo_demo_script,
        seed_reset.minio_demo_shell,
        seed_reset.validated_case_id,
    ):
        with pytest.raises(seed_reset.ResetError, match="not a case id"):
            build(bad)


def test_the_minio_demo_shell_clears_both_prefixes_of_the_case() -> None:
    shell = seed_reset.minio_demo_shell(CASE)
    assert "for bucket in uploads canonical;" in shell
    assert f'target="fcrm/${{bucket}}/{CASE}/"' in shell
    assert "rm --recursive --force" in shell
    assert "nothing under" in shell


def test_the_stage_shell_pipes_stdin_into_the_fixtures_prefix() -> None:
    shell = seed_reset.minio_stage_shell("business_case_remittance_MX.pdf")
    assert shell.endswith(
        'pipe "fcrm/uploads/_fixtures/business_case_remittance_MX.pdf"'
    )


@pytest.mark.parametrize("name", ["", "a b.pdf", "../x.pdf", 'a";rm.pdf', "a/b.pdf"])
def test_an_unsafe_fixture_name_is_refused(name: str) -> None:
    with pytest.raises(seed_reset.ResetError, match="unsafe"):
        seed_reset.minio_stage_shell(name)


# ----------------------------------------------------------------- the steps
def test_all_drops_through_the_mongo_container_then_replays_the_init(
    calls: _Calls,
) -> None:
    seed_reset.reset_all(COMPOSE)
    (first, _, stdin), (second, _, second_stdin) = calls.calls
    assert first == seed_reset.exec_command(COMPOSE, "mongo", seed_reset.MONGOSH_SHELL)
    assert stdin is not None
    assert b"dropDatabase" in stdin
    assert second == seed_reset.exec_command(
        COMPOSE, "mongo", seed_reset.MONGO_INIT_SHELL
    )
    assert second_stdin is None


def test_demo_case_clears_mongo_then_minio_then_restores_the_draft(
    calls: _Calls,
) -> None:
    seed_reset.reset_demo_case(COMPOSE)
    (mongo, _, stdin), (minio, _, _), (restore, _, _) = calls.calls
    assert restore == seed_reset.exec_command(
        COMPOSE, "api", "python -m fcrm_case.demo_restore"
    )
    assert mongo[: len(COMPOSE)] == COMPOSE
    assert mongo[len(COMPOSE) : len(COMPOSE) + 2] == ["exec", "-T"]
    assert mongo[6] == "mongo"
    assert minio[6] == "minio"
    assert stdin is not None
    assert CASE.encode() in stdin
    assert CASE in minio[-1]


def test_stage_fixtures_pipes_each_committed_pdf(calls: _Calls) -> None:
    staged = seed_reset.stage_fixtures(COMPOSE)
    assert staged == list(seed_reset.FIXTURE_FILES)
    assert len(calls.calls) == 3
    for (command, _, stdin), name in zip(calls.calls, staged, strict=True):
        assert command[6] == "minio"
        assert name in command[-1]
        assert stdin == (seed_reset.FIXTURES_DIR / name).read_bytes()
        assert stdin is not None
        assert stdin.startswith(b"%PDF")


def test_the_staged_files_are_exactly_the_committed_demo_documents() -> None:
    committed = sorted(p.name for p in seed_reset.FIXTURES_DIR.glob("*.pdf"))
    assert sorted(seed_reset.FIXTURE_FILES) == committed


def test_stage_fixtures_checks_every_file_before_copying_any(
    calls: _Calls, tmp_path: Path
) -> None:
    (tmp_path / seed_reset.FIXTURE_FILES[0]).write_bytes(b"%PDF")
    with pytest.raises(seed_reset.ResetError, match="missing fixture"):
        seed_reset.stage_fixtures(COMPOSE, tmp_path)
    assert calls.calls == []


# ------------------------------------------------------------------ run_step
class _Done:
    def __init__(self, code: int, out: bytes = b"", err: bytes = b"") -> None:
        self.returncode = code
        self.stdout = out
        self.stderr = err


def test_run_step_passes_argv_stdin_and_a_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def fake_run(command: list[str], **kwargs: Any) -> _Done:
        seen.update(command=command, **kwargs)
        return _Done(0, b"emptied fcrm_audit.audit_records: 3\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    output = seed_reset.run_step(["docker", "x"], "step", stdin=b"SCRIPT")
    assert output == "emptied fcrm_audit.audit_records: 3"
    assert seen["command"] == ["docker", "x"]
    assert seen["input"] == b"SCRIPT"
    assert seen["timeout"] == seed_reset.COMMAND_TIMEOUT_S
    assert seen["capture_output"] is True
    assert seen["check"] is False
    assert "shell" not in seen


def test_a_failing_step_reports_the_tail_of_its_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: _Done(1, b"", b"line1\nline2\nboom\n")
    )
    with pytest.raises(seed_reset.ResetError, match="exit 1: line1 \\| line2 \\| boom"):
        seed_reset.run_step(["docker"], "dropping")


def test_a_timeout_and_a_missing_docker_become_reset_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def timeout(*args: Any, **kwargs: Any) -> None:
        raise subprocess.TimeoutExpired("docker", 1)

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(seed_reset.ResetError, match="timed out"):
        seed_reset.run_step(["docker"], "step")

    def missing(*args: Any, **kwargs: Any) -> None:
        raise FileNotFoundError("docker")

    monkeypatch.setattr(subprocess, "run", missing)
    with pytest.raises(seed_reset.ResetError, match="Docker installed"):
        seed_reset.run_step(["docker"], "step")


def test_main_turns_a_failed_step_into_a_non_zero_exit(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def fail(*args: Any, **kwargs: Any) -> str:
        raise seed_reset.ResetError("mongo is down")

    monkeypatch.setattr(seed_reset, "run_step", fail)
    assert seed_reset.main(["--all", "--env", "local"]) == 1
    assert "mongo is down" in caplog.text


def test_main_passes_the_compose_command_through(calls: _Calls) -> None:
    seed_reset.main(
        ["--demo-case", "--env", "local", "--compose", "docker compose -f a.yaml -p x"]
    )
    assert calls.calls[0][0][:6] == ["docker", "compose", "-f", "a.yaml", "-p", "x"]


# ------------------------------------------------------- never reads dotenv
def _string_constants(source: str) -> list[str]:
    tree = ast.parse(source)
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    return [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and id(n) not in docstrings
    ]


def test_the_script_never_refers_to_an_env_file_outside_its_docstrings() -> None:
    source = (ROOT / "ops" / "scripts" / "seed_reset.py").read_text(encoding="utf-8")
    for text in _string_constants(source):
        assert ".env" not in text
        assert "dotenv" not in text.lower()
    assert "dotenv" not in source.lower()


def test_the_only_file_it_reads_is_a_fixture() -> None:
    source = (ROOT / "ops" / "scripts" / "seed_reset.py").read_text(encoding="utf-8")
    reads = re.findall(r"\.(?:read_text|read_bytes|open)\(", source)
    assert reads == [".read_bytes("]


# --------------------------------------------------------------- the Makefile
def _recipe(target: str) -> str:
    text = (ROOT / "Makefile").read_text(encoding="utf-8")
    match = re.search(rf"^{target}:.*\n((?:\t.*\n)+)", text, re.MULTILINE)
    assert match is not None, target
    return match.group(1)


def test_make_seed_runs_the_reset_restarts_the_apps_and_stages_the_pdfs() -> None:
    recipe = _recipe("seed")
    assert "pending" not in recipe
    assert "ops/scripts/seed_reset.py --all" in recipe
    assert "restart api harness" in recipe
    assert (
        recipe.index("--all")
        < recipe.index("restart")
        < recipe.index("--stage-fixtures")
    )


def test_make_demo_reset_runs_the_demo_case_reset() -> None:
    recipe = _recipe("demo-reset")
    assert "pending" not in recipe
    assert "ops/scripts/seed_reset.py --demo-case" in recipe
