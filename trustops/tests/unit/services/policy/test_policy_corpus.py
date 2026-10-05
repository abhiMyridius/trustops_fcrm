"""S3's clause documents over the synthetic corpus (ADR-29): ``clauses.json``
when it exists, the four seed clauses when it does not, and a corpus that has no
clause on cash-pickup agent networks (the demo's policy gap) either way.
"""

from __future__ import annotations

from typing import Any

import pytest
from fcrm_policy.seedmodel import clause_documents
from fcrm_policy.store import (
    POLICY_CLAUSES,
    InMemoryKeywordCollection,
    clause_metadata,
)
from fcrm_seed.loader import load_raw

VERBATIM_IDS = {"FFIEC-TPPP-04", "FCRM42-3.1", "TP71-2.3", "OFAC-1.2"}


def test_documents_are_the_197_synthetic_clauses_when_clauses_json_exists(
    synthetic_corpus_clauses: list[dict[str, Any]],
) -> None:
    docs = clause_documents()
    assert len(docs) == len(synthetic_corpus_clauses) == 197
    assert len({d["clause_id"] for d in docs}) == 197
    assert {d["_id"] for d in docs} == {f"v14:{d['clause_id']}" for d in docs}
    assert all(d["corpus_version"] == 14 for d in docs)
    assert all(d["result_id"] == d["clause_id"] for d in docs)


def test_the_four_seed_texts_survive_unchanged_in_the_full_corpus(
    synthetic_corpus_clauses: list[dict[str, Any]],
) -> None:
    docs = {d["clause_id"]: d for d in clause_documents()}
    for seeded in load_raw()["demo"]["clauses"]:
        doc = docs[seeded["clause_id"]]
        assert doc["text"] == seeded["quote"]
        assert doc["ref"] == seeded["ref"]
        assert doc["source_id"] == seeded["src"]
        assert doc["provenance_kind"] == "seed_verbatim"


def test_every_document_says_what_kind_of_text_it_is_and_where_it_sits(
    synthetic_corpus_clauses: list[dict[str, Any]],
) -> None:
    kinds = {
        "seed_verbatim",
        "synthetic_internal_standard",
        "synthetic_public_stand_in",
    }
    for doc in clause_documents():
        assert doc["provenance_kind"] in kinds
        assert doc["quotable"] is True
        assert doc["page"] >= 1
        assert doc["line_end"] == doc["line_start"] + 2
        assert doc["risk_areas"]
        assert doc["trigger_types"]
        assert doc["jurisdictions"]
        assert doc["search_text"] == f"{doc['title']} {doc['text']}"


@pytest.mark.usefixtures("four_clause_corpus")
def test_documents_fall_back_to_the_four_seed_clauses_without_clauses_json() -> None:
    docs = clause_documents()
    assert {d["clause_id"] for d in docs} == VERBATIM_IDS
    assert all(d["provenance_kind"] == "seed_verbatim" for d in docs)
    assert all(d["quotable"] is True for d in docs)
    assert all(d["risk_areas"] == [] for d in docs)


def test_hit_metadata_carries_quotable_source_ref_and_provenance(
    synthetic_corpus_clauses: list[dict[str, Any]],
) -> None:
    doc = next(d for d in clause_documents() if d["clause_id"] == "TP71-2.4")
    meta = clause_metadata(doc)
    assert meta["quotable"] is True
    assert meta["source_id"] == "INT-TP-7.1"
    assert meta["ref"] == "Standard 7.1 §2.4 · v2025-11"
    assert meta["provenance_kind"] == "synthetic_internal_standard"
    assert meta["risk_areas"]


def test_metadata_defaults_to_quotable_for_a_document_without_the_flag() -> None:
    assert clause_metadata({"source_id": "OFAC"})["quotable"] is True
    assert clause_metadata({"quotable": False})["quotable"] is False


async def test_keyword_search_over_the_full_corpus_finds_a_generated_clause(
    synthetic_corpus_clauses: list[dict[str, Any]],
) -> None:
    collection = InMemoryKeywordCollection(clause_documents(), POLICY_CLAUSES)
    ranked = await collection.text_search(
        "documented control for item TP71-2.4", filters={}, limit=5
    )
    assert ranked
    assert ranked[0][0].startswith("TP71-")


async def test_filters_narrow_the_full_corpus_by_tag(
    synthetic_corpus_clauses: list[dict[str, Any]],
) -> None:
    collection = InMemoryKeywordCollection(clause_documents(), POLICY_CLAUSES)
    ranked = await collection.text_search(
        "documented control", filters={"risk_area": "geography"}, limit=50
    )
    assert ranked
    docs = await collection.get_by_ids([clause_id for clause_id, _ in ranked])
    assert all("geography" in d["risk_areas"] for d in docs.values())


def test_no_clause_text_mentions_a_cash_pickup_channel() -> None:
    """The demo's scene 12 searches the corpus for cash-pickup agent networks,
    finds none and records a gap. The corpus must keep it that way, whether it
    is the four seed clauses or the 197 of ``clauses.json``."""
    for doc in clause_documents():
        text = f"{doc['title']} {doc['text']}".lower()
        assert "cash pickup" not in text
        assert "cash-pickup" not in text
        assert "cash out" not in text
