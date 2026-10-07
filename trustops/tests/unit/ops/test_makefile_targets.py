"""``Makefile``: what ``test-int`` and ``check`` run, read from ``make -n``.

A dry run prints the recipes without executing them, so these tests pin the
wiring (which tests ``test-int`` selects, which static guards ``check`` runs)
without starting Docker or a second test run.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _dry_run(target: str) -> str:
    done = subprocess.run(
        ["make", "-n", target],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return done.stdout


def test_test_int_selects_every_integration_marked_test_under_tests() -> None:
    lines = [ln.strip() for ln in _dry_run("test-int").splitlines()]
    pytest_lines = [ln for ln in lines if " pytest " in ln]

    assert len(pytest_lines) == 1
    assert pytest_lines[0].endswith("pytest -m integration tests")
    assert "tests/integration" not in pytest_lines[0]


def test_test_int_says_so_when_the_live_mongo_uri_is_unset() -> None:
    recipe = _dry_run("test-int")

    assert 'if [ -z "$FCRM_TEST_MONGO_URI" ]' in recipe
    assert "the *_live.py tests under tests/unit will SKIP" in recipe


def test_check_runs_the_ai_registry_and_model_id_guard_and_the_lock_check() -> None:
    recipe = _dry_run("check")

    assert "ai/_tools/validate_ai.py" in recipe
    assert "ai/_tools/compute_locks.py --check" in recipe


def test_gate_still_runs_test_int() -> None:
    assert "pytest -m integration tests" in _dry_run("gate")


UP_TARGETS = ("up", "up-obs", "up-cache", "up-debug", "up-dev")


def test_every_up_target_recreates_opa_before_it_starts_the_stack() -> None:
    # OPA reads its policy and data files once at start; a bind-mounted file that
    # changed after that is never seen, so a new rule (or a tightened one) would
    # not apply on a stack brought up again (D-92).
    for target in UP_TARGETS:
        lines = [ln.strip() for ln in _dry_run(target).splitlines()]
        recreate = [
            i for i, ln in enumerate(lines) if "--force-recreate --no-deps opa" in ln
        ]
        started = [i for i, ln in enumerate(lines) if ln.endswith("up -d --build")]

        assert len(recreate) == 1, target
        assert len(started) == 1, target
        assert recreate[0] < started[0], target


def test_pull_names_only_images_that_exist_in_a_registry() -> None:
    # harness shares the image that api builds (trustops/backend:local), and an
    # explicit `docker compose pull harness` ignores pull_policy: never and fails
    # with "pull access denied", which stopped `make bootstrap` on a first run.
    lines = [
        ln for ln in _dry_run("pull").splitlines() if "compose" in ln and " pull " in ln
    ]

    assert len(lines) == 1
    for registry_service in ("mongo", "rabbitmq", "minio", "opa", "gateway"):
        assert registry_service in lines[0].split()
    for built_here in ("api", "harness", "web"):
        assert built_here not in lines[0].split()
    assert "--ignore-buildable" not in lines[0]


def _make_keys(cwd: Path) -> None:
    # Runs the real recipe (openssl, chmod) in a scratch folder: ``make -f`` with
    # the repository's Makefile writes jwt_keys/ relative to ``cwd``.
    subprocess.run(
        ["make", "-f", str(ROOT / "Makefile"), "keys"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )


def _mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def test_keys_writes_a_key_the_api_container_user_can_read(tmp_path: Path) -> None:
    # The api runs as uid 10001; on Docker for Linux and WSL 2 a bind-mounted
    # 0600 key owned by the operator is unreadable and the api never starts.
    _make_keys(tmp_path)

    keys = tmp_path / "jwt_keys"
    assert _mode(keys) == 0o755
    assert _mode(keys / "jwt.pem") == 0o644
    assert _mode(keys / "jwt.pub.pem") == 0o644
    assert (keys / ".gitignore").read_text() == "*\n"


def test_keys_repairs_an_existing_key_without_rotating_it(tmp_path: Path) -> None:
    # A key written by the earlier recipe (0700 folder, 0600 key) is fixed by
    # the next `make up`, which depends on `keys`.
    _make_keys(tmp_path)
    keys = tmp_path / "jwt_keys"
    original = (keys / "jwt.pem").read_bytes()
    keys.chmod(0o700)
    (keys / "jwt.pem").chmod(0o600)

    _make_keys(tmp_path)

    assert _mode(keys) == 0o755
    assert _mode(keys / "jwt.pem") == 0o644
    assert (keys / "jwt.pem").read_bytes() == original


def test_tools_verifies_the_opa_download_without_requiring_shasum() -> None:
    # shasum ships with perl on macOS; a slim Linux or WSL image may have only
    # coreutils' sha256sum, which the recipe prefers when present.
    recipe = (ROOT / "Makefile").read_text()

    assert "command -v sha256sum || echo 'shasum -a 256'" in recipe
