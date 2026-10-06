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
