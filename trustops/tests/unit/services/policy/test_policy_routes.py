"""HTTP-layer tests for S3, with no live Mongo: the route layer takes
whatever ``SearchService`` was wired in, so these wire one built from the
in-memory keyword collections (``fcrm_policy.store``'s stand-in for Mongo
``$text``, the same two-tier shape S2's ``InMemoryConfigStore`` uses) and
H4's *real* vector engine -- the ``memory`` backend plus deterministic
``dummy`` embeddings, exactly as ``fcrm_context_worker``'s own test suite
exercises it, so this is real retrieval end to end, not a stub.

``test_golden_search_reproduces_the_demo_s_four_clauses_and_0118`` is the
task's named acceptance check: ``POST /search`` over ``policy_clauses``
surfaces the demo's four cited clauses, and over ``precedent_cases``
ranks FCRM-2026-0118 first for the demo case's own description -- the
same precedent the seed itself pins at similarity 0.81
(``demo["precedent"]``). That 0.81 is an authored narrative figure (see
the module docstring note below), not a value any generic retrieval
formula reproduces bit for bit; the golden check is therefore on *rank*
(0118 is the top precedent hit), matching what Guard 4 / the critic
actually depend on, with the pinned seed figure asserted alongside it for
visibility, not equality.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fcrm_common.errors import install_error_handlers
from fcrm_common.health import ReadinessProbe, build_health_router
from fcrm_context_worker.config import context_config_from_env
from fcrm_context_worker.seedload import seeded_store
from fcrm_contracts.enums import Role
from fcrm_policy.routes import router
from fcrm_policy.search.service import SearchService
from fcrm_policy.search.vector import VectorClauseSource
from fcrm_policy.store import (
    CONTROLS,
    POLICY_CLAUSES,
    PRECEDENT_CASES,
    build_in_memory_collections,
)
from fcrm_seed.loader import load_raw

DEMO_CLAUSE_IDS = {"FFIEC-TPPP-04", "FCRM42-3.1", "TP71-2.3", "OFAC-1.2"}

# These test search mechanics on the four-clause corpus the seed carries; with
# clauses.json committed (ADR-29) the loaders would see 197, so they are pinned.
pytestmark = pytest.mark.usefixtures("four_clause_corpus")


@pytest.fixture
async def search_service(tmp_path: Path) -> SearchService:
    """A real H4 vector engine (memory backend, dummy embeddings) seeded
    with the four demo clauses, fused with the in-memory keyword stand-in
    over the full 80-case seed.
    """
    config = context_config_from_env(
        {"VECTOR_BACKEND": "memory", "EMBEDDING_DIMENSION": "64"},
        projects_root=tmp_path,
    )
    store, llm = await seeded_store(config)
    vector = VectorClauseSource(store, llm, config)
    collections = build_in_memory_collections(load_raw())
    return SearchService(collections=collections, vector=vector)


@pytest.fixture
def app(
    search_service: SearchService, wire_test_auth: Callable[[FastAPI], FastAPI]
) -> FastAPI:
    application = FastAPI()
    install_error_handlers(application)
    application.state.probe = ReadinessProbe()
    application.include_router(router)
    application.include_router(build_health_router(application.state.probe))
    application.state.search = search_service
    return wire_test_auth(application)


@pytest.fixture
def client(app: FastAPI, act_as: Callable[[Role, str], None]) -> TestClient:
    """Searches as the demo analyst; the role matrix is in
    ``test_policy_auth.py``.
    """
    act_as(Role.ANALYST, "u-riyer")
    return TestClient(app)


def _search(client: TestClient, **body: Any) -> dict[str, Any]:
    response = client.post("/search", json=body)
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


def _demo_precedent_query(demo: dict[str, Any]) -> str:
    """The query a caller (e.g. the research agent) would naturally build
    from the demo case's own fields -- the same construction
    ``fcrm_policy.seedmodel.precedent_documents`` uses for its
    ``search_text``, so this queries the index the way it was actually
    built to be queried.
    """
    words = [
        demo["title"],
        demo.get("unit", ""),
        " ".join(t.replace("_", " ") for t in demo["change_types"]),
        " ".join(demo.get("domains", [])),
        " ".join(demo.get("geographies", [])),
    ]
    return " ".join(w for w in words if w)


def test_golden_search_reproduces_the_demo_s_four_clauses_and_0118(
    client: TestClient, demo: dict[str, Any]
) -> None:
    clauses = _search(
        client,
        index=POLICY_CLAUSES,
        query="screening of payout agents and corridors",
        top_k=10,
    )
    clause_ids = {hit["id"] for hit in clauses["hits"]}
    assert clause_ids == DEMO_CLAUSE_IDS

    precedents = _search(
        client, index=PRECEDENT_CASES, query=_demo_precedent_query(demo), top_k=5
    )
    assert precedents["hits"][0]["id"] == demo["precedent"]["id"] == "FCRM-2026-0118"
    # The seed's own pinned narrative figure, surfaced for visibility only
    # (see module docstring: not expected to equal our fused/cosine score).
    assert demo["precedent"]["similarity"] == 0.81


def test_hit_scores_are_in_unit_range_ordered_and_carry_per_source_scores(
    client: TestClient,
) -> None:
    body = _search(
        client,
        index=POLICY_CLAUSES,
        query="screening of payout agents and corridors",
        top_k=10,
    )
    scores = [h["score"] for h in body["hits"]]
    assert scores == sorted(scores, reverse=True)
    assert all(0.0 < s <= 1.0 for s in scores)
    for hit in body["hits"]:
        assert hit["metadata"]["vector_score"] is not None
    precedents = _search(
        client, index=PRECEDENT_CASES, query="payout partner onboarding", top_k=3
    )
    for hit in precedents["hits"]:
        assert hit["metadata"]["vector_score"] is None
        assert 0.0 < hit["metadata"]["text_score"] <= 1.0


def test_policy_clause_search_returns_verbatim_text_and_metadata(
    client: TestClient,
) -> None:
    body = _search(
        client, index=POLICY_CLAUSES, query="jurisdiction screening", top_k=10
    )
    hit = next(h for h in body["hits"] if h["id"] == "FCRM42-3.1")
    assert "jurisdiction screening at onboarding" in hit["text"]
    assert hit["metadata"]["source_id"] == "INT-FCRM-4.2"


def test_policy_clause_search_filters_by_source_id_via_corpus_version(
    client: TestClient,
) -> None:
    body = _search(
        client,
        index=POLICY_CLAUSES,
        query="screening",
        top_k=10,
        filters={"corpus_version": 14},
    )
    assert {h["id"] for h in body["hits"]} == DEMO_CLAUSE_IDS
    empty = _search(
        client,
        index=POLICY_CLAUSES,
        query="screening",
        top_k=10,
        filters={"corpus_version": 15},
    )
    assert empty["hits"] == []


def test_control_search_filters_by_domain(client: TestClient) -> None:
    body = _search(
        client,
        index=CONTROLS,
        query="due diligence",
        top_k=10,
        filters={"risk_area": "channel"},
    )
    assert all(h["metadata"]["domain"] == "channel" for h in body["hits"])
    assert any(h["id"] == "CTL-T01" for h in body["hits"])


def test_search_rejects_an_unknown_index_as_a_422_problem(client: TestClient) -> None:
    response = client.post(
        "/search", json={"index": "made_up_index", "query": "x", "top_k": 5}
    )
    assert response.status_code == 422
    body = response.json()
    assert body["type"].endswith("/validation_failed")


def test_search_rejects_top_k_above_the_configured_max(client: TestClient) -> None:
    response = client.post(
        "/search", json={"index": POLICY_CLAUSES, "query": "x", "top_k": 31}
    )
    assert response.status_code == 422


def test_search_rejects_a_malformed_body_as_422_problem(client: TestClient) -> None:
    response = client.post("/search", json={"query": "x"})
    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"


def test_healthz_is_always_ok(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}


def test_readyz_is_ok_with_no_checks_registered(client: TestClient) -> None:
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
