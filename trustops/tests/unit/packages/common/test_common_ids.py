"""fcrm_common.ids: prefixed ULIDs and case ids (design A §1.4)."""

from __future__ import annotations

import re

import pytest
from fcrm_common.ids import mint_case_id, new_id, parse_case_id
from ulid import ULID

ULID_CHARS = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")


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
