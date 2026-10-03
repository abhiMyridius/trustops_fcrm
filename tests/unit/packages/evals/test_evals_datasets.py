"""The bundled datasets: deterministic, seed-derived, self-consistent with
the real routing predicate and the watchlist set intersection."""

from __future__ import annotations

import math
from pathlib import Path

import pytest
from fcrm_caseflow.routing import is_fast_lane_candidate
from fcrm_contracts.enums import AgentName
from fcrm_evals.datasets import DATA_DIR, dataset_name, load, load_rows
from fcrm_evals.datasets.build import build_all, check, main, write
from fcrm_evals.datasets.generators.assessment import is_stale, months_between
from fcrm_evals.datasets.generators.intake import parse_source, required_fields
from fcrm_evals.datasets.generators.screening import parse_usd_millions
from fcrm_evals.watchlist import reflag_candidates

EXPECTED_SIZES = {
    AgentName.SCREENING: 100,
    AgentName.INTAKE: 80,
    AgentName.RESEARCH: 42,
    AgentName.ASSESSMENT: 80,
    AgentName.CRITIC: 61,
    AgentName.NARRATIVE: 66,
    AgentName.EXAMINER: 5,
    AgentName.WATCHLIST: 29,
    AgentName.POLICY_GAP: 1,
}


def test_check_committed_datasets_match_a_fresh_build() -> None:
    assert check(build_all(), DATA_DIR) == []


def test_build_all_is_deterministic() -> None:
    assert build_all() == build_all()


def test_load_sizes_are_the_documented_sizes() -> None:
    assert {a: len(load(a)) for a in AgentName} == EXPECTED_SIZES


def test_load_every_row_belongs_to_its_agent_and_has_unique_ids() -> None:
    for agent in AgentName:
        rows = load(agent)
        assert {r.agent for r in rows} == {agent}
        assert len({r.row_id for r in rows}) == len(rows)


def test_check_detects_a_stale_and_a_missing_file(tmp_path: Path) -> None:
    texts = build_all()
    write(texts, tmp_path)
    assert check(texts, tmp_path) == []
    (tmp_path / "critic.jsonl").write_text("{}\n", encoding="utf-8")
    (tmp_path / "examiner.jsonl").unlink()
    assert check(texts, tmp_path) == ["critic.jsonl", "examiner.jsonl"]


def test_main_check_against_committed_files_returns_zero() -> None:
    assert main(["--check"]) == 0


def test_main_check_against_empty_dir_returns_one(tmp_path: Path) -> None:
    assert main(["--check", "--out", str(tmp_path)]) == 1


def test_main_writes_loadable_rows(tmp_path: Path) -> None:
    assert main(["--out", str(tmp_path)]) == 0
    assert len(load_rows(tmp_path / "screening.jsonl")) == 100


def test_screening_labels_equal_the_real_r4_predicate() -> None:
    for row in load(AgentName.SCREENING):
        types = row.input["change_types"]
        volume = row.input["annual_volume_usd"]
        expected = is_fast_lane_candidate(
            types,
            math.inf if volume is None else volume,
            new_geography="new_geography" in types,
            new_segment="new_segment" in types,
        )
        assert row.label("fast_lane") is expected


def test_screening_label_conflicts_are_vendor_onboarding_only_cases() -> None:
    conflicts = [r for r in load(AgentName.SCREENING) if r.metadata.label_conflict]
    assert [r.row_id for r in conflicts] == [
        "FCRM-2026-0088",
        "FCRM-2026-0091",
        "FCRM-2026-0110",
        "FCRM-2026-0123",
        "FCRM-2026-0129",
        "FCRM-2026-0133",
        "FCRM-2026-0139",
    ]
    assert all(r.input["change_types"] == ["vendor_onboarding"] for r in conflicts)


def test_screening_volume_variants_straddle_the_five_million_line() -> None:
    rows = {r.row_id: r for r in load(AgentName.SCREENING)}
    assert rows["FCRM-2026-0089~vol-4.2M"].label("fast_lane") is True
    assert rows["FCRM-2026-0089~vol-5.0M"].label("fast_lane") is False


def test_parse_usd_millions_reads_the_demo_volume() -> None:
    assert parse_usd_millions("$48M year 1") == 48_000_000
    assert parse_usd_millions("about forty") is None


def test_research_demo_row_expects_the_four_accepted_clauses() -> None:
    demo = load(AgentName.RESEARCH)[0]
    assert demo.row_id == "FCRM-2026-0151"
    assert demo.label("relevant_clause_ids") == [
        "FFIEC-TPPP-04",
        "FCRM42-3.1",
        "TP71-2.3",
        "OFAC-1.2",
    ]
    assert demo.label("top_precedent") == "FCRM-2026-0118"


def test_intake_demo_row_has_the_nine_extracted_fields() -> None:
    demo = load(AgentName.INTAKE)[0]
    assert len(demo.label("fields")) == 9
    assert len(demo.label("fields")) + len(demo.label("absent_fields")) == 18


def test_required_fields_for_the_demo_types_is_eighteen() -> None:
    types = ["vendor_onboarding", "new_geography", "new_product"]
    assert len(required_fields(types)) == 18


def test_parse_source_places_lineless_sources_on_line_one() -> None:
    assert parse_source("case p.1 l.2") == ("business_case_remittance_MX", 1, 2)
    assert parse_source("contract p.7 §9") == ("payout_partner_contract", 7, 1)
    with pytest.raises(ValueError, match="not a document"):
        parse_source("reused · FCRM-2026-0118")


def test_months_between_is_day_aware() -> None:
    from datetime import date

    assert months_between(date(2024, 1, 15), date(2026, 1, 15)) == 24
    assert months_between(date(2024, 1, 16), date(2026, 1, 15)) == 23


def test_is_stale_flags_the_demo_partner_audit() -> None:
    assert is_stale("2023-11-15", "2026-10-01") is True
    assert is_stale("2026-05-06", "2026-10-01") is False


def test_reflag_candidates_ignores_closed_cases_and_unlisted_geographies() -> None:
    cases = [
        {"id": "A", "stage": "under_assessment", "geographies": ["Algeria"]},
        {"id": "B", "stage": "closed", "geographies": ["Algeria"]},
        {"id": "C", "stage": "draft", "geographies": ["Peru"]},
    ]
    assert reflag_candidates(cases, ["Algeria"]) == [("A", "Algeria")]
    assert reflag_candidates(cases, []) == []


def test_watchlist_fixture_row_reflags_the_two_open_seed_cases() -> None:
    rows = {r.row_id: r for r in load(AgentName.WATCHLIST)}
    assert rows["list:v2026-10-fixture"].label("reflags") == [
        ["FCRM-2026-0144", "Algeria"],
        ["FCRM-2026-0147", "Namibia"],
    ]
    assert rows["list:v2026-06"].label("reflags") == []


def test_dataset_name_carries_the_real_row_count() -> None:
    assert dataset_name(AgentName.SCREENING, 100) == "screening/fast-lane-100"
