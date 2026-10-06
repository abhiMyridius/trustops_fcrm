"""``ops/scripts/economy_profile.py``: the economy copy of ``ai/`` has no Sonnet
or Opus in any tier chain, changes nothing else, and never deletes a folder it
did not make. No Docker and no model call."""

from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
from typing import Any

import pytest
import yaml
from fcrm_agents.models import load_tiers_config

ROOT = Path(__file__).resolve().parents[4]
_SPEC = importlib.util.spec_from_file_location(
    "economy_profile", ROOT / "ops" / "scripts" / "economy_profile.py"
)
assert _SPEC is not None
assert _SPEC.loader is not None
economy_profile = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(economy_profile)

SHIPPED_TIERS = ROOT / "ai" / "models" / "tiers.yaml"
CHAINED = ("mid", "strong", "judge")


def _shipped() -> dict[str, Any]:
    return yaml.safe_load(SHIPPED_TIERS.read_text(encoding="utf-8"))


def _labels(config: dict[str, Any], tier: str) -> list[str]:
    chain = economy_profile.chain_of(config["tiers"][tier])
    return [economy_profile.model_label(config, model) for model in chain]


def test_the_shipped_tiers_do_use_sonnet_and_opus() -> None:
    # The check below would pass vacuously if the shipped file had none.
    assert economy_profile.expensive_models(_shipped())


def test_no_tier_chain_of_the_economy_profile_holds_sonnet_or_opus() -> None:
    economy = economy_profile.build_economy_config(_shipped())
    assert economy_profile.expensive_models(economy) == []
    for tier in economy["tiers"]:
        assert not any(
            label.startswith(("Sonnet", "Opus")) for label in _labels(economy, tier)
        )


def test_the_chains_follow_the_cheap_tier_and_the_judge_stays_independent() -> None:
    shipped = _shipped()
    economy = economy_profile.build_economy_config(shipped)
    cheap = economy["tiers"]["cheap"]
    assert economy["tiers"]["mid"]["primary"] == cheap["primary"]
    assert economy["tiers"]["strong"]["fallbacks"] == cheap["fallbacks"]
    judge = economy["tiers"]["judge"]
    assert economy_profile.model_label(economy, judge["primary"]) == "Nova Pro"
    assert judge["fallbacks"] == [cheap["primary"]]
    assert judge["primary"] != economy["tiers"]["strong"]["primary"]


def test_only_the_three_chains_differ_from_the_shipped_file() -> None:
    shipped = _shipped()
    economy = economy_profile.build_economy_config(shipped)
    expected = copy.deepcopy(shipped)
    for tier in CHAINED:
        expected["tiers"][tier]["primary"] = economy["tiers"][tier]["primary"]
        expected["tiers"][tier]["fallbacks"] = economy["tiers"][tier]["fallbacks"]
    assert economy == expected
    assert economy["tiers"]["strong"]["budget"] == shipped["tiers"]["strong"]["budget"]
    assert economy["prices_usd_per_mtok"] == shipped["prices_usd_per_mtok"]


def test_building_does_not_modify_its_input() -> None:
    shipped = _shipped()
    before = copy.deepcopy(shipped)
    economy_profile.build_economy_config(shipped)
    assert shipped == before


def test_a_missing_judge_model_is_an_error_not_a_silent_default() -> None:
    shipped = _shipped()
    shipped["models"] = {
        key: value
        for key, value in shipped["models"].items()
        if value["label"] != "Nova Pro"
    }
    shipped["tiers"]["strong"]["fallbacks"] = []
    with pytest.raises(economy_profile.EconomyProfileError, match="Nova Pro"):
        economy_profile.build_economy_config(shipped)


def test_a_missing_tier_is_an_error() -> None:
    shipped = _shipped()
    del shipped["tiers"]["judge"]
    with pytest.raises(economy_profile.EconomyProfileError, match="judge"):
        economy_profile.build_economy_config(shipped)


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
    code = economy_profile.main(["--out", str(precious)])
    assert code == 1
