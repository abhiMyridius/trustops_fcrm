"""``fcrm_case.store.InMemoryCaseStore``: the conditional-update contract
every write method shares (closed/already-submitted cases reject writes),
and pagination.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fcrm_case.domain.fieldset import FieldDef
from fcrm_case.domain.record import (
    CaseRecord,
    FieldValueRecord,
    SubmissionVersionRecord,
)
from fcrm_case.store import InMemoryCaseStore
from fcrm_common.events import build_envelope
from fcrm_contracts.enums import CaseState, ChangeType
from fcrm_contracts.events.payloads import CaseEvent
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


def _event(case_id: str, event_type: str) -> object:
    return build_envelope(
        event_type,
        CaseEvent(case_id=case_id, actor="u-priya").model_dump(mode="json"),
        case_id=case_id,
    )


@pytest.fixture
def store() -> InMemoryCaseStore:
    return InMemoryCaseStore()


@pytest.mark.asyncio
async def test_create_then_get_round_trips_the_record(store: InMemoryCaseStore) -> None:
    record = _record("FCRM-2026-0200")
    await store.create(record, _event("FCRM-2026-0200", "case.created"))
    fetched = await store.get("FCRM-2026-0200")
    assert fetched is not None
    assert fetched.case_id == "FCRM-2026-0200"
    assert store.events[0].type == "case.created"


@pytest.mark.asyncio
async def test_get_of_an_unknown_case_is_none(store: InMemoryCaseStore) -> None:
    assert await store.get("FCRM-2026-9999") is None


@pytest.mark.asyncio
async def test_write_field_recomputes_against_the_latest_state(
    store: InMemoryCaseStore,
) -> None:
    await store.create(
        _record("FCRM-2026-0201"), _event("FCRM-2026-0201", "case.created")
    )
    field = FieldValueRecord(field_id="F01", label="x", value="hello", filled_at=NOW)
    updated = await store.write_field("FCRM-2026-0201", field)
    assert updated is not None
    assert updated.fields["F01"].value == "hello"


@pytest.mark.asyncio
async def test_write_field_on_a_closed_case_returns_none(
    store: InMemoryCaseStore,
) -> None:
    await store.create(
        _record("FCRM-2026-0202", state=CaseState.CLOSED),
        _event("FCRM-2026-0202", "case.created"),
    )
    field = FieldValueRecord(field_id="F01", label="x", value="hello", filled_at=NOW)
    assert await store.write_field("FCRM-2026-0202", field) is None


@pytest.mark.asyncio
async def test_write_field_on_an_already_submitted_case_returns_none(
    store: InMemoryCaseStore,
) -> None:
    await store.create(
        _record("FCRM-2026-0203", submitted_at=NOW),
        _event("FCRM-2026-0203", "case.created"),
    )
    field = FieldValueRecord(field_id="F01", label="x", value="hello", filled_at=NOW)
    assert await store.write_field("FCRM-2026-0203", field) is None


@pytest.mark.asyncio
async def test_submit_is_one_shot(store: InMemoryCaseStore) -> None:
    await store.create(
        _record("FCRM-2026-0204"), _event("FCRM-2026-0204", "case.created")
    )
    version = SubmissionVersionRecord(
        version=1, created_at=NOW, created_by="u-priya", field_values={"F01": "hello"}
    )
    first = await store.submit(
        "FCRM-2026-0204", version, _event("FCRM-2026-0204", "case.submitted")
    )
    assert first is not None
    assert first.submitted_at == NOW

    second_version = SubmissionVersionRecord(
        version=2, created_at=NOW, created_by="u-priya", field_values={"F01": "again"}
    )
    second = await store.submit(
        "FCRM-2026-0204", second_version, _event("FCRM-2026-0204", "case.submitted")
    )
    assert second is None  # already submitted -- rejected, not overwritten


@pytest.mark.asyncio
async def test_list_filters_by_owner_and_paginates(store: InMemoryCaseStore) -> None:
    for i in range(3):
        case_id = f"FCRM-2026-030{i}"
        await store.create(
            _record(case_id, owner="u-priya" if i < 2 else "u-other"),
            _event(case_id, "case.created"),
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
