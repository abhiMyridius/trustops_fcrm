"""Shared fixtures for every test under ``tests/`` (``ai/guidance/coding-
standards.md``: "Fixtures and shared mocks: ``tests/conftest.py`` only").

This file did not exist before the integration/contract/black-box layer
added here (``tests/integration/``); ``tests/unit/**`` has a handful of
its own nested ``conftest.py`` files from earlier lanes (agent-worker,
ingestion-worker, context-worker) which this file does not touch or
replace -- they stay local to their own unit's tests, as already merged.
Everything new under ``tests/integration/`` draws its shared fixtures
from here instead of adding another nested one, per the standard.
"""

from __future__ import annotations

from typing import Any

import pytest
from fcrm_seed.loader import cases as seed_cases
from fcrm_seed.loader import control_catalogue_weights, demo_case, scoring_model


@pytest.fixture(scope="session")
def demo() -> dict[str, Any]:
    """The canonical demo case, FCRM-2026-0151, exactly as every service's
    own golden tests read it (``fcrm_seed.loader.demo_case``).
    """
    return demo_case()


@pytest.fixture(scope="session")
def all_seed_cases() -> list[dict[str, Any]]:
    """All 80 seed cases (``fcrm_seed.loader.cases``)."""
    return seed_cases()


@pytest.fixture(scope="session")
def catalogue_weights() -> dict[str, float]:
    """``{control_id: catalogue_weight}`` from the seeded control
    inventory (v9), used to fill in ``ControlInput.catalogue_weight``
    exactly as the real callers (H1, the override path) would.
    """
    return control_catalogue_weights()


@pytest.fixture(scope="session")
def seeded_scoring_model() -> dict[str, Any]:
    """The raw ``seed.json["scoring_model"]`` section (v3)."""
    return scoring_model()
