"""Filter allow-list (and its drift check against S3's copy) plus filtered search
over the seeded ``memory`` backend."""

from __future__ import annotations

import pytest
from fcrm_context_worker.filters import (
    ALLOWED_METADATA_KEYS,
    UnknownFilterKeyError,
    matches_filters,
    to_vector_filter,
    validate_metadata,
)
from fcrm_context_worker.search import search_clauses
from fcrm_policy.search import filters as policy_filters

SAMPLES: list[tuple[dict[str, object], dict[str, object]]] = [
    ({"source_id": "FFIEC", "corpus_version": "v14"}, {"source_id": "FFIEC"}),
    ({"source_id": "OFAC"}, {"source_id": "FFIEC"}),
    ({"risk_area": "geography"}, {"risk_area": None}),
    ({}, {"jurisdiction": "MX"}),
]


def test_allowed_metadata_keys_match_policy_service_copy() -> None:
    assert ALLOWED_METADATA_KEYS == policy_filters.ALLOWED_METADATA_KEYS


@pytest.mark.parametrize(("metadata", "filters"), SAMPLES)
def test_matches_filters_behaves_like_policy_service_copy(
    metadata: dict[str, object], filters: dict[str, object]
) -> None:
    assert matches_filters(metadata, filters) == policy_filters.matches_filters(
        metadata, filters
    )
    assert validate_metadata(metadata) == policy_filters.validate_metadata(metadata)


def test_to_vector_filter_rejects_unknown_key() -> None:
    with pytest.raises(UnknownFilterKeyError) as exc:
        to_vector_filter({"source_id": "FFIEC", "risk_level": "high"})
    assert exc.value.keys == ["risk_level"]


def test_to_vector_filter_drops_none_and_empty() -> None:
    assert to_vector_filter({"source_id": "OFAC", "jurisdiction": None}) == {
        "source_id": "OFAC"
    }
    assert to_vector_filter({"jurisdiction": None}) is None
    assert to_vector_filter(None) is None


async def test_search_clauses_filters_narrow_results(seeded: tuple) -> None:
    store, llm, config = seeded
    query = "screening of payout agents and corridors"
    unfiltered = await search_clauses(
        query, store=store, llm=llm, config=config, top_k=10
    )
    ffiec = await search_clauses(
        query,
        store=store,
        llm=llm,
        config=config,
        top_k=10,
        filters={"source_id": "FFIEC"},
    )
    ofac = await search_clauses(
        query,
        store=store,
        llm=llm,
        config=config,
        top_k=10,
        filters={"source_id": "OFAC"},
    )
    assert len(unfiltered) == 4
    assert [h.clause_id for h in ffiec] == ["FFIEC-TPPP-04"]
    assert [h.clause_id for h in ofac] == ["OFAC-1.2"]
    assert {h.clause_id for h in ffiec}.isdisjoint({h.clause_id for h in ofac})


async def test_search_clauses_corpus_version_filter(seeded: tuple) -> None:
    store, llm, config = seeded
    v14 = await search_clauses(
        "corridor",
        store=store,
        llm=llm,
        config=config,
        top_k=10,
        filters={"corpus_version": "v14"},
    )
    v15 = await search_clauses(
        "corridor",
        store=store,
        llm=llm,
        config=config,
        top_k=10,
        filters={"corpus_version": "v15"},
    )
    assert len(v14) == 4
    assert v15 == []


async def test_search_clauses_returns_verbatim_seed_quote(seeded: tuple) -> None:
    store, llm, config = seeded
    hits = await search_clauses(
        "jurisdiction screening",
        store=store,
        llm=llm,
        config=config,
        filters={"source_id": "INT-FCRM-4.2"},
    )
    assert len(hits) == 1
    assert hits[0].clause_id == "FCRM42-3.1"
    assert "jurisdiction screening at onboarding" in hits[0].text
    assert hits[0].ref == "Policy 4.2 §3.1 · v2026-03"


async def test_search_clauses_unknown_filter_key_raises(seeded: tuple) -> None:
    store, llm, config = seeded
    with pytest.raises(UnknownFilterKeyError):
        await search_clauses(
            "x", store=store, llm=llm, config=config, filters={"band": "3"}
        )
