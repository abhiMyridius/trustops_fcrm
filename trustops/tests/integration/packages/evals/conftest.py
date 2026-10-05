"""Fixtures for the evals' live-Mongo tests: the real ``tiers.yaml`` and a
fixed timestamp (the throw-away database itself is ``live_db`` in
``tests/conftest.py``)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fcrm_agents.models import load_tiers_config

REPO_AI = Path(__file__).parents[4] / "ai"


@pytest.fixture(scope="session")
def tiers_config() -> Any:
    """The repository's real ``ai/models/tiers.yaml``, parsed."""
    return load_tiers_config(REPO_AI)


@pytest.fixture
def stamp() -> datetime:
    """A fixed result timestamp."""
    return datetime(2026, 10, 5, 14, 22, tzinfo=UTC)
