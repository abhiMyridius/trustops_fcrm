"""Shared fixtures for the cloned evoq_ingestion engine's test suite.

TrustOps clone notes (src/backend/workers/ingestion-worker/UPSTREAM.md):

* This file stays beside the moved suite rather than in ``tests/conftest.py``
  because it only serves this engine's tests and the lane may not edit the
  shared conftest; the lead can hoist it.
* The repo's ``pytest.ini`` runs ``--strict-markers`` and does not know
  upstream's ``slow``/``perf`` marks, so they are registered here. Upstream's
  default run excluded them (``-m "not slow and not perf"``); here they are
  skipped *with a visible reason* unless ``FCRM_RUN_SLOW=1`` is set.
* Upstream's ``--update-golden`` CLI option needed a root-level conftest; it
  is now the ``EVOQ_UPDATE_GOLDEN=1`` environment variable read by
  ``test_real_fixtures.py``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

_SLOW_MARKS = ("slow", "perf")


def pytest_configure(config: pytest.Config) -> None:
    """Register upstream's two marks so ``--strict-markers`` accepts them."""
    config.addinivalue_line(
        "markers",
        "slow: exercises real binary parsing or large inputs (FCRM_RUN_SLOW=1)",
    )
    config.addinivalue_line(
        "markers", "perf: timing/memory budget assertions (FCRM_RUN_SLOW=1)"
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Skip slow/perf tests unless ``FCRM_RUN_SLOW=1`` (upstream's default run)."""
    if os.environ.get("FCRM_RUN_SLOW") == "1":
        return
    skip = pytest.mark.skip(reason="slow/perf budget test; set FCRM_RUN_SLOW=1 to run")
    for item in items:
        if any(item.get_closest_marker(mark) for mark in _SLOW_MARKS):
            item.add_marker(skip)


@pytest.fixture
def uploads_folder(tmp_path: Path) -> Path:
    folder = tmp_path / "uploads"
    folder.mkdir()
    (folder / "notes.txt").write_text(
        "Plain text notes.\nSecond line.", encoding="utf-8"
    )
    (folder / "readme.md").write_text("# Heading\n\nBody text.", encoding="utf-8")
    (folder / "diagram.png").write_bytes(b"\x89PNG\r\n\x1a\nnot-a-real-png")
    return folder
