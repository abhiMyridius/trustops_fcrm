"""The deterministic synthesis in fcrm_tools.seed_data and the retriever."""

from __future__ import annotations

import pytest
from fcrm_seed.loader import load_raw
from fcrm_tools.markdown import parse_pages, render_pages
from fcrm_tools.retrieval import IndexedDoc, KeywordIndex, tokenize
from fcrm_tools.seed_data import (
    clause_records,
    demo_ledger_entries,
    jurisdiction_list_out,
    list_line,
    seed_pins,
    submission_versions,
    synthetic_documents,
    tag_domains,
    version_number,
)

SEED = load_raw()


def test_seed_pins_match_demo_versions() -> None:
    pins = seed_pins(SEED)
    assert (pins.scoring_v, pins.corpus_v, pins.controls_v, pins.fieldset_v) == (
        3,
        14,
        9,
        6,
    )
    assert (pins.routing_v, pins.jurisdictions_v, pins.prompts_v) == (
        4,
        "v2026-06",
        "2026-09-28",
    )


def test_version_number_strips_v() -> None:
    assert version_number("v14") == 14


def test_clause_records_quotable_text_is_seed_quote_verbatim() -> None:
    records = clause_records(SEED)
    quotable = {r.clause_id: r for r in records if r.quotable}
    assert sorted(quotable) == ["FCRM42-3.1", "FFIEC-TPPP-04", "OFAC-1.2", "TP71-2.3"]
    for clause in SEED["demo"]["clauses"]:
        assert quotable[clause["clause_id"]].text == clause["quote"]
    sections = sum(len(s["sections"]) for s in SEED["corpus"]["sources"])
    assert len(records) == 4 + sections


@pytest.mark.parametrize(
    ("clause_id", "domain"),
    [
        ("FFIEC-TPPP-04", "channel"),
        ("FCRM42-3.1", "geography"),
        ("TP71-2.3", "channel"),
        ("OFAC-1.2", "geography"),
    ],
)
def test_clause_records_tag_demo_clause_domains(clause_id: str, domain: str) -> None:
    record = next(r for r in clause_records(SEED) if r.clause_id == clause_id)
    assert domain in record.risk_areas


def test_tag_domains_keyword_tagger() -> None:
    assert tag_domains("Limits and velocity") == ("product",)
    assert tag_domains("nothing here") == ()


def test_synthetic_documents_page_text_lines() -> None:
    docs = synthetic_documents(SEED)
    assert sorted(docs) == sorted(d["name"] for d in SEED["demo"]["documents"])
    contract = docs["payout_partner_contract.pdf"]
    assert contract.page_count == 9
    assert "Contract AML clauses reference" in contract.page_text(7)
    letter = docs["vendor_DD_scope_letter_2026.pdf"]
    assert letter.pages[1][1] == "corridor DD review scheduled Nov 2026 (scope letter)"


def test_submission_versions_counts_match_audit_narration() -> None:
    versions = submission_versions(SEED)
    v1, v2 = versions[1], versions[2]
    assert sum(v["value"] is not None for v in v1.values()) == 15
    changed = [fid for fid in v1 if v1[fid]["value"] != v2[fid]["value"]]
    assert changed == ["F21", "F11"] or sorted(changed) == ["F11", "F21"]
    assert v2["F22"]["value"] is None


def test_demo_ledger_entries_cover_every_agent_proposal_record() -> None:
    entries = demo_ledger_entries(SEED)
    assert {e.agent for e in entries} == {
        "intake",
        "screening",
        "research",
        "assessment",
        "critic",
        "narrative",
    }
    critic = next(e for e in entries if e.kind == "critic.findings")
    assert critic.seq == 34
    assert critic.payload["findings"][0]["records"] == ["#30"]
    assessment = next(e for e in entries if e.kind == "proposal.assessment")
    assert "score" not in assessment.payload
    assert len(assessment.payload["controls"]) == 13


def test_jurisdiction_list_line_for_mexico() -> None:
    listing = jurisdiction_list_out(SEED, "current")
    assert list_line(listing, "Mexico") == "v2026-06 · Mexico: not listed"
    assert list_line(listing, "Iran") == "v2026-06 · Iran: call for action"


def test_render_and_parse_pages_round_trip() -> None:
    markdown = render_pages("d", 3, {2: ["a", "b"], 3: ["c"]}, banner="x")
    assert parse_pages(markdown) == {2: ["a", "b"], 3: ["c"]}


def test_tokenize_folds_plurals_and_drops_stopwords() -> None:
    assert tokenize("The Remittances to the Policies") == ["remittance", "policy"]


def test_keyword_index_is_deterministic_and_query_sensitive() -> None:
    index = KeywordIndex(
        [
            IndexedDoc("b", "payout partner corridor"),
            IndexedDoc("a", "payout partner corridor"),
            IndexedDoc("c", "crypto custody pilot"),
        ]
    )
    assert [h.doc.doc_id for h in index.search("payout corridor", top_k=3)] == [
        "a",
        "b",
    ]
    assert [h.doc.doc_id for h in index.search("crypto", top_k=3)] == ["c"]
    assert index.search("unrelated words", top_k=3) == []


def test_keyword_index_rejects_duplicate_ids() -> None:
    with pytest.raises(ValueError, match="unique"):
        KeywordIndex([IndexedDoc("a", "x"), IndexedDoc("a", "y")])
