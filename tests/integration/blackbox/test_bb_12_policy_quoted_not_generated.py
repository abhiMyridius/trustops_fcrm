"""BB-12, "policy quoted, not generated" (design doc numbering: "Every
accepted clause's ``quote`` is a substring of ``GET policy/clauses/{id}``
text for the pinned corpus version"; CLAUDE.md "policy text is quoted
verbatim with a ``source_ref``, never generated").

Driven through the real S3 app (``fcrm_policy.main.create_app``): H4's real
vector engine (memory backend, dummy embeddings) fused with the in-memory
keyword index, HTTP only. The clauses under test are the four the demo
case accepted (``demo["clauses"]``), the quotes are checked with the real
rule 8 (``fcrm_guardrails.rules.r8_policy_quoted``, which uses the single
verbatim matcher Guard 4 also uses).

One honest substitution: S3 has no ``GET /policy/clauses/{id}`` route. The
only way to obtain clause text over HTTP is ``POST /search`` on the
``policy_clauses`` index, so the served text is read from the hit whose id
is the clause id, with the case's pinned corpus version as a filter. The
property being proved is the same -- every accepted quote is a verbatim
substring of the text S3 serves for that clause -- but a dedicated
by-id route would be a cleaner thing to test against, and the design table
still lists it.

Also not covered: the "accepted" side. Accepting a clause is a human gate
decision (H1/S1), which has no route, so this takes the demo's recorded
accepted clauses as given.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from fcrm_contracts.enums import Domain
from fcrm_contracts.proposals.provenance import ClauseSourceRef
from fcrm_contracts.proposals.research import ClauseProposal
from fcrm_guardrails.rules.r8_policy_quoted import check as policy_quoted

pytestmark = pytest.mark.integration

PINNED_CORPUS_V = 14  # the demo case pins corpus v14


def _serve(client: TestClient, query: str, corpus_v: int = PINNED_CORPUS_V) -> Any:
    response = client.post(
        "/search",
        json={
            "index": "policy_clauses",
            "query": query,
            "top_k": 10,
            "filters": {"corpus_version": corpus_v},
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["hits"]


def _served_hit(client: TestClient, clause: dict[str, Any]) -> dict[str, Any]:
    """What S3 serves for ``clause`` at the pinned corpus version: the one
    hit whose id is the clause id, asked for by the clause's title.
    """
    clause_id = clause["clause_id"]
    hits = [h for h in _serve(client, clause["title"]) if h["id"] == clause_id]
    assert len(hits) == 1, f"S3 did not serve {clause_id} at v{PINNED_CORPUS_V}"
    hit: dict[str, Any] = hits[0]
    return hit


def _proposal(clause: dict[str, Any], **overrides: Any) -> ClauseProposal:
    """A clause proposal quoting ``clause["quote"]`` from its own clause.
    ``domain`` and ``applicability`` are required by the contract but play
    no part in the quote rule.
    """
    fields: dict[str, Any] = {
        "source_ref": ClauseSourceRef(
            clause_id=clause["clause_id"], corpus_v=PINNED_CORPUS_V
        ),
        "quote": clause["quote"],
        "confidence": 0.9,
        "clause_id": clause["clause_id"],
        "domain": Domain.CHANNEL,
        "applicability": "applies to the payout-agent corridor",
    }
    return ClauseProposal(**{**fields, **overrides})


@pytest.fixture
def corpus(policy_client: TestClient, demo: dict[str, Any]) -> dict[str, str]:
    """``{clause_id: text}`` as S3 serves it for the pinned corpus."""
    return {
        c["clause_id"]: _served_hit(policy_client, c)["text"] for c in demo["clauses"]
    }


def test_s3_serves_each_accepted_clause_with_its_source_reference_at_the_pinned_corpus(
    policy_client: TestClient, demo: dict[str, Any]
) -> None:
    for clause in demo["clauses"]:
        meta = _served_hit(policy_client, clause)["metadata"]
        assert meta["ref"] == clause["ref"]
        assert meta["source_id"] == clause["src"]
        assert meta["title"] == clause["title"]
        assert meta["corpus_version"] == PINNED_CORPUS_V


def test_every_accepted_quote_is_verbatim_in_the_text_s3_serves(
    corpus: dict[str, str], demo: dict[str, Any]
) -> None:
    assert set(corpus) == {c["clause_id"] for c in demo["clauses"]}
    for clause in demo["clauses"]:
        # Literally a substring of what S3 served...
        assert clause["quote"] in corpus[clause["clause_id"]]

    # ... and rule 8 agrees for the whole accepted set, at the pinned version.
    result = policy_quoted(
        [_proposal(c) for c in demo["clauses"]], corpus, corpus_v=PINNED_CORPUS_V
    )
    assert result.ok, result.detail


def test_the_served_text_is_the_same_whatever_the_query_so_it_is_stored_not_generated(
    policy_client: TestClient, demo: dict[str, Any]
) -> None:
    for clause in demo["clauses"]:
        texts = set()
        for query in (
            clause["title"],
            "screening of payout agents and corridors",
            "sanctions lists vendor due diligence",
        ):
            for hit in _serve(policy_client, query):
                if hit["id"] == clause["clause_id"]:
                    texts.add(hit["text"])
        assert len(texts) == 1, f"{clause['clause_id']} text varies with the query"


def test_a_paraphrase_is_not_a_quote_and_rule_8_refuses_it(
    corpus: dict[str, str], demo: dict[str, Any]
) -> None:
    ffiec = next(c for c in demo["clauses"] if c["clause_id"] == "FFIEC-TPPP-04")
    paraphrase = _proposal(
        ffiec,
        quote="Banks ought to look at how risky the processor's customers and "
        "payment volumes are.",
    )
    result = policy_quoted(paraphrase, corpus, corpus_v=PINNED_CORPUS_V)
    assert not result.ok
    assert "FFIEC-TPPP-04" in result.detail


def test_a_real_quote_cited_against_the_wrong_clause_is_refused(
    corpus: dict[str, str], demo: dict[str, Any]
) -> None:
    ffiec = next(c for c in demo["clauses"] if c["clause_id"] == "FFIEC-TPPP-04")
    ofac = next(c for c in demo["clauses"] if c["clause_id"] == "OFAC-1.2")
    misattributed = _proposal(ofac, quote=ffiec["quote"])
    assert not policy_quoted(misattributed, corpus, corpus_v=PINNED_CORPUS_V).ok


def test_a_re_cased_quote_is_refused_as_not_verbatim(
    corpus: dict[str, str], demo: dict[str, Any]
) -> None:
    fcrm = next(c for c in demo["clauses"] if c["clause_id"] == "FCRM42-3.1")
    # "High" is a defined jurisdiction rating in the clause; lower-casing it
    # is already rewriting the policy.
    recased = _proposal(fcrm, quote=fcrm["quote"].replace("rated High", "rated high"))
    assert not policy_quoted(recased, corpus, corpus_v=PINNED_CORPUS_V).ok


def test_a_quote_pinned_to_another_corpus_version_is_refused(
    policy_client: TestClient, corpus: dict[str, str], demo: dict[str, Any]
) -> None:
    clause = demo["clauses"][0]
    wrong_version = _proposal(
        clause,
        source_ref=ClauseSourceRef(clause_id=clause["clause_id"], corpus_v=13),
    )
    assert not policy_quoted(wrong_version, corpus, corpus_v=PINNED_CORPUS_V).ok
    # And S3 serves no text at all for a corpus version the case did not pin.
    assert _serve(policy_client, clause["title"], corpus_v=13) == []
