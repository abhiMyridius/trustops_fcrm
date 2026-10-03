"""Tests for the Index Contract v2.1 additions in ``vector/contract.py``.

Covers: the validator accepts a v2 record unchanged and a v2.1 record with
the three new optional keys; rejects a ``chunk_level=0`` record carrying
``parent_key``; ``shrink_metadata``'s two-step order is deterministic
regardless of dict insertion order; ``text`` is never popped; and the
``NON_FILTERABLE_KEYS`` cap is a runtime ``RuntimeError``, not a
stripped-under-``-O`` ``assert``.
"""

from __future__ import annotations

import pytest
from evoq_context.vector import contract
from evoq_context.vector.contract import (
    FILTERABLE_KEYS,
    NON_FILTERABLE_KEYS,
    MetadataContractViolation,
    MetadataTooLarge,
    join_list_metadata,
    metadata_size_bytes,
    shrink_metadata,
    validate_metadata,
)


def _base_metadata(**overrides) -> dict:
    meta = {
        "doc_id": "file:D1",
        "project": "proj",
        "source_type": "file",
        "source_id": "D1",
        "content_hash": "sha256:" + "a" * 64,
        "chunk_key": "file:D1:aaaaaaaaaaaaaaaa:0000",
        "chunk_index": 0,
        "offset": 0,
        "chunk_strategy": "paragraph_windows",
        "type": "plain_text",
        "snapshot_version": "v1",
        "index_contract": 2,
        "text_truncated": False,
        "text": "hello world",
    }
    meta.update(overrides)
    return meta


# ── v2.1 keys are filterable and optional ───────────────────────────────


def test_v21_keys_are_declared_filterable() -> None:
    assert {"parent_key", "chunk_level", "topic_id"} <= FILTERABLE_KEYS


def test_plain_v2_record_still_validates() -> None:
    validate_metadata(_base_metadata())


def test_v21_child_record_with_parent_key_validates() -> None:
    meta = _base_metadata(
        chunk_key="file:D1:aaaaaaaaaaaaaaaa:0000",
        chunk_level=1,
        parent_key="file:D1:aaaaaaaaaaaaaaaa:0005",
        topic_id="t001",
    )
    validate_metadata(meta)


def test_v21_parent_record_validates_without_parent_key() -> None:
    meta = _base_metadata(chunk_key="file:D1:aaaaaaaaaaaaaaaa:0005", chunk_level=0)
    validate_metadata(meta)


def test_chunk_level_zero_with_parent_key_is_rejected() -> None:
    meta = _base_metadata(
        chunk_key="file:D1:aaaaaaaaaaaaaaaa:0005",
        chunk_level=0,
        parent_key="file:D1:aaaaaaaaaaaaaaaa:0006",
    )
    with pytest.raises(MetadataContractViolation, match="chunk_level=0"):
        validate_metadata(meta)


def test_chunk_level_must_be_zero_or_one() -> None:
    with pytest.raises(MetadataContractViolation):
        validate_metadata(_base_metadata(chunk_level=2))


def test_parent_key_must_start_with_doc_id() -> None:
    meta = _base_metadata(chunk_level=1, parent_key="file:OTHER:aaaaaaaaaaaaaaaa:0005")
    with pytest.raises(MetadataContractViolation, match="does not start with doc_id"):
        validate_metadata(meta)


def test_parent_key_must_have_key_tail_shape() -> None:
    meta = _base_metadata(chunk_level=1, parent_key="file:D1:not-a-hash")
    with pytest.raises(MetadataContractViolation):
        validate_metadata(meta)


def test_parent_key_must_not_equal_own_chunk_key() -> None:
    key = "file:D1:aaaaaaaaaaaaaaaa:0000"
    meta = _base_metadata(chunk_key=key, chunk_level=1, parent_key=key)
    with pytest.raises(MetadataContractViolation, match="must not equal"):
        validate_metadata(meta)


def test_topic_id_must_match_pattern() -> None:
    with pytest.raises(MetadataContractViolation):
        validate_metadata(_base_metadata(topic_id="topic1"))
    validate_metadata(_base_metadata(topic_id="t007"))
    validate_metadata(_base_metadata(topic_id="t1234"))


def test_unknown_key_still_rejected() -> None:
    with pytest.raises(MetadataContractViolation):
        validate_metadata(_base_metadata(made_up_key="x"))


# ── shrink_metadata: deterministic order, text never popped ────────────


def test_shrink_metadata_never_pops_text() -> None:
    meta = {
        "text": "x" * 12_000,
        "strategy_reason": "y" * 30_000,
        "doc_id": "file:D1",
        "chunk_key": "file:D1:aaaaaaaaaaaaaaaa:0000",
    }
    shrunk = shrink_metadata(meta, max_bytes=15_000)
    assert "text" in shrunk
    assert shrunk["text"]


def test_shrink_metadata_order_is_deterministic_regardless_of_dict_order() -> None:
    """Two dicts with the same keys/values in different insertion order must
    shrink to byte-identical results — the fix for the audit's
    insertion-order-dependent last-resort loop."""
    fields = {
        "text": "t" * 12_000,
        "title": "T" * 5_000,
        "section_path": "S" * 5_000,
        "strategy_reason": "R" * 5_000,
        "source_path": "P" * 5_000,
        "hierarchy_path": "H" * 5_000,
        "labels": "l" * 5_000,
        "columns": "c" * 5_000,
        "doc_id": "file:D1",
        "chunk_key": "file:D1:aaaaaaaaaaaaaaaa:0000",
    }
    forward = dict(fields)
    reversed_ = dict(reversed(list(fields.items())))
    shrunk_forward = shrink_metadata(forward, max_bytes=12_500)
    shrunk_reversed = shrink_metadata(reversed_, max_bytes=12_500)
    assert shrunk_forward == shrunk_reversed


def test_shrink_metadata_step1_pops_shrink_order_keys_first() -> None:
    meta = {
        "text": "t" * 100,
        "strategy_reason": "r" * 2000,
        "doc_id": "file:D1",
        "chunk_key": "file:D1:aaaaaaaaaaaaaaaa:0000",
    }
    shrunk = shrink_metadata(meta, max_bytes=300)
    assert "strategy_reason" not in shrunk


def test_shrink_metadata_step2_caps_secondary_fields() -> None:
    meta = {
        "text": "t" * 100,
        "title": "T" * 5000,
        "doc_id": "file:D1",
        "chunk_key": "file:D1:aaaaaaaaaaaaaaaa:0000",
    }
    shrunk = shrink_metadata(meta, max_bytes=400)
    assert len(shrunk["title"]) <= 200


def test_shrink_metadata_raises_metadata_too_large_when_nothing_fits() -> None:
    meta = {
        "text": "t" * 12_000,
        "doc_id": "file:D1",
        "chunk_key": "file:D1:aaaaaaaaaaaaaaaa:0000",
    }
    with pytest.raises(MetadataTooLarge):
        shrink_metadata(meta, max_bytes=100)


def test_shrink_metadata_reports_guardrail_event_when_observer_given() -> None:
    events = []

    class _Recorder:
        def on_guardrail_event(self, event):
            events.append(event)

    meta = {
        "text": "t" * 12_000,
        "strategy_reason": "r" * 30_000,
        "doc_id": "file:D1",
        "chunk_key": "file:D1:aaaaaaaaaaaaaaaa:0000",
    }
    shrink_metadata(meta, max_bytes=15_000, observer=_Recorder())
    assert len(events) == 1
    assert events[0].name == "metadata_shrink"
    assert events[0].outcome == "corrected"


def test_shrink_metadata_is_noop_under_the_cap() -> None:
    meta = {"text": "short", "doc_id": "file:D1"}
    assert shrink_metadata(meta, max_bytes=40_960) == meta


# ── NON_FILTERABLE_KEYS cap is a real runtime check ─────────────────────


def test_non_filterable_keys_cap_is_ten() -> None:
    assert len(NON_FILTERABLE_KEYS) <= 10


def test_non_filterable_cap_check_is_a_real_function_not_a_bare_assert() -> None:
    """The module-level ``assert`` (stripped under ``python -O``) became a
    real function the module calls at import time; call it directly here so
    a future regression (someone appending an 11th key) fails loudly even
    under -O."""
    contract._check_non_filterable_cap()  # no raise today
    original = contract.NON_FILTERABLE_KEYS
    try:
        contract.NON_FILTERABLE_KEYS = original + ("eleventh",)
        with pytest.raises(RuntimeError):
            contract._check_non_filterable_cap()
    finally:
        contract.NON_FILTERABLE_KEYS = original


# ── one list-join convention ─────────────────────────────────────────────


def test_join_list_metadata_uses_comma_and_caps_at_twenty() -> None:
    assert join_list_metadata(["a", "b", "c"]) == "a,b,c"
    values = [f"id{i}" for i in range(30)]
    joined = join_list_metadata(values)
    assert joined.count(",") == 19


def test_metadata_size_bytes_is_the_utf8_json_size() -> None:
    import json

    meta = {"text": "hello", "doc_id": "file:D1"}
    assert metadata_size_bytes(meta) == len(
        json.dumps(meta, ensure_ascii=False).encode("utf-8")
    )
