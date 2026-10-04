"""BB-04, "audit chain verifies" (design doc numbering; BUILD-PLAN.md's
shorter summary line calls the same rule "audit chain verifies and a
tampered record fails"): ``audit/verify/{case}`` -> ok on an intact
chain; a direct-storage tamper of one record -> ``verify`` reports the
first broken ``seq``.

``fcrm_audit`` has no HTTP layer yet (BUILD-PLAN's status table: "chain
core (9 tests, full 67-record demo replay); HTTP/Mongo pending common"),
so there is no real ``GET /verify/{case_id}`` route to drive -- this
tests the real ``fcrm_audit.chain.verify_chain`` function directly, which
*is* what that route will call once it exists (it is already proven
against the full, real 67-record demo chain in ``tests/unit/services/
audit/test_chain.py``).

That unit test tampers exactly one record (index 30) once. This file is
additive, not a duplicate: it sweeps tampering across *every* one of the
67 real demo records (not one hand-picked position), and across the
other ways a chain can be corrupted besides a ``summary`` edit -- the
``hash`` field itself, the ``prev_hash`` link, and the ``data`` payload --
proving detection holds everywhere in the chain, not just at the one
position the existing test happened to pick.
"""

from __future__ import annotations

from typing import Any

import pytest
from fcrm_audit.chain import verify_chain
from fcrm_audit.seedmodel import build_demo_chain

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def intact_chain(demo: dict[str, Any]) -> list[Any]:
    records = build_demo_chain(demo)
    assert verify_chain(demo["id"], records).ok is True
    return records


def test_the_intact_67_record_chain_verifies_cleanly(
    demo: dict[str, Any], intact_chain: list[Any]
) -> None:
    result = verify_chain(demo["id"], intact_chain)
    assert result.ok is True
    assert result.record_count == 67
    assert result.first_break_seq is None


@pytest.mark.parametrize("position", range(67))
def test_tampering_the_summary_at_any_single_position_is_caught(
    demo: dict[str, Any], intact_chain: list[Any], position: int
) -> None:
    """Every one of the 67 real demo records, tampered one at a time:
    the break is always reported at that exact record's ``seq``
    (1-indexed), never at a different position and never silently passed.
    """
    tampered = list(intact_chain)
    tampered[position] = tampered[position].model_copy(
        update={"summary": f"tampered at position {position}"}
    )

    result = verify_chain(demo["id"], tampered)
    assert result.ok is False
    assert result.first_break_seq == position + 1


@pytest.mark.parametrize("position", [0, 1, 20, 48, 49, 50, 66])
def test_tampering_the_data_payload_is_also_caught(
    demo: dict[str, Any], intact_chain: list[Any], position: int
) -> None:
    """``data`` is hashed too, not just ``summary`` -- a sample of
    positions across the chain, including the override record (#49) and
    the recompute record (#50), the two records a reviewer would most
    plausibly want to quietly edit.
    """
    tampered = list(intact_chain)
    original = tampered[position]
    tampered[position] = original.model_copy(
        update={"data": {**original.data, "scene": 999}}
    )

    result = verify_chain(demo["id"], tampered)
    assert result.ok is False
    assert result.first_break_seq == position + 1


@pytest.mark.parametrize("position", [0, 10, 33, 65])
def test_tampering_the_stored_hash_itself_is_caught(
    demo: dict[str, Any], intact_chain: list[Any], position: int
) -> None:
    """A tamperer who edits the *hash* field directly (not just the
    content, hoping the stored hash is never recomputed) is equally
    caught: ``verify_chain`` always recomputes from ``prev_hash`` and the
    canonical body, never trusts the stored ``hash`` at face value.
    """
    tampered = list(intact_chain)
    original = tampered[position]
    tampered[position] = original.model_copy(update={"hash": "0" * 64})

    result = verify_chain(demo["id"], tampered)
    assert result.ok is False
    assert result.first_break_seq == position + 1


@pytest.mark.parametrize("position", [1, 15, 40, 66])
def test_relinking_a_record_to_the_wrong_prev_hash_is_caught(
    demo: dict[str, Any], intact_chain: list[Any], position: int
) -> None:
    """A tamperer who splices in a different ``prev_hash`` (for example to
    make a deleted record's removal invisible by re-linking around it)
    breaks verification at that record, not one position off.
    """
    tampered = list(intact_chain)
    tampered[position] = tampered[position].model_copy(update={"prev_hash": "f" * 64})

    result = verify_chain(demo["id"], tampered)
    assert result.ok is False
    assert result.first_break_seq == position + 1


def test_truncating_the_chain_mid_sequence_is_caught_by_the_next_record_s_link(
    demo: dict[str, Any], intact_chain: list[Any]
) -> None:
    """Deleting a record outright (not editing it) breaks the next
    surviving record's ``prev_hash`` match, since nothing re-links around
    the gap -- the append-only, hash-chained design's actual defence
    against silent deletion, demonstrated rather than assumed.
    """
    truncated = intact_chain[:30] + intact_chain[31:]  # drop seq 31 entirely
    result = verify_chain(demo["id"], truncated)
    assert result.ok is False
    # The record that used to be seq 32 now sits at index 30 (expecting
    # seq 31 there), so verify_chain's own seq-gap check reports *its*
    # stored seq (32), not the position it was found at.
    assert result.first_break_seq == 32
