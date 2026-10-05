"""The pure pieces behind the corpus routes: precedent averaging, the filter
translation for list and range filters, the precedent row and its content hash,
and the ``index.build`` request whose ids derive from what is being built.
"""

from __future__ import annotations

from typing import Any

import pytest
from fcrm_contracts.api.policy import PrecedentHitOut, PrecedentRecordIn
from fcrm_contracts.events.payloads import IndexBuild
from fcrm_policy.indexing import index_build_envelope, status_id
from fcrm_policy.precedents import content_hash, precedent_row
from fcrm_policy.search.shaping import precedent_averages
from fcrm_policy.store import (
    PRECEDENT_CASES,
    InMemoryKeywordCollection,
    mongo_filter,
)


def _hit(case_id: str, **ratings: float) -> PrecedentHitOut:
    return PrecedentHitOut(
        case_id=case_id,
        title=case_id,
        similarity=0.5,
        closed_date="2026-01-01",
        control_ratings=dict(ratings),
    )


def test_the_precedent_average_is_the_mean_to_one_decimal_half_up() -> None:
    hits = [
        _hit("a", CTL_T01=4.5, CTL_G01=6.0),
        _hit("b", CTL_T01=4.6),
        _hit("c", CTL_G01=7.0),
    ]
    # CTL_T01: (4.5 + 4.6) / 2 = 4.55 -> 4.6 (half up; binary rounding gives 4.5)
    # CTL_G01: (6.0 + 7.0) / 2 = 6.5
    assert precedent_averages(hits) == {"CTL_G01": 6.5, "CTL_T01": 4.6}


def test_a_control_no_hit_rated_is_absent_not_zero() -> None:
    assert precedent_averages([_hit("a"), _hit("b")]) == {}
    assert precedent_averages([]) == {}


def test_the_average_of_a_single_rating_is_that_rating() -> None:
    assert precedent_averages([_hit("a", CTL_T01=4.9)]) == {"CTL_T01": 4.9}


# ── filters ───────────────────────────────────────────────────────────────


def test_list_filters_match_any_member_in_mongo() -> None:
    query = mongo_filter(
        PRECEDENT_CASES,
        {"trigger_types": ["a", "b"], "risk_areas": ["channel"], "outcome": "approved"},
    )
    assert query == {
        "change_types": {"$in": ["a", "b"]},
        "domains": {"$in": ["channel"]},
        "outcome": "approved",
    }


def test_the_two_date_bounds_merge_into_one_range() -> None:
    query = mongo_filter(
        PRECEDENT_CASES,
        {"closed_date_after": "2026-02-01", "closed_date_before": "2026-04-01"},
    )
    assert query == {"closed_date": {"$gte": "2026-02-01", "$lt": "2026-04-01"}}


DOCS: list[dict[str, Any]] = [
    {
        "result_id": "p1",
        "change_types": ["new_product"],
        "domains": ["product"],
        "closed_date": "2026-01-10",
        "outcome": "approved",
        "search_text": "alpha payout",
    },
    {
        "result_id": "p2",
        "change_types": ["vendor_onboarding", "new_geography"],
        "domains": ["channel"],
        "closed_date": "2026-03-10",
        "outcome": "deferred",
        "search_text": "alpha payout partner",
    },
    {
        "result_id": "p3",
        "change_types": ["new_geography"],
        "domains": ["geography"],
        "closed_date": "2026-05-10",
        "outcome": "approved",
        "search_text": "alpha payout corridor",
    },
]


@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        ({}, {"p1", "p2", "p3"}),
        ({"trigger_types": ["new_geography"]}, {"p2", "p3"}),
        ({"trigger_types": ["new_product", "vendor_onboarding"]}, {"p1", "p2"}),
        ({"risk_areas": ["channel", "geography"]}, {"p2", "p3"}),
        ({"outcome": "approved"}, {"p1", "p3"}),
        ({"closed_date_after": "2026-03-10"}, {"p2", "p3"}),
        ({"closed_date_before": "2026-03-10"}, {"p1"}),
        (
            {"closed_date_after": "2026-02-01", "closed_date_before": "2026-04-01"},
            {"p2"},
        ),
        ({"trigger_types": ["new_geography"], "outcome": "approved"}, {"p3"}),
        ({"trigger_types": None, "risk_areas": None}, {"p1", "p2", "p3"}),
    ],
)
async def test_the_in_memory_filters_agree_with_the_mongo_translation(
    filters: dict[str, Any], expected: set[str]
) -> None:
    collection = InMemoryKeywordCollection(DOCS, PRECEDENT_CASES)
    hits = await collection.text_search("alpha payout", filters=filters, limit=10)
    assert {doc_id for doc_id, _ in hits} == expected


# ── the precedent row ─────────────────────────────────────────────────────


def _record(**overrides: Any) -> PrecedentRecordIn:
    fields: dict[str, Any] = {
        "case_id": "FCRM-2026-0151",
        "title": "Consumer remittances",
        "change_types": ["new_geography"],
        "domains": ["geography"],
        "geographies": ["Mexico"],
        "closed_date": "2026-10-02",
        "outcome": "approve_with_conditions",
        "band": 3,
        "control_ratings": {"CTL-T01": 4.5},
        "unit": "Payments",
    }
    fields.update(overrides)
    return PrecedentRecordIn(**fields)


def test_a_precedent_row_has_the_seed_shape_and_search_text() -> None:
    row = precedent_row(_record())
    assert row["_id"] == row["result_id"] == row["case_id"] == "FCRM-2026-0151"
    assert row["outcome"] == "approve_with_conditions"
    assert row["band"] == 3
    assert row["search_text"] == (
        "Consumer remittances Payments new geography geography Mexico"
    )


def test_the_content_hash_ignores_the_id_and_itself_but_not_the_content() -> None:
    row = precedent_row(_record())
    assert content_hash(row) == content_hash({**row, "content_hash": "x"})
    assert content_hash(row) == content_hash({**row, "_id": "other"})
    assert content_hash(row) != content_hash(precedent_row(_record(band=4)))
    assert content_hash(row) != content_hash(
        precedent_row(_record(control_ratings={"CTL-T01": 4.6}))
    )


# ── the index.build request ───────────────────────────────────────────────


def test_an_index_build_request_is_fully_determined_by_what_it_builds() -> None:
    first = index_build_envelope("policy_clauses", 14, discriminator="seed")
    again = index_build_envelope("policy_clauses", 14, discriminator="seed")

    assert first.event_id == again.event_id
    assert first.payload["job_id"] == again.payload["job_id"]
    for other in (
        index_build_envelope("policy_clauses", 15, discriminator="seed"),
        index_build_envelope("controls", 14, discriminator="seed"),
        index_build_envelope("policy_clauses", 14, discriminator="abc"),
    ):
        assert other.event_id != first.event_id
        assert other.payload["job_id"] != first.payload["job_id"]


def test_an_index_build_request_carries_a_valid_payload_for_the_catalogued_event() -> (
    None
):
    envelope = index_build_envelope(
        "precedent_cases",
        66,
        discriminator="h",
        case_id="FCRM-2026-0151",
        mode="incremental",
    )
    assert envelope.type == "index.build"
    assert envelope.case_id == "FCRM-2026-0151"
    payload = IndexBuild.model_validate(envelope.payload)
    assert (payload.index, payload.version, payload.mode) == (
        "precedent_cases",
        66,
        "incremental",
    )


def test_a_status_row_is_keyed_by_index_and_version() -> None:
    assert status_id("policy_clauses", 14) == "policy_clauses:14"
