"""``fcrm_seed.gen_seed``: the generator behind ``seed.json`` is a fixed point,
and scene 5's three reusable values live on the closest precedent."""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest
from fcrm_seed import gen_seed
from fcrm_seed.loader import demo_case, load_raw

PRECEDENT = "FCRM-2026-0118"


def _seed() -> dict[str, Any]:
    return copy.deepcopy(load_raw())


def _case(seed: dict[str, Any], case_id: str) -> dict[str, Any]:
    return next(c for c in seed["cases"] if c["id"] == case_id)


def test_the_precedent_carries_exactly_the_three_reused_values() -> None:
    values = _case(load_raw(), PRECEDENT)["field_values"]
    assert [v["id"] for v in values] == ["F31", "F33", "F34"]
    reused = [f for f in demo_case()["fields"] if f["how"] == "reused"]
    assert [(v["id"], v["value"]) for v in values] == [
        (f["id"], f["value"]) for f in reused
    ]


def test_each_reused_value_names_its_field_and_where_it_came_from() -> None:
    labels = {
        "F31": "Transaction limits and velocity",
        "F33": "Disclosure text reference",
        "F34": "Monitoring rule coverage",
    }
    for value in _case(load_raw(), PRECEDENT)["field_values"]:
        assert value["label"] == labels[value["id"]]
        assert value["source"].startswith(PRECEDENT)


def test_no_other_case_gets_field_values() -> None:
    others = [c for c in load_raw()["cases"] if c["id"] != PRECEDENT]
    assert len(others) == 79
    assert not any("field_values" in c for c in others)


def test_the_demo_case_still_matches_the_decision_log() -> None:
    demo = demo_case()
    assert (demo["score_before"]["composite"], demo["score_before"]["band"]) == (9.8, 2)
    assert (demo["score_after"]["composite"], demo["score_after"]["band"]) == (10.4, 3)


def test_augment_is_idempotent_and_leaves_its_input_alone() -> None:
    base = _seed()
    snapshot = copy.deepcopy(base)
    once = gen_seed.augment(base)
    assert base == snapshot
    assert gen_seed.augment(once) == once


def test_the_committed_seed_is_a_fixed_point_of_the_generator() -> None:
    committed = gen_seed.SEED_PATH.read_bytes()
    assert gen_seed.serialise(gen_seed.augment(json.loads(committed))) == committed


def test_check_mode_is_green_on_the_committed_seed() -> None:
    assert gen_seed.main(["--check"]) == 0


def test_a_precedent_that_is_not_closed_is_refused() -> None:
    seed = _seed()
    _case(seed, PRECEDENT)["stage"] = "under_assessment"
    with pytest.raises(gen_seed.SeedGenerationError, match="closed case"):
        gen_seed.add_precedent_field_values(seed)


def test_a_demo_without_three_reused_fields_is_refused() -> None:
    seed = _seed()
    for field in seed["demo"]["fields"]:
        if field["id"] == "F34":
            field["how"] = "extracted"
    with pytest.raises(gen_seed.SeedGenerationError, match="expected 3"):
        gen_seed.add_precedent_field_values(seed)


def test_a_reused_field_outside_the_field_set_is_refused() -> None:
    seed = _seed()
    for field in seed["demo"]["fields"]:
        if field["id"] == "F34":
            field["id"] = "F99"
    with pytest.raises(gen_seed.SeedGenerationError, match="not in field set"):
        gen_seed.add_precedent_field_values(seed)


def test_serialise_uses_the_committed_on_disk_form() -> None:
    raw = gen_seed.serialise({"a": ["é", 1]})
    assert raw == b'{\n "a": [\n  "\\u00e9",\n  1\n ]\n}\n'
