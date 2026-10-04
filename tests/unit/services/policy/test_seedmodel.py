"""``fcrm_policy.seedmodel``: seed -> Mongo document adapters.

Counts are hand-verified against design A §6.1 / §2.3: corpus v14 ships
four full clause texts, 65 of the 80 seed cases are closed (the precedent
pool), and the control inventory is v9's 24 controls.
"""

from __future__ import annotations

from typing import Any

from fcrm_policy.seedmodel import (
    clause_documents,
    control_documents,
    precedent_documents,
)


def test_clause_documents_covers_the_four_demo_clauses() -> None:
    docs = clause_documents()
    assert len(docs) == 4
    ids = {doc["clause_id"] for doc in docs}
    assert ids == {"FFIEC-TPPP-04", "FCRM42-3.1", "TP71-2.3", "OFAC-1.2"}


def test_clause_document_text_is_the_verbatim_seed_quote() -> None:
    docs = {doc["clause_id"]: doc for doc in clause_documents()}
    clause = docs["TP71-2.3"]
    assert "independent assessment of the agent network" in clause["text"]
    assert clause["ref"] == "Standard 7.1 §2 · v2025-11"
    assert clause["search_text"] == f"{clause['title']} {clause['text']}"
    assert clause["corpus_version"] == 14
    assert clause["_id"] == "v14:TP71-2.3"


def test_precedent_documents_covers_every_closed_case(demo: dict[str, Any]) -> None:
    from fcrm_seed.loader import load_raw

    raw = load_raw()
    docs = precedent_documents(raw)
    assert len(docs) == 65
    assert all(doc["_id"] == doc["case_id"] for doc in docs)
    assert {doc["case_id"] for doc in docs} <= {c["id"] for c in raw["cases"]}


def test_precedent_document_for_the_demo_s_matched_precedent(
    demo: dict[str, Any],
) -> None:
    from fcrm_seed.loader import load_raw

    raw = load_raw()
    docs = {doc["case_id"]: doc for doc in precedent_documents(raw)}
    matched = docs[demo["precedent"]["id"]]
    assert matched["title"] == "Payout partner onboarding (Guatemala corridor)"
    assert matched["outcome"] == "approve_with_conditions"
    assert "channel" in matched["domains"]


def test_control_documents_covers_the_full_inventory() -> None:
    from fcrm_seed.loader import load_raw

    raw = load_raw()
    docs = control_documents(raw)
    assert len(docs) == 24
    assert {doc["_id"] for doc in docs} == {c["id"] for c in raw["controls"]}
    ctl_t01 = next(d for d in docs if d["control_id"] == "CTL-T01")
    assert ctl_t01["domain"] == "channel"
