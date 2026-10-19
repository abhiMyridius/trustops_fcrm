"""The critic check's thresholds in :mod:`fcrm_guardrails.thresholds` mirror
``ai/guards/thresholds.yaml`` (the file the critic's prompt is told the values
from), so the code that checks a finding and the model that raised it use one
number.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from fcrm_guardrails.thresholds import PRECEDENT_DEVIATION_FLAG, PRECEDENT_MIN_CASES

THRESHOLDS = Path(__file__).parents[4] / "ai" / "guards" / "thresholds.yaml"


def _configured() -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load(THRESHOLDS.read_text(encoding="utf-8"))
    return loaded


def test_the_deviation_flag_is_the_configured_one() -> None:
    assert _configured()["precedent_deviation_flag"] == PRECEDENT_DEVIATION_FLAG


def test_the_minimum_number_of_cases_is_the_configured_one() -> None:
    assert _configured()["precedent_min_cases"] == PRECEDENT_MIN_CASES
