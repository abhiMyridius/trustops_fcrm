"""S4's seeded trails: nine hash-chained records for each of the 65 closed seed
cases, none for the demo case (whose 67 records the demo produces live)."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fcrm_audit.chain import GENESIS_HASH, verify_chain
from fcrm_audit.lifespan import chain_self_test_case_id
from fcrm_audit.routes import router
from fcrm_audit.seedmodel import (
    CLOSED_CASE_RECORD_TYPES,
    SeedChainError,
    build_closed_case_chain,
    build_closed_case_chains,
)
from fcrm_audit.store import InMemoryAuditStore
from fcrm_common.errors import install_error_handlers
from fcrm_common.health import ReadinessProbe, build_health_router
from fcrm_contracts.enums import Role
from fcrm_seed.loader import cases, closed_cases, demo_case

PRECEDENT = "FCRM-2026-0118"


def _by_case() -> dict[str, list[Any]]:
    grouped: dict[str, list[Any]] = {}
    for record in build_closed_case_chains(cases()):
        grouped.setdefault(record.case_id, []).append(record)
    return grouped


def test_every_closed_case_gets_nine_records_and_nothing_else_does() -> None:
    chains = _by_case()
    closed = {c["id"] for c in closed_cases()}
    assert len(closed) == 65
    assert set(chains) == closed
    assert {len(chain) for chain in chains.values()} == {9}
    assert sum(len(chain) for chain in chains.values()) == 585


def test_the_demo_case_has_no_seeded_trail() -> None:
    assert demo_case()["id"] not in _by_case()


def test_the_nine_records_have_the_documented_types_in_order() -> None:
    for chain in _by_case().values():
        assert tuple(r.type for r in chain) == CLOSED_CASE_RECORD_TYPES
        assert [r.seq for r in chain] == list(range(1, 10))
    assert CLOSED_CASE_RECORD_TYPES[0] == "case.created"
    assert CLOSED_CASE_RECORD_TYPES[-1] == "chain.verified"


def test_every_seeded_chain_verifies_end_to_end() -> None:
    for case_id, chain in _by_case().items():
        result = verify_chain(case_id, chain)
        assert result.ok, case_id
        assert result.record_count == 9
        assert chain[0].prev_hash == GENESIS_HASH


def test_a_tampered_seeded_record_is_caught() -> None:
    chain = _by_case()[PRECEDENT]
    chain[4] = chain[4].model_copy(update={"summary": "Composite 1.0, band 1"})
    result = verify_chain(PRECEDENT, chain)
    assert (result.ok, result.first_break_seq) == (False, 5)


def test_timestamps_never_go_backwards_and_keep_whole_seconds() -> None:
    for chain in _by_case().values():
        times = [r.created_at for r in chain]
        assert times == sorted(times)
        assert all(t.microsecond == 0 and t.tzinfo is not None for t in times)


def test_actors_are_the_cases_owner_and_analyst_or_the_system() -> None:
    for case in closed_cases():
        chain = _by_case()[case["id"]]
        actors = {r.type: r.actor for r in chain}
        assert actors["case.created"] == case["owner"]
        assert actors["case.submitted"] == case["owner"]
        assert actors["recommendation.locked"] == case["analyst"]
        assert actors["case.closed"] == case["analyst"]
        assert actors["triage.assigned"] == "system"
        assert actors["chain.verified"] == "system"


def test_records_carry_the_cases_score_band_and_outcome() -> None:
    expected_outcomes = {
        "approved": "approve",
        "approve_with_conditions": "approve_with_conditions",
        "deferred": "defer",
        "rejected": "reject",
    }
    for case in closed_cases():
        by_type = {r.type: r for r in _by_case()[case["id"]]}
        score = case["score"]
        assert by_type["score.computed"].data["composite"] == score["composite"]
        assert by_type["score.computed"].data["band"] == score["band"]
        outcome = expected_outcomes[case["outcome"]]
        assert by_type["outcome.set"].data["outcome"] == outcome
        assert by_type["case.closed"].data["outcome"] == outcome
        mode = "committee" if score["band"] >= 3 else "delegated"
        assert by_type["outcome.set"].data["mode"] == mode
        assert all(r.data["seeded"] is True for r in by_type.values())


def test_committee_outcomes_are_set_by_the_system_delegated_ones_by_the_analyst() -> (
    None
):
    for case in closed_cases():
        outcome_set = next(r for r in _by_case()[case["id"]] if r.type == "outcome.set")
        if case["score"]["band"] >= 3:
            assert outcome_set.actor == "system"
        else:
            assert outcome_set.actor == case["analyst"]


def test_building_twice_gives_identical_hashes() -> None:
    first = [r.hash for r in build_closed_case_chains(cases())]
    second = [r.hash for r in build_closed_case_chains(cases())]
    assert first == second


def test_the_self_test_case_is_a_closed_precedent_with_a_seeded_chain() -> None:
    case_id = chain_self_test_case_id(demo_case())
    assert case_id == PRECEDENT
    assert case_id in {c["id"] for c in closed_cases()}
    assert case_id in _by_case()


def test_cases_not_closed_are_skipped_and_a_malformed_closed_case_is_refused() -> None:
    open_case = next(c for c in cases() if c["stage"] == "under_assessment")
    assert build_closed_case_chains([open_case]) == []
    closed = dict(closed_cases()[0])
    closed["decided"] = "2000-01-01"
    with pytest.raises(SeedChainError, match="out of order"):
        build_closed_case_chain(closed)


def test_record_types_are_all_in_the_p7_audit_vocabulary() -> None:
    vocabulary = {r["type"] for r in demo_case()["audit"]}
    assert set(CLOSED_CASE_RECORD_TYPES) <= vocabulary | {"outcome.set"}
    counts = Counter(r.type for r in build_closed_case_chains(cases()))
    assert set(counts.values()) == {65}


@pytest.fixture
def seeded_app(wire_test_auth: Callable[[FastAPI], FastAPI]) -> FastAPI:
    application = FastAPI()
    install_error_handlers(application)
    application.state.probe = ReadinessProbe()
    application.include_router(router)
    application.include_router(build_health_router(application.state.probe))
    application.state.store = InMemoryAuditStore(build_closed_case_chains(cases()))
    return wire_test_auth(application)


def test_verify_over_http_passes_for_the_precedent_and_is_empty_for_the_demo(
    seeded_app: FastAPI, act_as: Callable[[Role, str], None]
) -> None:
    act_as(Role.ANALYST, "u-riyer")
    with TestClient(seeded_app) as client:
        precedent = client.get(f"/verify/{PRECEDENT}")
        assert precedent.json() == {
            "case_id": PRECEDENT,
            "record_count": 9,
            "ok": True,
            "first_break_seq": None,
        }
        assert client.get(f"/verify/{demo_case()['id']}").json()["record_count"] == 0


def test_records_over_http_page_through_a_seeded_trail(
    seeded_app: FastAPI, act_as: Callable[[Role, str], None]
) -> None:
    act_as(Role.ANALYST, "u-riyer")
    with TestClient(seeded_app) as client:
        page = client.get(f"/records/{PRECEDENT}", params={"limit": 4})
        body = page.json()
        assert [r["type"] for r in body["items"]] == list(CLOSED_CASE_RECORD_TYPES[:4])
        assert body["total"] == 9
