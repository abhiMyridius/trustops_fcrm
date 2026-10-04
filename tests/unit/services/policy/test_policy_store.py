"""``fcrm_policy.store``: the keyword-ranking side (Mongo-shaped filter
translation, the in-memory TF-cosine stand-in, and the per-index metadata
shapes). No live Mongo here -- see ``test_mongo_live.py`` for that tier.
"""

from __future__ import annotations

from typing import Any

import pytest
from fcrm_policy.store import (
    CONTROLS,
    POLICY_CLAUSES,
    PRECEDENT_CASES,
    InMemoryKeywordCollection,
    build_in_memory_collections,
    clause_metadata,
    control_metadata,
    mongo_filter,
    precedent_metadata,
    text_for,
)
from fcrm_seed.loader import load_raw


def test_mongo_filter_maps_trigger_type_to_change_types_for_precedents() -> None:
    query = mongo_filter(PRECEDENT_CASES, {"trigger_type": "vendor_onboarding"})
    assert query == {"change_types": "vendor_onboarding"}


def test_mongo_filter_maps_risk_area_to_domain_for_controls() -> None:
    query = mongo_filter(CONTROLS, {"risk_area": "channel"})
    assert query == {"domain": "channel"}


def test_mongo_filter_builds_a_range_for_closed_date_before() -> None:
    query = mongo_filter(PRECEDENT_CASES, {"closed_date_before": "2026-07-01"})
    assert query == {"closed_date": {"$lt": "2026-07-01"}}


def test_mongo_filter_ignores_none_values() -> None:
    filters = {"corpus_version": None, "risk_area": None}
    assert mongo_filter(POLICY_CLAUSES, filters) == {}


def test_mongo_filter_drops_a_filter_not_applicable_to_the_index() -> None:
    # closed_date_before means nothing for policy_clauses; it is silently
    # dropped rather than raising, since clauses simply have no such field.
    assert mongo_filter(POLICY_CLAUSES, {"closed_date_before": "2026-01-01"}) == {}


@pytest.fixture(scope="module")
def raw() -> dict[str, Any]:
    return load_raw()


@pytest.fixture(scope="module")
def in_memory(raw: dict[str, Any]) -> dict[str, InMemoryKeywordCollection]:
    return build_in_memory_collections(raw)


async def test_in_memory_clause_search_ranks_by_term_overlap(
    in_memory: dict[str, InMemoryKeywordCollection],
) -> None:
    hits = await in_memory[POLICY_CLAUSES].text_search(
        "screening of payout agents and corridors", filters={}, limit=10
    )
    assert {doc_id for doc_id, _ in hits} == {
        "FFIEC-TPPP-04",
        "FCRM42-3.1",
        "TP71-2.3",
        "OFAC-1.2",
    }
    # Every returned score is a positive cosine similarity.
    assert all(score > 0.0 for _, score in hits)


def _demo_precedent_query(raw: dict[str, Any], demo: dict[str, Any]) -> str:
    """The query a caller (e.g. the research agent) would naturally build
    from the demo case's own fields -- same construction as
    ``fcrm_policy.seedmodel.precedent_documents``'s ``search_text``, so
    this test queries the index the way it was actually built to be
    queried, not with an arbitrary hand-typed sentence.
    """
    case = next(c for c in raw["cases"] if c["id"] == demo["id"])
    words = [
        case["title"],
        case.get("unit", ""),
        " ".join(t.replace("_", " ") for t in case["change_types"]),
        " ".join(case.get("domains", [])),
        " ".join(case.get("geographies", [])),
    ]
    return " ".join(w for w in words if w)


async def test_in_memory_precedent_search_ranks_the_demo_s_precedent_first(
    in_memory: dict[str, InMemoryKeywordCollection],
    demo: dict[str, Any],
    raw: dict[str, Any],
) -> None:
    hits = await in_memory[PRECEDENT_CASES].text_search(
        _demo_precedent_query(raw, demo), filters={}, limit=5
    )
    assert hits[0][0] == demo["precedent"]["id"]


async def test_in_memory_search_applies_filters(
    in_memory: dict[str, InMemoryKeywordCollection],
) -> None:
    query = "screening of payout agents and corridors"
    hits = await in_memory[POLICY_CLAUSES].text_search(
        query, filters={"corpus_version": 14}, limit=10
    )
    assert len(hits) == 4
    none_hits = await in_memory[POLICY_CLAUSES].text_search(
        query, filters={"corpus_version": 15}, limit=10
    )
    assert none_hits == []


async def test_in_memory_get_by_ids_returns_only_known_ids(
    in_memory: dict[str, InMemoryKeywordCollection],
) -> None:
    docs = await in_memory[CONTROLS].get_by_ids(["CTL-T01", "no-such-control"])
    assert list(docs) == ["CTL-T01"]


def test_text_for_reads_the_clause_s_verbatim_text() -> None:
    doc = {"text": "…screen all parties…", "title": "OFAC"}
    assert text_for(POLICY_CLAUSES, doc) == "…screen all parties…"


def test_clause_metadata_shape() -> None:
    doc = {
        "source_id": "OFAC",
        "corpus_version": "v14",
        "ref": "OFAC std §1 · v2026-01",
        "title": "OFAC Sanctions Screening Standard",
    }
    meta = clause_metadata(doc)
    assert meta["source_id"] == "OFAC"
    assert meta["risk_areas"] == []


def test_precedent_metadata_shape() -> None:
    doc = {"title": "x", "outcome": "approve", "control_ratings": {"CTL-T01": 4.5}}
    meta = precedent_metadata(doc)
    assert meta["control_ratings"] == {"CTL-T01": 4.5}


def test_control_metadata_shape() -> None:
    doc = {"domain": "channel", "name": "Vendor due-diligence assessment"}
    meta = control_metadata(doc)
    assert meta["domain"] == "channel"
