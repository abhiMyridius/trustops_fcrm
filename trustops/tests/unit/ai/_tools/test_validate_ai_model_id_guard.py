"""``ai/_tools/validate_ai.py``: the model-id rule over ``src``, ``evals`` and ``ops``.

``ai/models/tiers.yaml`` is the only file that names a model id (ADR-03,
ADR-04). ``validate_ai`` scans ``ai/`` and, with ``scan_for_model_ids``, the
source trees, with a short allow-list whose every row must still be live.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[4]


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "validate_ai_under_test", ROOT / "ai" / "_tools" / "validate_ai.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validate_ai = _load()

# Assembled from parts so this file never reads as a model id itself.
_BEDROCK_ID = "us." + "anthropic." + "claude-" + "haiku-4-5-v1:0"
_SHORT_ID = "claude-" + "sonnet-5-5"


def _write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_the_repository_has_no_model_id_outside_the_allow_list() -> None:
    assert validate_ai.scan_for_model_ids(ROOT) == {}


def test_every_allow_list_row_is_a_real_file_that_still_names_an_id() -> None:
    for rel in validate_ai.MODEL_ID_ALLOWED:
        text = (ROOT / rel).read_text(encoding="utf-8", errors="ignore")
        assert validate_ai.MODEL_ID_RE.search(text), f"{rel} no longer needs its row"


def test_a_model_id_in_python_source_is_reported_with_the_id(tmp_path: Path) -> None:
    _write(tmp_path, "src/backend/svc/app.py", f'MODEL = "{_BEDROCK_ID}"\n')

    found = validate_ai.scan_for_model_ids(tmp_path, allowed={})

    assert found == {"src/backend/svc/app.py": [_BEDROCK_ID]}


def test_the_short_claude_form_is_reported_too(tmp_path: Path) -> None:
    _write(tmp_path, "evals/run.yaml", f"judge: {_SHORT_ID}\n")

    found = validate_ai.scan_for_model_ids(tmp_path, allowed={})

    assert list(found) == ["evals/run.yaml"]
    assert found["evals/run.yaml"][0].startswith("claude-sonnet-5")


def test_allowed_files_are_skipped_and_others_are_not(tmp_path: Path) -> None:
    _write(tmp_path, "src/backend/seed.json", f'{{"by": "{_BEDROCK_ID}"}}')
    _write(tmp_path, "src/backend/other.py", f'X = "{_BEDROCK_ID}"\n')

    found = validate_ai.scan_for_model_ids(
        tmp_path, allowed={"src/backend/seed.json": "provenance"}
    )

    assert list(found) == ["src/backend/other.py"]


def test_dependency_folders_other_suffixes_and_other_roots_are_ignored(
    tmp_path: Path,
) -> None:
    _write(tmp_path, "src/frontend/src/node_modules/x/index.js", _BEDROCK_ID)
    _write(tmp_path, "src/backend/.venv/lib/x.py", _BEDROCK_ID)
    _write(tmp_path, "src/backend/notes.md", _BEDROCK_ID)
    _write(tmp_path, "src/backend/.env", _BEDROCK_ID)
    _write(tmp_path, "tests/unit/test_fixture.py", _BEDROCK_ID)
    _write(tmp_path, "docs/architecture/design.md", _BEDROCK_ID)

    assert validate_ai.scan_for_model_ids(tmp_path, allowed={}) == {}


def test_a_missing_root_is_not_an_error(tmp_path: Path) -> None:
    assert validate_ai.scan_for_model_ids(tmp_path, allowed={}) == {}


def test_the_report_fails_when_an_offender_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write(tmp_path, "ops/run.sh", f"MODEL={_BEDROCK_ID}\n")
    monkeypatch.setattr(validate_ai, "REPO", tmp_path)
    monkeypatch.setattr(validate_ai, "MODEL_ID_ALLOWED", {})
    report = validate_ai.Report()

    validate_ai.check_model_ids_in_source(report)

    assert len(report.failures) == 1
    assert "ops/run.sh" in report.failures[0]


def test_the_report_fails_when_an_allow_list_row_goes_stale(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write(tmp_path, "src/backend/clean.py", "X = 1\n")
    monkeypatch.setattr(validate_ai, "REPO", tmp_path)
    monkeypatch.setattr(
        validate_ai, "MODEL_ID_ALLOWED", {"src/backend/clean.py": "was provenance"}
    )
    report = validate_ai.Report()

    validate_ai.check_model_ids_in_source(report)

    assert any("stale" in failure for failure in report.failures)
