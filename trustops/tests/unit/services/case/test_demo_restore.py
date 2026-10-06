"""``fcrm_case.demo_restore``: what ``make demo-reset`` writes back for the demo
case after it deleted every row that carries the case's id (the case's own
record included; the startup seed fills only empty collections).
"""

from __future__ import annotations

from fcrm_case.demo_restore import demo_case_record
from fcrm_case.seedmodel import (
    build_case_seed,
    field_set_from_seed,
    pinned_versions_from_seed,
)
from fcrm_common.seedload import SeedReader
from fcrm_contracts.enums import CaseState

DEMO = "FCRM-2026-0151"


def test_the_restored_record_is_the_seeds_draft_of_the_demo_case() -> None:
    record = demo_case_record(SeedReader().raw)

    assert record.case_id == DEMO
    assert record.state is CaseState.INTAKE
    assert record.analyst_id is None
    assert record.change_types == []
    assert record.documents == []
    assert record.current_submission_v is None


def test_it_is_the_record_the_startup_seed_inserts() -> None:
    raw = SeedReader().raw
    rows = build_case_seed(
        raw,
        field_set_from_seed(raw["field_set"]),
        pinned_versions_from_seed(raw["demo"]["versions"]),
    )
    seeded = next(c for c in rows.cases if c.case_id == DEMO)

    assert demo_case_record(raw) == seeded
