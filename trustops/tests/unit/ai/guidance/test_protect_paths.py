"""``protect_paths.py``: the repository paths the hook refuses to let an agent edit."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

HOOKS = Path(__file__).resolve().parents[4] / "ai" / "guidance" / "hooks"


def _load() -> ModuleType:
    sys.path.insert(0, str(HOOKS))
    try:
        spec = importlib.util.spec_from_file_location(
            "protect_paths_under_test", HOOKS / "protect_paths.py"
        )
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(HOOKS))
    return module


protect_paths = _load()


@pytest.mark.parametrize(
    "rel",
    [
        "EVOQ_Applications/.env",
        "EVOQ_Applications/AEO/.env.local",
        "EVOQ_Applications/a/b/c/.env.production",
        "AEO/secrets/key.pem",
        "x/AEO/secrets",
    ],
)
def test_secret_paths_are_blocked(rel: str) -> None:
    assert protect_paths.SECRET_PATH_RE.search(rel), rel
    assert protect_paths.protected_reason(rel)


@pytest.mark.parametrize(
    "rel",
    [
        "docs/env.md",
        "EVOQ_Applications/README.md",
        "EVOQ_Applications/notes/envelope.txt",
        "AEO/secretsauce/readme.md",
        "src/backend/app.py",
    ],
)
def test_ordinary_paths_are_not_secret_paths(rel: str) -> None:
    assert not protect_paths.SECRET_PATH_RE.search(rel), rel


def test_env_example_stays_editable() -> None:
    assert protect_paths.protected_reason(".env.example") is None
    assert protect_paths.protected_reason(".env") is not None
    assert protect_paths.protected_reason("deploy/.env.staging") is not None
