"""Typed accessors over ``seed.json``.

Every service's ``seedload`` lifespan hook and every test that needs golden
data goes through this module rather than re-parsing the JSON file, so a
seed-shape change is caught in one place.
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from typing import Any


@lru_cache(maxsize=1)
def load_raw() -> dict[str, Any]:
    """The full seed document, parsed once per process."""
    seed_file = resources.files("fcrm_seed").joinpath("seed.json")
    text = seed_file.read_text(encoding="utf-8")
    data: dict[str, Any] = json.loads(text)
    return data


def scoring_model() -> dict[str, Any]:
    """``seed.json["scoring_model"]``: likelihood/impact scales, domain
    weights, retention factor and floor, bands, boundary-flag distance.
    """
    model: dict[str, Any] = load_raw()["scoring_model"]
    return model


def control_catalogue_weights() -> dict[str, float]:
    """``{control_id: catalogue_weight}`` from the control inventory, used to
    normalise control effectiveness within a domain (fcrm_scoring.engine).
    """
    return {c["id"]: c["weight"] for c in load_raw()["controls"]}


def cases() -> list[dict[str, Any]]:
    """All 80 seed cases, including the demo case at index matching its id."""
    case_list: list[dict[str, Any]] = load_raw()["cases"]
    return case_list


def demo_case() -> dict[str, Any]:
    """The canonical demo case, FCRM-2026-0151, with its score_before,
    score_after, audit trail and committee vote already worked out.
    """
    demo: dict[str, Any] = load_raw()["demo"]
    return demo


def routing_rules() -> list[dict[str, Any]]:
    """Routing rules v4, R1 through R6, as data (ADR-19)."""
    rules: list[dict[str, Any]] = load_raw()["routing"]["rules"]
    return rules


def band_distribution() -> dict[str, int]:
    """The expected count of cases per band across all 80 seed cases."""
    distribution: dict[str, int] = load_raw()["derived"]["band_distribution"]
    return distribution
