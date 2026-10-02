"""fcrm_common.hashing: canonical JSON determinism and the S4 hash chain
(design A §2.4.1).
"""

from __future__ import annotations

import hashlib
import itertools
import math
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fcrm_common.hashing import canonical_json, chain_hash, genesis_hash, sha256_hex
from fcrm_contracts.enums import Band, Role

# Known vectors, computed once with hashlib directly (see the test names).
CHAIN_PREV = "0" * 64
CHAIN_BODY = '{"a":1,"b":[true,null,"é"]}'.encode()
CHAIN_EXPECTED = "59f855f956e43aa71ddd58da623b1d58fc2cdc44994db813f73a39d3dbf4aa91"
GENESIS_0151 = "1cdb1ae6eae95c49e0cec669c1713dec67ca0ee4d4d8a6e036fe9730180d5b1b"


def test_canonical_json_is_independent_of_key_order():
    items = [
        ("case_id", "FCRM-2026-0151"),
        ("seq", 49),
        ("payload", {"before": 7.6, "after": 4.5, "item_id": "CTL-T01"}),
        ("actor", {"role": "fcrm_analyst", "id": "u-riyer"}),
        ("what", "CTL-T01 7.6 → 4.5"),
    ]
    encodings = {canonical_json(dict(p)) for p in itertools.permutations(items)}
    assert len(encodings) == 1


def test_canonical_json_nested_key_order_is_irrelevant():
    a = {"outer": {"z": 1, "a": {"y": 2, "b": 3}}}
    b = {"outer": {"a": {"b": 3, "y": 2}, "z": 1}}
    assert canonical_json(a) == canonical_json(b)


def test_canonical_json_exact_bytes():
    value = {"b": [True, None, "é"], "a": 1}
    assert canonical_json(value) == CHAIN_BODY


def test_canonical_json_renders_datetime_in_utc_millis():
    plus_two = timezone(timedelta(hours=2))
    value = {"at": datetime(2026, 10, 1, 17, 2, 11, 482999, tzinfo=plus_two)}
    assert canonical_json(value) == b'{"at":"2026-10-01T15:02:11.482Z"}'


def test_canonical_json_renders_date_decimal_and_enum():
    value = {
        "d": date(2026, 10, 9),
        "x": Decimal("7.60"),
        "band": Band.BAND_3,
        "role": Role.ANALYST,
        "z": Decimal("-0.0"),
    }
    assert canonical_json(value) == (
        b'{"band":"3","d":"2026-10-09","role":"fcrm_analyst","x":"7.6","z":"0"}'
    )


@pytest.mark.parametrize(
    "bad",
    [
        {"at": datetime(2026, 10, 1, 12, 0)},  # naive datetime
        {"tags": {"a", "b"}},  # set has no canonical order
        {1: "int key"},
        {"raw": b"bytes"},
    ],
)
def test_canonical_json_rejects_values_without_canonical_form(bad):
    with pytest.raises(TypeError):
        canonical_json(bad)


@pytest.mark.parametrize("bad", [math.nan, math.inf, Decimal("NaN")])
def test_canonical_json_rejects_non_finite_numbers(bad):
    with pytest.raises(ValueError, match="rejects"):
        canonical_json({"x": bad})


def test_sha256_hex_matches_hashlib():
    assert sha256_hex(b"abc") == hashlib.sha256(b"abc").hexdigest()


def test_chain_hash_known_vector():
    assert chain_hash(CHAIN_PREV, CHAIN_BODY) == CHAIN_EXPECTED


def test_chain_hash_is_prev_newline_body():
    expected = hashlib.sha256(f"{CHAIN_PREV}\n".encode() + CHAIN_BODY).hexdigest()
    assert chain_hash(CHAIN_PREV, CHAIN_BODY) == expected


def test_chain_hash_changes_when_prev_or_body_changes():
    base = chain_hash(CHAIN_PREV, CHAIN_BODY)
    assert chain_hash("1" + CHAIN_PREV[1:], CHAIN_BODY) != base
    assert chain_hash(CHAIN_PREV, CHAIN_BODY + b" ") != base


def test_genesis_hash_known_vector():
    assert genesis_hash("FCRM-2026-0151") == GENESIS_0151


def test_chain_of_three_records_verifies():
    case_id = "FCRM-2026-0151"
    bodies = [
        canonical_json(
            {
                "case_id": case_id,
                "seq": i,
                "type": "x",
                "occurred_at": datetime(2026, 10, 1, tzinfo=UTC),
            }
        )
        for i in range(1, 4)
    ]
    prev = genesis_hash(case_id)
    hashes = []
    for body in bodies:
        prev = chain_hash(prev, body)
        hashes.append(prev)
    replay = genesis_hash(case_id)
    for body, stored in zip(bodies, hashes, strict=True):
        replay = chain_hash(replay, body)
        assert replay == stored
