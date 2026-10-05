"""H4's clause seeding over the synthetic corpus (ADR-29): ``clauses.json`` when
it exists, the four seed clauses when it does not."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fcrm_context_worker.config import context_config_from_env
from fcrm_context_worker.search import search_clauses
from fcrm_context_worker.seedload import (
    clause_records,
    seed_clauses,
    seeded_store,
)
from fcrm_seed.loader import load_raw

VERBATIM_IDS = {"FFIEC-TPPP-04", "FCRM42-3.1", "TP71-2.3", "OFAC-1.2"}


def test_seed_clauses_are_the_197_of_clauses_json_when_it_exists(
    synthetic_corpus_clauses: list[dict[str, Any]],
) -> None:
    clauses = seed_clauses()
    assert len(clauses) == 197
    assert len({c["clause_id"] for c in clauses}) == 197
    sample = next(c for c in clauses if c["clause_id"] == "PR23-2.1")
    assert {
        "source_id",
        "title",
        "text",
        "ref",
        "page",
        "line_start",
        "line_end",
        "risk_areas",
        "trigger_types",
        "jurisdictions",
        "quotable",
        "provenance_kind",
    } <= set(sample)
    assert sample["provenance_kind"] == "synthetic_internal_standard"


def test_the_four_seed_texts_are_verbatim_in_the_full_corpus(
    synthetic_corpus_clauses: list[dict[str, Any]],
) -> None:
    by_id = {c["clause_id"]: c for c in seed_clauses()}
    for seeded in load_raw()["demo"]["clauses"]:
        assert by_id[seeded["clause_id"]]["text"] == seeded["quote"]


@pytest.mark.usefixtures("four_clause_corpus")
def test_seed_clauses_fall_back_to_the_four_without_clauses_json() -> None:
    clauses = seed_clauses()
    assert {c["clause_id"] for c in clauses} == VERBATIM_IDS
    assert all(c["provenance_kind"] == "seed_verbatim" for c in clauses)
    assert all(c["quotable"] is True for c in clauses)
    assert not any("risk_areas" in c for c in clauses)


def test_every_clause_becomes_valid_index_contract_metadata(
    synthetic_corpus_clauses: list[dict[str, Any]],
) -> None:
    records = clause_records()
    assert len(records) == 197
    assert len({r["chunk_key"] for r in records}) == 197
    sample = next(r for r in records if r["clause_id"] == "OCC43-2.2")
    assert sample["source_id"] == "OCC-2017-43"
    assert sample["corpus_version"] == "v14"
    assert isinstance(sample["page"], int)
    assert sample["section"].startswith("OCC-2017-43 synthetic stand-in")
    assert sample["risk_area"]
    assert sample["trigger_type"]
    assert sample["jurisdiction"]


async def test_the_memory_store_indexes_the_whole_corpus_and_filters_by_source(
    tmp_path: Path, synthetic_corpus_clauses: list[dict[str, Any]]
) -> None:
    config = context_config_from_env(
        {"VECTOR_BACKEND": "memory", "EMBEDDING_DIMENSION": "64"},
        projects_root=tmp_path,
    )
    store, llm = await seeded_store(config)
    hits = await search_clauses(
        "documented control for item FFIEC-TPPP-07",
        store=store,
        llm=llm,
        config=config,
        filters={"source_id": "FFIEC"},
        top_k=5,
    )
    assert len(hits) == 5
    assert {h.source_id for h in hits} == {"FFIEC"}
