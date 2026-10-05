"""Rule 8: policy is quoted verbatim from the pinned corpus, independently
of Guard 4. Corpus text: the seed's four demo clauses."""

from __future__ import annotations

from fcrm_contracts.enums import Domain
from fcrm_contracts.proposals.provenance import ClauseSourceRef, DocSourceRef
from fcrm_contracts.proposals.research import ClauseProposal
from fcrm_guardrails.quote_match import normalise_quote
from fcrm_guardrails.rules.r8_policy_quoted import check
from fcrm_seed.loader import demo_case
from hypothesis import given
from hypothesis import strategies as st

CORPUS = {c["clause_id"]: c["quote"] for c in demo_case()["clauses"]}


def _clause(
    cid: str, quote: str, *, ref_id: str | None = None, corpus_v: int = 14
) -> ClauseProposal:
    return ClauseProposal(
        source_ref=ClauseSourceRef(clause_id=ref_id or cid, corpus_v=corpus_v),
        quote=quote,
        confidence=0.9,
        clause_id=cid,
        domain=Domain.GEOGRAPHY,
        applicability="corridor to Mexico",
    )


def test_check_all_four_demo_clauses_pass() -> None:
    result = check(
        [_clause(cid, text) for cid, text in CORPUS.items()], CORPUS, corpus_v=14
    )
    assert result.ok
    assert result.detail == "4 clause(s) quoted verbatim"


def test_check_paraphrase_fails() -> None:
    clause = _clause(
        "FCRM42-3.1",
        "new corridors need jurisdiction checks at onboarding and EDD for "
        "High-rated destinations",
    )
    result = check(clause, CORPUS)
    assert not result.ok
    assert "FCRM42-3.1: none match" in result.detail


def test_check_case_changed_quote_fails_as_fuzzy() -> None:
    result = check(_clause("OFAC-1.2", CORPUS["OFAC-1.2"].upper()), CORPUS)
    assert not result.ok
    assert "fuzzy" in result.detail


def test_check_quote_attributed_to_the_wrong_clause_fails() -> None:
    assert not check(_clause("OFAC-1.2", CORPUS["TP71-2.3"]), CORPUS).ok


def test_check_source_ref_naming_another_clause_fails() -> None:
    result = check(_clause("OFAC-1.2", CORPUS["OFAC-1.2"], ref_id="TP71-2.3"), CORPUS)
    assert "source_ref names TP71-2.3" in result.detail


def test_check_wrong_corpus_version_fails() -> None:
    result = check(
        _clause("OFAC-1.2", CORPUS["OFAC-1.2"], corpus_v=13), CORPUS, corpus_v=14
    )
    assert "case pinned v14" in result.detail


def test_check_clause_missing_from_corpus_fails() -> None:
    assert (
        "not in the corpus"
        in check(_clause("NEW-9.9", "a clause that does not exist"), CORPUS).detail
    )


def test_check_non_clause_source_fails() -> None:
    clause = _clause("OFAC-1.2", CORPUS["OFAC-1.2"]).model_copy(
        update={
            "source_ref": DocSourceRef(doc_id="d", page=1, line_start=1, line_end=1)
        }
    )
    assert "cites a doc source" in check(clause, CORPUS).detail


@given(st.sampled_from(sorted(CORPUS)), st.data())
def test_property_any_contiguous_excerpt_passes(cid: str, data: st.DataObject) -> None:
    words = normalise_quote(CORPUS[cid]).split(" ")
    start = data.draw(st.integers(0, len(words) - 3))
    end = data.draw(st.integers(start + 3, len(words)))
    excerpt = " ".join(words[start:end])
    if len(excerpt) < 12:
        return
    assert check(_clause(cid, excerpt), CORPUS).ok


@given(st.sampled_from(sorted(CORPUS)), st.data())
def test_property_dropping_an_interior_word_fails(
    cid: str, data: st.DataObject
) -> None:
    words = normalise_quote(CORPUS[cid]).split(" ")
    drop = data.draw(st.integers(1, len(words) - 2))
    tampered = " ".join(words[:drop] + words[drop + 1 :])
    assert not check(_clause(cid, tampered), CORPUS).ok
