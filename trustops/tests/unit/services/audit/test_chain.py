"""Tests for the hash chain: determinism, genesis, tamper detection, and a
full replay of the demo case's 67-record audit trail from the seed.
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime

import pytest
from fcrm_audit.chain import (
    GENESIS_HASH,
    build_record,
    canonical_json,
    chain_hash,
    verify_chain,
)
from fcrm_audit.seedmodel import build_demo_chain
from fcrm_contracts.api.audit import RecordAppend
from fcrm_seed.loader import demo_case
from hypothesis import given
from hypothesis import strategies as st


def test_canonical_json_is_order_independent() -> None:
    a = {"z": 1, "a": 2, "data": {"b": 1, "a": 2}}
    b = {"a": 2, "z": 1, "data": {"a": 2, "b": 1}}
    assert canonical_json(a) == canonical_json(b)


def test_chain_hash_matches_the_documented_formula() -> None:
    """hash = sha256(prev_hash + "\\n" + canonical_body) — literally."""
    import hashlib

    body = {"x": 1}
    expected = hashlib.sha256(b"0" * 64 + b"\n" + canonical_json(body)).hexdigest()
    assert chain_hash("0" * 64, body) == expected


def test_first_record_uses_the_genesis_hash() -> None:
    append = RecordAppend(
        case_id="FCRM-2026-TEST", type="case.created", actor="S1", summary="x"
    )
    record = build_record(
        append, seq=1, prev_hash=GENESIS_HASH, created_at=datetime.now(UTC)
    )
    assert record.prev_hash == GENESIS_HASH
    assert record.seq == 1


def test_a_two_record_chain_verifies() -> None:
    append1 = RecordAppend(
        case_id="C1", type="case.created", actor="S1", summary="created"
    )
    r1 = build_record(
        append1, seq=1, prev_hash=GENESIS_HASH, created_at=datetime.now(UTC)
    )
    append2 = RecordAppend(
        case_id="C1", type="case.submitted", actor="S1", summary="submitted"
    )
    r2 = build_record(append2, seq=2, prev_hash=r1.hash, created_at=datetime.now(UTC))

    result = verify_chain("C1", [r1, r2])
    assert result.ok is True
    assert result.record_count == 2
    assert result.first_break_seq is None


def test_tampering_with_a_record_s_data_breaks_verification() -> None:
    """A tampered ``summary`` (or any field in the hashed body) must flip
    ``ok`` to False and report the first broken ``seq`` — this is the
    whole point of the chain, so it must actually catch a real mutation,
    not just a malformed input.
    """
    demo_dict = demo_case()
    records = build_demo_chain(demo_dict)
    assert verify_chain(demo_dict["id"], records).ok is True

    tampered = copy.deepcopy(records)
    tampered[30] = tampered[30].model_copy(update={"summary": "tampered text"})

    result = verify_chain(demo_dict["id"], tampered)
    assert result.ok is False
    assert result.first_break_seq == 31  # seq is 1-indexed; index 30 -> seq 31


def test_reordering_two_records_breaks_verification() -> None:
    demo_dict = demo_case()
    records = build_demo_chain(demo_dict)

    reordered = list(records)
    reordered[10], reordered[11] = reordered[11], reordered[10]

    result = verify_chain(demo_dict["id"], reordered)
    assert result.ok is False


def test_the_demo_case_s_67_record_audit_trail_verifies_end_to_end() -> None:
    """Replays the seed's full narrated audit trail for FCRM-2026-0151
    through the real chain-building and verification code
    (docs/requirements/research/06-walkthrough-seed-extraction.md §3.10).
    """
    demo_dict = demo_case()
    assert demo_dict["audit_count"] == 67

    records = build_demo_chain(demo_dict)
    assert len(records) == 67
    assert [r.seq for r in records] == list(range(1, 68))

    result = verify_chain(demo_dict["id"], records)
    assert result.ok is True
    assert result.record_count == 67

    # The override record (#49) and the recompute (#50) must exist with
    # the right types, per the walkthrough's audit table.
    by_seq = {r.seq: r for r in records}
    assert by_seq[49].type == "human.override"
    assert by_seq[50].type == "score.recomputed"
    assert by_seq[67].type == "chain.verified"


@given(
    st.lists(
        st.fixed_dictionaries(
            {
                "type": st.sampled_from(
                    ["case.created", "case.submitted", "gate.decided"]
                ),
                "actor": st.sampled_from(["S1", "H1", "analyst"]),
                "summary": st.text(min_size=1, max_size=40),
            }
        ),
        min_size=1,
        max_size=20,
    )
)
def test_any_sequence_of_appends_produces_a_chain_that_verifies(
    entries: list[dict],
) -> None:
    """Property test: whatever the content, a correctly-built chain always
    verifies — the hash never depends on anything but (prev_hash, body).
    """
    prev_hash = GENESIS_HASH
    records = []
    for i, entry in enumerate(entries, start=1):
        append = RecordAppend(case_id="PROP", **entry)
        record = build_record(
            append, seq=i, prev_hash=prev_hash, created_at=datetime.now(UTC)
        )
        records.append(record)
        prev_hash = record.hash

    assert verify_chain("PROP", records).ok is True


def test_empty_chain_verifies_trivially() -> None:
    result = verify_chain("EMPTY", [])
    assert result.ok is True
    assert result.record_count == 0


def _third_party_recompute(served: dict) -> str:
    """What an outside verifier does with one item of ``GET /records``: drop
    ``prev_hash``/``hash``, sorted-key compact JSON, sha256 over
    ``prev_hash + "\\n" + body``. Uses only the standard library."""
    import hashlib
    import json

    body = {k: v for k, v in served.items() if k not in ("prev_hash", "hash")}
    canonical = json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(
        served["prev_hash"].encode("utf-8") + b"\n" + canonical
    ).hexdigest()


def test_hash_is_reproducible_from_the_served_json_mode_representation() -> None:
    """The served ``created_at`` prints ``Z``; the hash used to be computed
    over ``isoformat()``'s ``+00:00``, so nobody outside could recompute it."""
    append = RecordAppend(
        case_id="C1", type="case.created", actor="S1", summary="créé", data={"n": 1}
    )
    record = build_record(
        append,
        seq=1,
        prev_hash=GENESIS_HASH,
        created_at=datetime(2026, 10, 1, 9, 0, 1, 123000, tzinfo=UTC),
    )
    served = record.model_dump(mode="json")
    assert served["created_at"].endswith("Z")
    assert _third_party_recompute(served) == record.hash


def test_verify_chain_rejects_a_record_from_another_case() -> None:
    append = RecordAppend(case_id="C1", type="case.created", actor="S1", summary="x")
    r1 = build_record(
        append, seq=1, prev_hash=GENESIS_HASH, created_at=datetime.now(UTC)
    )
    foreign = RecordAppend(case_id="C2", type="case.submitted", actor="S1", summary="y")
    r2 = build_record(foreign, seq=2, prev_hash=r1.hash, created_at=datetime.now(UTC))

    result = verify_chain("C1", [r1, r2])
    assert result.ok is False
    assert result.first_break_seq == 2


def test_canonical_json_rejects_nan_with_a_clear_error() -> None:
    with pytest.raises(ValueError, match="NaN"):
        canonical_json({"x": float("nan")})


def test_canonical_json_rejects_a_lone_surrogate_with_a_clear_error() -> None:
    with pytest.raises(ValueError, match="surrogate"):
        canonical_json({"s": "bad \ud800 text"})


@pytest.mark.parametrize(
    "field",
    [
        {"summary": "lone \ud800 surrogate"},
        {"actor": "\udfff"},
        {"data": {"note": ["ok", {"deep": "\ud800"}]}},
        {"data": {"\ud800": "key"}},
        {"data": {"x": float("nan")}},
        {"data": {"x": [float("inf")]}},
    ],
)
def test_record_append_rejects_unhashable_content_at_the_contract(field: dict) -> None:
    """A 422 from Pydantic, long before ``canonical_json`` could 500."""
    from pydantic import ValidationError

    body = {"case_id": "C1", "type": "t", "actor": "a", "summary": "s", **field}
    with pytest.raises(ValidationError):
        RecordAppend(**body)
