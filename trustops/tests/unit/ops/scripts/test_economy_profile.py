"""``ops/scripts/economy_profile.py``: the economy copy of ``ai/`` holds no Opus and
no Sonnet 5 in any tier chain, changes only what ``ai/models/economy.yaml`` says,
marks its added price as unverified, and never deletes a folder it did not make.
No Docker and no model call."""

from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
from typing import Any

import pytest
import yaml
from fcrm_agents.models import load_tiers_config
from fcrm_evals.usage import STATUS_PARTLY_VERIFIED, PriceTable

ROOT = Path(__file__).resolve().parents[4]
_SPEC = importlib.util.spec_from_file_location(
    "economy_profile", ROOT / "ops" / "scripts" / "economy_profile.py"
)
assert _SPEC is not None
assert _SPEC.loader is not None
economy_profile = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(economy_profile)

SHIPPED_TIERS = ROOT / "ai" / "models" / "tiers.yaml"
ECONOMY_FILE = ROOT / "ai" / "models" / "economy.yaml"
SET_BY_ECONOMY = ("mid", "strong", "judge")


def _shipped() -> dict[str, Any]:
    return yaml.safe_load(SHIPPED_TIERS.read_text(encoding="utf-8"))


def _extras() -> dict[str, Any]:
    return economy_profile.load_extras(ECONOMY_FILE)


def _built() -> dict[str, Any]:
    return economy_profile.build_economy_config(_shipped(), _extras())


def _labels(config: dict[str, Any], tier: str) -> list[str]:
    chain = economy_profile.chain_of(config["tiers"][tier])
    return [economy_profile.model_label(config, model) for model in chain]


def test_the_shipped_tiers_do_use_opus_and_sonnet_5() -> None:
    # The checks below would pass vacuously if the shipped file had neither.
    assert economy_profile.expensive_models(_shipped())


def test_no_tier_chain_of_the_economy_profile_holds_opus_or_sonnet_5() -> None:
    economy = _built()

    assert economy_profile.expensive_models(economy) == []
    for tier in economy["tiers"]:
        assert not any(
            label.startswith(("Opus", "Sonnet 5")) for label in _labels(economy, tier)
        )


def test_the_chains_are_the_ones_economy_yaml_declares() -> None:
    economy = _built()

    assert _labels(economy, "cheap") == _labels(_shipped(), "cheap")
    assert _labels(economy, "mid") == ["Sonnet 4.5", "Haiku 4.5"]
    # A retry runs on the second model alone: strong retries on Sonnet 4.5 itself.
    assert _labels(economy, "strong") == ["Sonnet 4.5", "Sonnet 4.5"]
    judge = _labels(economy, "judge")
    assert judge == ["Nova Pro", "Haiku 4.5"]
    # The critic still differs from the assessor it checks (ADR-04).
    assert judge[0] != _labels(economy, "strong")[0]


def test_only_the_declared_additions_and_chains_differ_from_the_shipped_file() -> None:
    shipped = _shipped()
    extras = _extras()
    economy = _built()
    expected = copy.deepcopy(shipped)
    expected["models"].update(extras["models"])
    expected["prices_usd_per_mtok"]["rows"].update(extras["prices"])
    for tier in SET_BY_ECONOMY:
        expected["tiers"][tier]["primary"] = economy["tiers"][tier]["primary"]
        expected["tiers"][tier]["fallbacks"] = economy["tiers"][tier]["fallbacks"]

    assert economy == expected
    assert economy["tiers"]["strong"]["budget"] == shipped["tiers"]["strong"]["budget"]


def test_the_added_price_is_marked_unverified_and_says_so_in_the_roll_up() -> None:
    table = PriceTable.from_tiers(_built())

    assert table.unverified == ["Sonnet 4.5"]
    assert table.status == STATUS_PARTLY_VERIFIED
    assert PriceTable.from_tiers(_shipped()).unverified == []


def test_building_does_not_modify_its_inputs() -> None:
    shipped, extras = _shipped(), _extras()
    before = copy.deepcopy((shipped, extras))
    economy_profile.build_economy_config(shipped, extras)

    assert (shipped, extras) == before


def test_a_chain_with_an_opus_is_refused() -> None:
    extras = _extras()
    extras["chains"]["strong"] = ["Sonnet 4.5", "Opus 5.5"]

    with pytest.raises(economy_profile.EconomyProfileError, match="Opus 5.5"):
        economy_profile.build_economy_config(_shipped(), extras)


def test_a_chain_with_the_latest_sonnet_is_refused() -> None:
    extras = _extras()
    extras["chains"]["mid"] = ["Sonnet 5.5", "Haiku 4.5"]

    with pytest.raises(economy_profile.EconomyProfileError, match="Sonnet 5.5"):
        economy_profile.build_economy_config(_shipped(), extras)


def test_an_unknown_label_is_an_error_not_a_silent_default() -> None:
    extras = _extras()
    extras["chains"]["judge"] = ["No Such Model"]

    with pytest.raises(economy_profile.EconomyProfileError, match="No Such Model"):
        economy_profile.build_economy_config(_shipped(), extras)


def test_an_empty_chain_is_an_error() -> None:
    extras = _extras()
    extras["chains"]["mid"] = []

    with pytest.raises(economy_profile.EconomyProfileError, match="mid"):
        economy_profile.build_economy_config(_shipped(), extras)


def test_a_chain_for_a_tier_the_shipped_file_lacks_is_an_error() -> None:
    extras = _extras()
    extras["chains"]["imaginary"] = ["Haiku 4.5"]

    with pytest.raises(economy_profile.EconomyProfileError, match="imaginary"):
        economy_profile.build_economy_config(_shipped(), extras)


def test_adding_a_model_the_shipped_file_already_has_is_an_error() -> None:
    shipped = _shipped()
    extras = _extras()
    existing = next(iter(shipped["models"]))
    extras["models"][existing] = {"label": "Again"}

    with pytest.raises(economy_profile.EconomyProfileError, match="already has"):
        economy_profile.build_economy_config(shipped, extras)


def test_a_price_for_a_model_nothing_adds_is_an_error() -> None:
    extras = _extras()
    extras["prices"]["us.example.ghost-v1:0"] = {"input": 1.0, "output": 1.0}

    with pytest.raises(economy_profile.EconomyProfileError, match="no model entry"):
        economy_profile.build_economy_config(_shipped(), extras)


def test_a_file_without_its_sections_is_an_error(tmp_path: Path) -> None:
    broken = tmp_path / "economy.yaml"
    broken.write_text("models: {}\nprices: {}\n", encoding="utf-8")

    with pytest.raises(economy_profile.EconomyProfileError, match="chains"):
        economy_profile.load_extras(broken)


def test_the_written_profile_loads_through_the_runtime_loader(tmp_path: Path) -> None:
    out = tmp_path / ".economy"
    override = economy_profile.write_profile(ROOT / "ai", out)
    loaded = load_tiers_config(out / "ai")

    assert economy_profile.expensive_models(loaded) == []
    assert override == out / "compose.override.yaml"
    # The shipped folder is untouched.
    assert economy_profile.expensive_models(_shipped())
    assert (out / "ai" / "prompts").is_dir()
    header = (out / "ai" / "models" / "tiers.yaml").read_text(encoding="utf-8")
    assert header.startswith("# GENERATED")


def test_the_override_replaces_only_the_ai_mount_of_api_and_harness(
    tmp_path: Path,
) -> None:
    out = tmp_path / ".economy"
    override = economy_profile.write_profile(ROOT / "ai", out)
    services = yaml.safe_load(override.read_text(encoding="utf-8"))["services"]

    assert sorted(services) == ["api", "harness"]
    for service in services.values():
        assert service["volumes"] == [f"{(out / 'ai').resolve()}:/app/ai:ro"]


def test_writing_twice_replaces_the_earlier_profile(tmp_path: Path) -> None:
    out = tmp_path / ".economy"
    economy_profile.write_profile(ROOT / "ai", out)
    stale = out / "ai" / "stale.txt"
    stale.write_text("x", encoding="utf-8")
    economy_profile.write_profile(ROOT / "ai", out)

    assert not stale.exists()


def test_a_folder_the_script_did_not_make_is_never_deleted(tmp_path: Path) -> None:
    out = tmp_path / "precious"
    out.mkdir()
    keep = out / "notes.txt"
    keep.write_text("keep", encoding="utf-8")

    with pytest.raises(economy_profile.EconomyProfileError, match="not deleting"):
        economy_profile.write_profile(ROOT / "ai", out)
    assert keep.read_text(encoding="utf-8") == "keep"


def test_main_reports_failure_with_exit_code_one(tmp_path: Path) -> None:
    precious = tmp_path / "precious"
    precious.mkdir()

    assert economy_profile.main(["--out", str(precious)]) == 1
