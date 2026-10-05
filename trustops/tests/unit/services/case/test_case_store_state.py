"""The case store's H1-facing writes: ``apply_state``, ``assign_analyst``,
``open_case_counts``, ``exists`` and the lean ``get``, and the activity log they
write through, against the in-memory store (the Mongo twin is exercised in
``tests/integration/live_mongo/test_case_foundation_live.py``).
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from fcrm_case.domain.record import CaseRecord, SubmissionVersionRecord
from fcrm_case.store import InMemoryCaseStore
from fcrm_common.time import utcnow
from fcrm_contracts.api.case_internal import StatePatch, UiActor, UiEventIn
from fcrm_contracts.enums import CaseState

ACTOR = UiActor(id="svc:caseflow", role="fcrm_service", component="caseflow")
CASE = "FCRM-2026-0151"


async def test_a_stage_change_is_written_with_its_activity_row_and_a_seq(
    case_store: InMemoryCaseStore, stored_case: CaseRecord
) -> None:
    write = await case_store.apply_state(
        CASE, StatePatch(stage=CaseState.SCREENING, run_id="run_1"), ACTOR
    )
    assert write is not None
    assert write.record.state == CaseState.SCREENING
    assert write.record.run_id == "run_1"
    assert write.record.activity_seq == 1
    assert write.entry is not None
    assert (write.entry.seq, write.entry.type, write.entry.visibility) == (
        1,
        "case.stage_changed",
        "all",
    )
    assert (await case_store.get(CASE)) == write.record


async def test_replaying_the_same_patch_changes_nothing_and_adds_no_row(
    case_store: InMemoryCaseStore, stored_case: CaseRecord
) -> None:
    patch = StatePatch(stage=CaseState.SCREENING, run_id="run_1", round_open=True)
    first = await case_store.apply_state(CASE, patch, ACTOR)
    again = await case_store.apply_state(CASE, patch, ACTOR)
    assert first is not None
    assert again is not None
    assert again.entry is None
    assert again.record == first.record
    assert len(case_store.activity.entries) == 1


async def test_a_flag_only_patch_changes_the_flag_and_not_the_stage_or_the_log(
    case_store: InMemoryCaseStore, stored_case: CaseRecord
) -> None:
    write = await case_store.apply_state(
        CASE, StatePatch(stage=CaseState.INTAKE, fast_lane_proposed=True), ACTOR
    )
    assert write is not None
    assert write.entry is None
    assert write.record.stage_flags.fast_lane_proposed is True
    assert write.record.stage_flags.round_open is False
    assert write.record.state == CaseState.INTAKE


async def test_apply_state_is_not_gated_on_the_case_being_open_for_writes(
    case_store: InMemoryCaseStore, stored_case: CaseRecord
) -> None:
    """H1 decides every transition, including the one into closed."""
    await case_store.apply_state(CASE, StatePatch(stage=CaseState.DECIDE), ACTOR)
    closed = await case_store.apply_state(
        CASE, StatePatch(stage=CaseState.CLOSED), ACTOR
    )
    assert closed is not None
    assert closed.record.state == CaseState.CLOSED
    assert closed.record.closed_at is not None


async def test_apply_state_on_an_unknown_case_is_none(
    case_store: InMemoryCaseStore,
) -> None:
    assert (
        await case_store.apply_state(
            "FCRM-2026-9999", StatePatch(stage=CaseState.TRIAGE), ACTOR
        )
        is None
    )


async def test_assigning_an_analyst_writes_a_po_row_with_the_name_only(
    case_store: InMemoryCaseStore, stored_case: CaseRecord
) -> None:
    write = await case_store.assign_analyst(CASE, "u-riyer", "Rahul Iyer", ACTOR)
    assert write is not None
    assert (write.record.analyst_id, write.record.analyst_name) == (
        "u-riyer",
        "Rahul Iyer",
    )
    assert write.entry is not None
    assert write.entry.visibility == "po"
    assert write.entry.detail == {"analyst_name": "Rahul Iyer"}


async def test_assigning_the_analyst_already_assigned_is_a_replay(
    case_store: InMemoryCaseStore, stored_case: CaseRecord
) -> None:
    await case_store.assign_analyst(CASE, "u-riyer", "Rahul Iyer", ACTOR)
    again = await case_store.assign_analyst(CASE, "u-riyer", "Rahul Iyer", ACTOR)
    assert again is not None
    assert again.entry is None
    assert len(case_store.activity.entries) == 1
    reassigned = await case_store.assign_analyst(CASE, "u-mshah", "Meera Shah", ACTOR)
    assert reassigned is not None
    assert reassigned.entry is not None
    assert reassigned.entry.seq == 2


async def test_assign_on_an_unknown_case_is_none(case_store: InMemoryCaseStore) -> None:
    assert (
        await case_store.assign_analyst("FCRM-2026-9999", "u-riyer", "R", ACTOR) is None
    )


async def test_activity_seq_is_monotonic_and_gap_free_across_writers(
    case_store: InMemoryCaseStore,
    stored_case: CaseRecord,
    make_event: Callable[..., UiEventIn],
) -> None:
    await case_store.apply_state(CASE, StatePatch(stage=CaseState.TRIAGE), ACTOR)
    await case_store.activity.append(CASE, make_event())
    await case_store.assign_analyst(CASE, "u-riyer", "Rahul Iyer", ACTOR)
    await case_store.activity.append(CASE, make_event())
    assert [e.seq for e in case_store.activity.entries] == [1, 2, 3, 4]
    record = await case_store.get(CASE)
    assert record is not None
    assert record.activity_seq == 4


async def test_activity_since_returns_the_tail_in_order_and_by_audience(
    case_store: InMemoryCaseStore,
    stored_case: CaseRecord,
    make_event: Callable[..., UiEventIn],
) -> None:
    await case_store.activity.append(CASE, make_event(visibility="po"))
    await case_store.activity.append(CASE, make_event(visibility="analyst"))
    await case_store.activity.append(CASE, make_event(visibility="all"))
    log = case_store.activity
    assert [e.seq for e in await log.since(CASE, 0)] == [1, 2, 3]
    assert [e.seq for e in await log.since(CASE, 1)] == [2, 3]
    assert [e.seq for e in await log.since(CASE, 0, audience="po")] == [1, 3]
    assert [e.seq for e in await log.since(CASE, 0, audience="staff")] == [1, 2, 3]
    assert [e.seq for e in await log.since(CASE, 0, limit=2)] == [1, 2]


async def test_open_case_counts_counts_every_assigned_case_that_is_not_closed(
    case_store: InMemoryCaseStore, make_case_record: Callable[..., CaseRecord]
) -> None:
    for number, (analyst, state) in enumerate(
        [
            ("u-riyer", CaseState.REVIEW),
            ("u-riyer", CaseState.DECIDE),
            ("u-riyer", CaseState.CLOSED),
            ("u-mshah", CaseState.CLARIFICATION),
            (None, CaseState.INTAKE),
        ],
        start=1,
    ):
        record = make_case_record(
            f"FCRM-2026-{number:04d}", analyst_id=analyst, state=state
        )
        case_store._cases[record.case_id] = record
    assert await case_store.open_case_counts() == {"u-riyer": 2, "u-mshah": 1}


async def test_exists_and_the_lean_get(
    case_store: InMemoryCaseStore, stored_case: CaseRecord
) -> None:
    assert await case_store.exists(CASE) is True
    assert await case_store.exists("FCRM-2026-9999") is False
    version = SubmissionVersionRecord(
        version=1, created_at=utcnow(), created_by="u-pnair", field_values={}
    )
    case_store.records[CASE] = stored_case.model_copy(
        update={"submission_versions": [version], "current_submission_v": 1}
    )
    full = await case_store.get(CASE)
    lean = await case_store.get(CASE, with_versions=False)
    assert full is not None
    assert lean is not None
    assert len(full.submission_versions) == 1
    assert lean.submission_versions == []
    assert full.current_submission_v == 1


@pytest.mark.parametrize("missing", ["FCRM-2026-9999"])
async def test_the_log_does_not_append_for_an_unknown_case(
    case_store: InMemoryCaseStore, make_event: Callable[..., UiEventIn], missing: str
) -> None:
    assert await case_store.activity.append(missing, make_event()) is None
    assert case_store.activity.entries == []
