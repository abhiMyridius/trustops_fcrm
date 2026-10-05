"""``fcrm_case.store.InMemoryCaseStore``: reads, pagination and the case-id
counter. (The product owner's writes are ``fcrm_case.po_store``'s, tested in
``test_po_store.py``; H1's state and assignment writes in
``test_case_store_state.py``.)
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fcrm_case.domain.fieldset import FieldDef
from fcrm_case.domain.record import CaseRecord, FieldValueRecord
from fcrm_case.store import InMemoryCaseStore
from fcrm_contracts.enums import ChangeType
from fcrm_contracts.versions import PinnedVersions

NOW = datetime(2026, 1, 12, tzinfo=UTC)
PINNED = PinnedVersions(
    scoring_v=3,
    corpus_v=14,
    controls_v=9,
    fieldset_v=6,
    jurisdictions_v="v2026-06",
    routing_v=4,
    prompts_v="2026-09-28",
)


def _record(case_id: str, **overrides: object) -> CaseRecord:
    base: dict[str, object] = dict(
        case_id=case_id,
        title="Consumer remittances pilot",
        unit="Payments",
        owner="u-priya",
        created_by="u-priya",
        change_types=[ChangeType.VENDOR_ONBOARDING],
        fieldset_v=6,
        completion_threshold=0.8,
        pinned_versions=PINNED,
        required_fields=[FieldDef(field_id="F01", label="x", always_required=True)],
        created_at=NOW,
    )
    base.update(overrides)
    return CaseRecord(**base)  # type: ignore[arg-type]


@pytest.fixture
def store() -> InMemoryCaseStore:
    return InMemoryCaseStore()


def put(store: InMemoryCaseStore, record: CaseRecord) -> None:
    store.records[record.case_id] = record


@pytest.mark.asyncio
async def test_get_round_trips_a_stored_record(store: InMemoryCaseStore) -> None:
    put(store, _record("FCRM-2026-0200"))
    fetched = await store.get("FCRM-2026-0200")
    assert fetched is not None
    assert fetched.case_id == "FCRM-2026-0200"


@pytest.mark.asyncio
async def test_get_of_an_unknown_case_is_none(store: InMemoryCaseStore) -> None:
    assert await store.get("FCRM-2026-9999") is None


@pytest.mark.asyncio
async def test_list_filters_by_owner_and_paginates(store: InMemoryCaseStore) -> None:
    for i in range(3):
        put(
            store, _record(f"FCRM-2026-030{i}", owner="u-priya" if i < 2 else "u-other")
        )
    page = await store.list(owner="u-priya", limit=1)
    assert len(page.items) == 1
    assert page.total == 2
    assert page.next_cursor is not None

    next_page = await store.list(owner="u-priya", limit=1, cursor=page.next_cursor)
    assert len(next_page.items) == 1
    assert next_page.items[0].case_id != page.items[0].case_id
    assert next_page.next_cursor is None


@pytest.mark.asyncio
async def test_next_case_id_increments_per_year(store: InMemoryCaseStore) -> None:
    first = await store.next_case_id(2026)
    second = await store.next_case_id(2026)
    other_year = await store.next_case_id(2027)
    assert first == "FCRM-2026-0001"
    assert second == "FCRM-2026-0002"
    assert other_year == "FCRM-2027-0001"


@pytest.mark.asyncio
async def test_list_rejects_a_non_positive_limit_instead_of_slicing(
    store: InMemoryCaseStore,
) -> None:
    """``limit=-1`` used to slice ``[0:-1]`` and silently drop the last row;
    the store now refuses it the way ``Repository.find_paginated`` does."""
    for i in range(3):
        put(store, _record(f"FCRM-2026-040{i}"))
    with pytest.raises(ValueError, match="limit"):
        await store.list(limit=-1)
    with pytest.raises(ValueError, match="limit"):
        await store.list(limit=0)


@pytest.mark.parametrize("blank", ["", "   ", "\n\t ", " "])
def test_a_whitespace_only_value_is_not_a_filled_field(blank: str) -> None:
    record = _record(
        "FCRM-2026-0500",
        fields={
            "F01": FieldValueRecord(
                field_id="F01", label="x", value=blank, filled_at=NOW
            ),
            "F02": FieldValueRecord(
                field_id="F02", label="y", value=" ok ", filled_at=NOW
            ),
        },
    )
    assert record.filled_field_ids == {"F02"}
