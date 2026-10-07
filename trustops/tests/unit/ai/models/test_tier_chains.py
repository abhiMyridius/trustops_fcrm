"""The tier chains of ``ai/models/tiers.yaml`` hold no Opus and no Sonnet 5 (ADR-62),
keep the critic independent of the assessor (ADR-04), retry a strong-tier agent
on a model that can do it, and carry no model that no chain uses."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

TIERS_YAML = Path(__file__).resolve().parents[4] / "ai" / "models" / "tiers.yaml"
BANNED_LABELS = ("Opus", "Sonnet 5")


@pytest.fixture(name="config")
def _config() -> dict[str, Any]:
    return yaml.safe_load(TIERS_YAML.read_text(encoding="utf-8"))


def _chain(config: dict[str, Any], tier: str) -> list[str]:
    entry = config["tiers"][tier]
    return [entry["primary"], *entry.get("fallbacks", [])]


def _labels(config: dict[str, Any], tier: str) -> list[str]:
    return [config["models"][model]["label"] for model in _chain(config, tier)]


def test_no_chain_holds_an_opus_or_a_sonnet_5(config: dict[str, Any]) -> None:
    for tier in config["tiers"]:
        for label in _labels(config, tier):
            assert not label.startswith(BANNED_LABELS), (tier, label)


def test_no_model_entry_or_price_row_names_an_opus_or_a_sonnet_5(
    config: dict[str, Any],
) -> None:
    labels = [entry["label"] for entry in config["models"].values()]

    assert not [label for label in labels if label.startswith(BANNED_LABELS)]


def test_the_critic_runs_on_a_different_model_than_the_assessor(
    config: dict[str, Any],
) -> None:
    assert config["tiers"]["judge"]["primary"] != config["tiers"]["strong"]["primary"]
    assert (
        config["models"][config["tiers"]["judge"]["primary"]]["family"]
        != config["models"][config["tiers"]["strong"]["primary"]]["family"]
    )


def test_a_strong_tier_retry_runs_on_a_model_that_can_do_the_work(
    config: dict[str, Any],
) -> None:
    # A retry runs on fallbacks[0] alone (ADR-14); Haiku and Nova Lite failed the
    # research and assessment schemas on a retry in the live runs of 6 Oct (D-95).
    strong = _labels(config, "strong")

    assert strong[0] == "Sonnet 4.5"
    assert strong[1] == "Sonnet 4.5"


def test_every_model_entry_is_used_by_a_chain_and_has_a_price_row(
    config: dict[str, Any],
) -> None:
    used = {model for tier in config["tiers"] for model in _chain(config, tier)}
    rows = set(config["prices_usd_per_mtok"]["rows"])

    assert set(config["models"]) == used
    assert used <= rows
