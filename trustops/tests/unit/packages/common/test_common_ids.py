"""fcrm_common.ids: prefixed ULIDs and case ids (design A §1.4)."""

from __future__ import annotations

import re

import pytest
from fcrm_common.ids import deterministic_id, mint_case_id, new_id, parse_case_id
from ulid import ULID

ULID_CHARS = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
BASE32_TOKEN = re.compile(r"^[a-z2-7]{26}$")


@pytest.mark.parametrize("prefix", ["evt", "job", "run", "gate", "pack", "thr", "cnd"])
def test_new_id_has_prefix_and_ulid(prefix):
    value = new_id(prefix)
    head, _, tail = value.partition("_")
    assert head == prefix
    assert ULID_CHARS.match(tail)
    assert str(ULID.from_str(tail)) == tail


def test_new_id_is_unique_and_time_sortable():
    ids = [new_id("evt") for _ in range(200)]
    assert len(set(ids)) == 200
    first, last = ULID.from_str(ids[0][4:]), ULID.from_str(ids[-1][4:])
    assert first.milliseconds <= last.milliseconds


@pytest.mark.parametrize("bad", ["", "EVT", "evt_", "1evt", "e-vt", "a" * 17])
def test_new_id_rejects_bad_prefix(bad):
    with pytest.raises(ValueError, match="invalid id prefix"):
        new_id(bad)


def test_mint_case_id_pads_sequence():
    assert mint_case_id(2026, 152) == "FCRM-2026-0152"
    assert mint_case_id(2026, 1) == "FCRM-2026-0001"
    assert mint_case_id(2027, 9999) == "FCRM-2027-9999"


@pytest.mark.parametrize(("year", "seq"), [(2026, 0), (2026, 10000), (26, 1)])
def test_mint_case_id_rejects_out_of_range(year, seq):
    with pytest.raises(ValueError, match="case id"):
        mint_case_id(year, seq)


def test_parse_case_id_round_trips():
    assert parse_case_id(mint_case_id(2026, 151)) == (2026, 151)
    with pytest.raises(ValueError, match="not a case id"):
        parse_case_id("FCRM-2026-151")


def test_deterministic_id_is_stable_for_the_same_parts() -> None:
    first = deterministic_id("step", "run_1", "intake.publish", "evt_1")
    again = deterministic_id("step", "run_1", "intake.publish", "evt_1")
    assert first == again


def test_deterministic_id_honours_the_prefix_and_is_lowercase_base32() -> None:
    value = deterministic_id("gate", "run_1", "review_item", "CTL-T01")
    head, _, tail = value.partition("_")
    assert head == "gate"
    assert BASE32_TOKEN.match(tail)
    assert "=" not in value


def test_deterministic_id_differs_for_distinct_parts() -> None:
    ids = {
        deterministic_id("gate", "run_1", "review_item", "CTL-T01"),
        deterministic_id("gate", "run_1", "review_item", "CTL-T02"),
        deterministic_id("gate", "run_2", "review_item", "CTL-T01"),
        deterministic_id("gate", "run_1", "ack", "CTL-T01"),
        deterministic_id("gate", "run_1", "review_item"),
    }
    assert len(ids) == 5


def test_deterministic_id_part_order_matters() -> None:
    assert deterministic_id("x", "a", "b") != deterministic_id("x", "b", "a")


def test_deterministic_id_prefix_changes_the_id() -> None:
    a = deterministic_id("step", "run_1")
    b = deterministic_id("gate", "run_1")
    assert a.partition("_")[2] == b.partition("_")[2]
    assert a != b


def test_deterministic_id_allows_an_empty_part() -> None:
    assert deterministic_id("gate", "run_1", "completeness", "") != deterministic_id(
        "gate", "run_1", "completeness"
    )


@pytest.mark.parametrize("bad", ["", "EVT", "1evt", "e-vt", "a" * 17])
def test_deterministic_id_rejects_a_bad_prefix(bad: str) -> None:
    with pytest.raises(ValueError, match="invalid id prefix"):
        deterministic_id(bad, "run_1")


def test_deterministic_id_needs_at_least_one_part() -> None:
    with pytest.raises(ValueError, match="at least one part"):
        deterministic_id("step")


def test_deterministic_id_rejects_a_part_holding_the_separator() -> None:
    """``("a|b", "c")`` and ``("a", "b|c")`` would hash alike."""
    with pytest.raises(ValueError, match="part separator"):
        deterministic_id("step", "a|b", "c")
