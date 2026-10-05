"""``fcrm_case.seedmodel.build_case_seed``: the 80 seed cases mapped onto S1's
collections (design A §11.3, ADR-16), checked against the seed itself. The same
rows loaded into a real MongoDB are checked in
``tests/integration/live_mongo/test_case_foundation_live.py``.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

import pytest
from fcrm_case.domain.fieldset import resolve_required_fields
from fcrm_case.seedmodel import (
    COUNTER_SEQ,
    COUNTER_YEAR,
    STAGE_MAP,
    CaseSeedRows,
    build_case_seed,
    field_set_from_seed,
    pinned_versions_from_seed,
)
from fcrm_contracts.enums import CaseState, ItemKind, ItemStatus
from fcrm_seed.loader import load_raw

DEMO = "FCRM-2026-0151"
PRECEDENT = "FCRM-2026-0118"


@pytest.fixture(scope="module")
def rows() -> CaseSeedRows:
    raw = load_raw()
    return build_case_seed(
        raw,
        field_set_from_seed(raw["field_set"]),
        pinned_versions_from_seed(raw["demo"]["versions"]),
    )


def test_all_eighty_cases_are_seeded_at_the_state_their_stage_maps_to(
    rows: CaseSeedRows, all_seed_cases: list[dict[str, Any]]
) -> None:
    assert len(rows.cases) == 80
    by_id = {c.case_id: c for c in rows.cases}
    for case in all_seed_cases:
        assert by_id[case["id"]].state == STAGE_MAP[case["stage"]]
    assert Counter(c.state for c in rows.cases) == {
        CaseState.CLOSED: 65,
        CaseState.REVIEW: 8,
        CaseState.CLARIFICATION: 4,
        CaseState.DECIDE: 2,
        CaseState.INTAKE: 1,
    }


def test_the_demo_case_is_seeded_at_intake_with_nothing_else(
    rows: CaseSeedRows, demo: dict[str, Any]
) -> None:
    case = next(c for c in rows.cases if c.case_id == DEMO)
    assert case.state == CaseState.INTAKE
    assert (case.owner, case.owner_name) == ("u-pnair", "Priya Nair")
    assert case.title == demo["title"]
    assert case.unit == "Payments"
    assert case.change_types == []
    assert case.domains == []
    assert case.analyst_id is None  # R5 assigns it live, to the lowest-load analyst
    assert case.submitted_at is None
    assert case.current_submission_v is None
    assert case.run_id is None
    assert case.activity_seq == 0
    assert [f.field_id for f in case.required_fields] == [f"F0{n}" for n in range(1, 7)]
    assert case.pinned_versions.scoring_v == 3
    assert case.fieldset_v == 6
    for collection in (
        rows.field_values,
        rows.review_items,
        rows.score_snapshots,
        rows.conditions,
    ):
        assert [r for r in collection if r.case_id == DEMO] == []


def test_the_stage_flags_follow_the_seed_stage(
    rows: CaseSeedRows, all_seed_cases: list[dict[str, Any]]
) -> None:
    by_id = {c.case_id: c for c in rows.cases}
    for case in all_seed_cases:
        flags = by_id[case["id"]].stage_flags
        assert flags.round_open is (case["stage"] == "awaiting_business")
        assert flags.locked is (case["stage"] == "committee")
        assert (flags.pack_version, flags.vote_round) == (
            (1, 1) if case["stage"] == "committee" else (None, None)
        )
        assert flags.fast_lane_proposed is bool(case["fast_lane"])
    assert by_id["FCRM-2026-0140"].stage_flags.fast_lane_proposed is True


def test_every_case_carries_its_seed_identity_dates_and_pins(
    rows: CaseSeedRows, all_seed_cases: list[dict[str, Any]]
) -> None:
    by_id = {c.case_id: c for c in rows.cases}
    for case in all_seed_cases:
        record = by_id[case["id"]]
        assert record.title == case["title"]
        assert record.unit == case["unit"]
        assert record.owner == case["owner"]
        assert record.created_at.date().isoformat() == case["created"]
        assert record.pinned_versions.controls_v == 9
        assert record.completion_threshold == 0.8
        if case["id"] == DEMO:
            continue
        assert record.analyst_id == case["analyst"]
        assert [c.value for c in record.change_types] == case["change_types"]
        assert record.domains == case["domains"]
        assert record.geographies == (case.get("geographies") or [])
        assert record.submitted_at is not None
        assert record.submitted_at.date().isoformat() == case["submitted"]
        assert record.current_submission_v == 1
        assert record.owner_name
        assert record.analyst_name


def test_closed_cases_carry_their_decision_date_and_nothing_else_does(
    rows: CaseSeedRows, all_seed_cases: list[dict[str, Any]]
) -> None:
    by_id = {c.case_id: c for c in rows.cases}
    for case in all_seed_cases:
        record = by_id[case["id"]]
        if case["stage"] == "closed":
            assert record.decided_at is not None
            assert record.decided_at.date().isoformat() == case["decided"]
            assert record.closed_at == record.decided_at
        else:
            assert record.decided_at is None
            assert record.closed_at is None


def test_required_fields_resolve_from_the_change_types(rows: CaseSeedRows) -> None:
    field_set = field_set_from_seed(load_raw()["field_set"])
    for record in rows.cases:
        change_types = [c.value for c in record.change_types]
        assert record.required_fields == resolve_required_fields(
            change_types, field_set
        )


def test_the_precedent_carries_its_three_reusable_values(rows: CaseSeedRows) -> None:
    values = [r for r in rows.field_values if r.case_id == PRECEDENT]
    assert {v.field_id: v.value for v in values} == {
        "F31": "$2,999 per transaction · $10,000 per 30 days",
        "F33": "Standard remittance disclosure v3",
        "F34": "Remittance rule set v3 (structuring, velocity, corridor anomalies)",
    }
    assert {v.origin for v in values} == {"owner_declared"}
    assert all(
        v.source and v.source.label and "owner declared" in v.source.label
        for v in values
    )
    assert len(rows.field_values) == 3


def test_seventy_nine_cases_have_confirmed_ungated_items_derived_from_their_inputs(
    rows: CaseSeedRows, all_seed_cases: list[dict[str, Any]]
) -> None:
    others = [c for c in all_seed_cases if c["id"] != DEMO]
    expected = sum(
        2 + len(area["controls"]) for c in others for area in c["inputs"].values()
    )
    assert len(rows.review_items) == expected
    assert {i.status for i in rows.review_items} == {ItemStatus.CONFIRMED}
    assert {i.kind for i in rows.review_items} == {ItemKind.FACTOR, ItemKind.CONTROL}
    assert all(i.gate_id is None and i.proposal_id is None for i in rows.review_items)
    assert all(i.decided is not None for i in rows.review_items)
    assert len({i.item_id for i in rows.review_items}) == len(rows.review_items)
    assert {i.case_id for i in rows.review_items} == {c["id"] for c in others}


def test_a_seeded_control_item_holds_the_inputs_rating_and_evidence(
    rows: CaseSeedRows, all_seed_cases: list[dict[str, Any]]
) -> None:
    case = next(c for c in all_seed_cases if c["id"] == PRECEDENT)
    controls = {
        (domain, ctl["id"]): ctl
        for domain, area in case["inputs"].items()
        for ctl in area["controls"]
    }
    items = {
        (i.subject["domain"], i.subject["control_id"]): i
        for i in rows.review_items
        if i.case_id == PRECEDENT and i.kind == ItemKind.CONTROL
    }
    assert set(items) == set(controls)
    for key, ctl in controls.items():
        assert items[key].proposed["rating"] == ctl["rating"]
        assert items[key].proposed["evidence"] == ctl["evidence"]


def test_the_score_snapshots_are_exactly_what_the_seed_holds(
    rows: CaseSeedRows, all_seed_cases: list[dict[str, Any]]
) -> None:
    assert len(rows.score_snapshots) == 79
    by_case = {s.case_id: s for s in rows.score_snapshots}
    for case in all_seed_cases:
        if case["id"] == DEMO:
            continue
        snapshot = by_case[case["id"]]
        score = case["score"]
        assert snapshot.composite == score["composite"]
        assert snapshot.band == score["band"]
        assert snapshot.route == score["route"]
        assert snapshot.boundary_flag is score["boundary_flag"]
        assert snapshot.boundary_distance == score["boundary_distance"]
        assert set(snapshot.domains) == set(case["domains"])
    # the 80-case band distribution is 19/49/11/1; the demo case is band 2
    assert Counter(s.band for s in rows.score_snapshots) == {1: 19, 2: 48, 3: 11, 4: 1}


def test_open_conditions_are_seeded_as_counted_and_the_po_total_matches_the_seed(
    rows: CaseSeedRows, all_seed_cases: list[dict[str, Any]], demo: dict[str, Any]
) -> None:
    counts = Counter(c.case_id for c in rows.conditions)
    for case in all_seed_cases:
        assert counts.get(case["id"], 0) == case.get("open_conditions", 0)
    assert all(c.status == "open" for c in rows.conditions)
    assert len({c.condition_id for c in rows.conditions}) == len(rows.conditions)
    owned_by_priya = [c for c in rows.conditions if c.owner_id == "u-pnair"]
    assert len(owned_by_priya) == load_raw()["derived"]["po_open_conditions"]
    assert all(c.owner_label == "Priya Nair" for c in owned_by_priya)


def test_the_open_case_load_matches_the_seed_without_the_demo_case(
    rows: CaseSeedRows,
) -> None:
    expected = {
        analyst: len(case_ids)
        for analyst, case_ids in load_raw()["derived"]["analyst_open"].items()
    }
    open_cases = Counter(
        c.analyst_id
        for c in rows.cases
        if c.analyst_id is not None and c.state != CaseState.CLOSED
    )
    assert dict(open_cases) == expected  # riyer 4, mshah 5, dbrooks 5: R5 picks Rahul


def test_the_case_counter_is_left_at_the_seed_value(rows: CaseSeedRows) -> None:
    assert (
        (rows.counter_year, rows.counter_seq)
        == (COUNTER_YEAR, COUNTER_SEQ)
        == (2026, 151)
    )
