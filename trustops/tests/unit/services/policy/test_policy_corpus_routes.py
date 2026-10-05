"""S3's corpus routes over in-memory stores and the real 197-clause corpus: a
clause by id, the web's policy search (on ``/search`` and ``/policy/search``),
the precedent search and the service-only ``/internal/*`` reads and writes.

The thing under test that matters most is rule 8: every text a response
carries is the stored clause's text exactly as ``clauses.json`` holds it, with
a structured ``source_ref`` that points back at it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from fcrm_contracts.enums import Role
from fcrm_seed.loader import load_raw

CLAUSE_COUNT = 197
DEMO_CLAUSE = "FCRM42-3.1"
WEB_PATHS = ["/search", "/policy/search"]

ClientFor = Callable[[Role], TestClient]


# ── GET /clauses/{id} ─────────────────────────────────────────────────────


def test_a_clause_is_served_verbatim_with_both_citations(
    corpus_as: ClientFor, corpus_texts: dict[str, str]
) -> None:
    response = corpus_as(Role.ANALYST).get(f"/clauses/{DEMO_CLAUSE}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["text"] == corpus_texts[DEMO_CLAUSE]
    assert body["source_ref"] == {
        "kind": "clause",
        "clause_id": DEMO_CLAUSE,
        "corpus_v": 14,
    }
    assert body["ref"]
    assert body["corpus_version"] == 14
    assert body["page"] is not None
    assert body["line_start"] <= body["line_end"]
    assert body["provenance_kind"] in {
        "seed_verbatim",
        "synthetic_internal_standard",
        "synthetic_public_stand_in",
    }


def test_a_pinned_corpus_version_that_does_not_exist_is_a_404(
    corpus_as: ClientFor,
) -> None:
    client = corpus_as(Role.ANALYST)
    assert client.get(f"/clauses/{DEMO_CLAUSE}?corpus_version=14").status_code == 200
    missing = client.get(f"/clauses/{DEMO_CLAUSE}?corpus_version=15")
    assert missing.status_code == 404
    assert missing.headers["content-type"] == "application/problem+json"


def test_an_unknown_clause_is_a_404(corpus_as: ClientFor) -> None:
    assert corpus_as(Role.ANALYST).get("/clauses/NOPE-9.9").status_code == 404


@pytest.mark.parametrize("role", [Role.ANALYST, Role.COMMITTEE, Role.SERVICE])
def test_staff_and_the_service_account_may_read_a_clause(
    corpus_as: ClientFor, role: Role
) -> None:
    assert corpus_as(role).get(f"/clauses/{DEMO_CLAUSE}").status_code == 200


@pytest.mark.parametrize("role", [Role.PO, Role.OBSERVER])
def test_the_po_and_the_observer_may_not_read_a_clause(
    corpus_as: ClientFor, role: Role
) -> None:
    assert corpus_as(role).get(f"/clauses/{DEMO_CLAUSE}").status_code == 403


# ── the web's policy search ───────────────────────────────────────────────


@pytest.mark.parametrize("path", WEB_PATHS)
def test_cash_pickup_agent_returns_clauses_with_verbatim_text_and_a_source_ref(
    corpus_as: ClientFor, corpus_texts: dict[str, str], path: str
) -> None:
    response = corpus_as(Role.ANALYST).post(
        path, json={"q": "cash pickup agent", "case_id": "FCRM-2026-0151"}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["query"] == "cash pickup agent"
    assert body["corpus_version"] == "v14"
    assert body["results"], "a search over 197 clauses always finds something"
    for hit in body["results"]:
        assert hit["quote"] == corpus_texts[hit["clause_id"]]
        assert hit["source_ref"] == {
            "kind": "clause",
            "clause_id": hit["clause_id"],
            "corpus_v": 14,
        }
        assert hit["ref"]
        assert hit["page"] is not None
        assert 0.0 < hit["similarity"] <= 1.0


@pytest.mark.parametrize("path", WEB_PATHS)
def test_a_policy_search_is_ordered_and_bounded_by_top_k(
    corpus_as: ClientFor, path: str
) -> None:
    body = corpus_as(Role.ANALYST).post(
        path, json={"q": "payout agent due diligence", "top_k": 5}
    )

    results = body.json()["results"]
    assert len(results) == 5
    scores = [hit["similarity"] for hit in results]
    assert scores == sorted(scores, reverse=True)
    assert len({hit["clause_id"] for hit in results}) == 5


def test_the_case_id_never_changes_what_a_search_finds(corpus_as: ClientFor) -> None:
    client = corpus_as(Role.ANALYST)
    without = client.post("/policy/search", json={"q": "sanctions screening"})
    with_case = client.post(
        "/policy/search", json={"q": "sanctions screening", "case_id": "FCRM-2026-0151"}
    )
    assert with_case.json() == without.json()


@pytest.mark.parametrize("path", WEB_PATHS)
@pytest.mark.parametrize(
    "body",
    [
        {"q": ""},
        {"q": "x" * 1001},
        {"q": "ok", "top_k": 0},
        {"q": "ok", "top_k": 31},
        {"q": "ok", "unexpected": True},
    ],
)
def test_a_malformed_policy_search_is_a_422(
    corpus_as: ClientFor, path: str, body: dict[str, Any]
) -> None:
    response = corpus_as(Role.ANALYST).post(path, json=body)
    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"


def test_the_policy_search_route_is_not_open_to_committee_or_the_po(
    corpus_as: ClientFor,
) -> None:
    body = {"q": "cash pickup agent"}
    assert corpus_as(Role.SERVICE).post("/policy/search", json=body).status_code == 200
    for role in (Role.COMMITTEE, Role.PO, Role.OBSERVER):
        assert corpus_as(role).post("/policy/search", json=body).status_code == 403


def test_the_tool_shaped_search_still_answers_on_the_same_path(
    corpus_as: ClientFor,
) -> None:
    response = corpus_as(Role.SERVICE).post(
        "/search",
        json={"index": "policy_clauses", "query": "cash pickup agent", "top_k": 4},
    )
    assert response.status_code == 200
    hits = response.json()["hits"]
    assert len(hits) == 4
    assert {"id", "score", "text", "metadata"} <= set(hits[0])
    assert hits[0]["metadata"]["page"] is not None
    assert hits[0]["metadata"]["clause_id"] == hits[0]["id"]


# ── POST /precedents/search ───────────────────────────────────────────────


def _precedent_search(client: TestClient, **body: Any) -> dict[str, Any]:
    response = client.post("/precedents/search", json=body)
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


def test_precedent_search_ranks_closed_cases_and_averages_their_ratings(
    corpus_as: ClientFor,
) -> None:
    body = _precedent_search(
        corpus_as(Role.ANALYST),
        query="payout partner vendor onboarding Mexico remittances",
        top_k=5,
    )

    results = body["results"]
    assert 1 <= len(results) <= 5
    for hit in results:
        assert hit["case_id"].startswith("FCRM-")
        assert hit["closed_date"]
        assert 0.0 < hit["similarity"] <= 1.0
    rated = {c for hit in results for c in hit["control_ratings"]}
    assert set(body["precedent_avg"]) == rated


def test_precedent_search_filters_apply_to_every_hit(
    corpus_as: ClientFor,
) -> None:
    client = corpus_as(Role.ANALYST)
    narrowed = _precedent_search(
        client,
        query="cash handling",
        top_k=10,
        filters={"outcome": "approve_with_conditions", "closed_after": "2026-01-01"},
    )

    assert narrowed["results"]
    assert {h["outcome"] for h in narrowed["results"]} == {"approve_with_conditions"}
    assert all(h["closed_date"] >= "2026-01-01" for h in narrowed["results"])


def test_precedent_search_list_filters_match_any_member(corpus_as: ClientFor) -> None:
    body = _precedent_search(
        corpus_as(Role.SERVICE),
        query="process change",
        top_k=10,
        filters={"trigger_types": ["process_change", "new_geography"]},
    )
    assert body["results"]
    raw = {c["id"]: c for c in load_raw()["cases"]}
    for hit in body["results"]:
        assert set(raw[hit["case_id"]]["change_types"]) & {
            "process_change",
            "new_geography",
        }


def test_precedent_search_date_bounds_form_a_range(corpus_as: ClientFor) -> None:
    body = _precedent_search(
        corpus_as(Role.ANALYST),
        query="process change",
        top_k=10,
        filters={"closed_after": "2026-02-01", "closed_before": "2026-04-01"},
    )
    assert all("2026-02-01" <= h["closed_date"] < "2026-04-01" for h in body["results"])


@pytest.mark.parametrize(
    "body",
    [
        {"query": ""},
        {"query": "x", "top_k": 11},
        {"query": "x", "filters": {"closed_after": "yesterday"}},
        {"query": "x", "filters": {"unknown": 1}},
    ],
)
def test_a_malformed_precedent_search_is_a_422(
    corpus_as: ClientFor, body: dict[str, Any]
) -> None:
    assert (
        corpus_as(Role.ANALYST).post("/precedents/search", json=body).status_code == 422
    )


@pytest.mark.parametrize("role", [Role.COMMITTEE, Role.PO, Role.OBSERVER])
def test_precedent_search_carries_bands_so_only_analyst_and_service_may_call_it(
    corpus_as: ClientFor, role: Role
) -> None:
    response = corpus_as(role).post("/precedents/search", json={"query": "x"})
    assert response.status_code == 403


# ── GET /internal/clauses ─────────────────────────────────────────────────


def _all_internal_clauses(client: TestClient, limit: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        response = client.get("/internal/clauses", params=params)
        assert response.status_code == 200, response.text
        page = response.json()
        items.extend(page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            return items


def test_internal_clauses_pages_the_whole_corpus_in_a_stable_order(
    corpus_as: ClientFor, corpus_texts: dict[str, str]
) -> None:
    items = _all_internal_clauses(corpus_as(Role.SERVICE), limit=60)

    assert len(items) == CLAUSE_COUNT
    assert len({i["clause_id"] for i in items}) == CLAUSE_COUNT
    assert {i["clause_id"]: i["text"] for i in items} == corpus_texts
    assert [i["clause_id"] for i in items] == sorted(
        (i["clause_id"] for i in items), key=lambda cid: f"v14:{cid}"
    )
    for item in items:
        assert {"source_id", "title", "ref", "page", "line_start", "line_end"} <= set(
            item
        )


def test_internal_clauses_reports_the_total_on_every_page(
    corpus_as: ClientFor,
) -> None:
    page = corpus_as(Role.SERVICE).get("/internal/clauses", params={"limit": 10}).json()
    assert page["total"] == CLAUSE_COUNT
    assert len(page["items"]) == 10
    assert page["next_cursor"]


def test_internal_clauses_for_a_version_that_does_not_exist_is_a_404(
    corpus_as: ClientFor,
) -> None:
    response = corpus_as(Role.SERVICE).get(
        "/internal/clauses", params={"corpus_version": 15}
    )
    assert response.status_code == 404


@pytest.mark.parametrize("limit", [0, 201])
def test_internal_clauses_bounds_its_page_size(
    corpus_as: ClientFor, limit: int
) -> None:
    response = corpus_as(Role.SERVICE).get("/internal/clauses", params={"limit": limit})
    assert response.status_code == 422


def test_internal_clauses_refuses_a_malformed_cursor(corpus_as: ClientFor) -> None:
    response = corpus_as(Role.SERVICE).get(
        "/internal/clauses", params={"cursor": "not-a-cursor"}
    )
    assert response.status_code == 422


@pytest.mark.parametrize("role", [Role.ANALYST, Role.COMMITTEE, Role.PO, Role.OBSERVER])
def test_internal_routes_are_for_the_service_account_alone(
    corpus_as: ClientFor, role: Role
) -> None:
    client = corpus_as(role)
    assert client.get("/internal/clauses").status_code == 403
    assert client.get("/internal/precedents").status_code == 403
    assert client.post("/internal/precedents", json=_new_precedent()).status_code == 403


# ── /internal/precedents ──────────────────────────────────────────────────


def _new_precedent(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "case_id": "FCRM-2026-0151",
        "title": "Consumer remittances US to Mexico via payout partner",
        "change_types": ["new_geography", "vendor_onboarding"],
        "domains": ["channel", "geography", "product"],
        "geographies": ["Mexico"],
        "closed_date": "2026-10-02",
        "outcome": "approve_with_conditions",
        "band": 3,
        "control_ratings": {"CTL-T01": 4.5, "CTL-G01": 6.0},
        "unit": "Payments",
    }
    body.update(overrides)
    return body


def test_a_closed_case_becomes_a_precedent_and_asks_for_one_index_build(
    corpus_as: ClientFor, corpus_app: Any
) -> None:
    client = corpus_as(Role.SERVICE)
    before = client.get("/internal/precedents", params={"limit": 1}).json()["total"]

    created = client.post("/internal/precedents", json=_new_precedent())

    assert created.status_code == 201, created.text
    body = created.json()
    assert body == {
        "case_id": "FCRM-2026-0151",
        "created": True,
        "changed": False,
        "index_requested": True,
        "index_version": before + 1,
    }
    events = corpus_app.state.precedents.events
    assert [e.type for e in events] == ["index.build"]
    assert events[0].payload["index"] == "precedent_cases"
    assert events[0].payload["version"] == before + 1
    assert events[0].case_id == "FCRM-2026-0151"


def test_a_new_precedent_is_readable_by_h4_with_its_search_text(
    corpus_as: ClientFor,
) -> None:
    corpus_as(Role.SERVICE).post("/internal/precedents", json=_new_precedent())
    page = (
        corpus_as(Role.SERVICE)
        .get("/internal/precedents", params={"limit": 200})
        .json()
    )
    stored = {item["case_id"]: item for item in page["items"]}
    assert stored["FCRM-2026-0151"]["outcome"] == "approve_with_conditions"
    assert stored["FCRM-2026-0151"]["band"] == 3
    assert "Mexico" in stored["FCRM-2026-0151"]["search_text"]


def test_posting_the_same_closed_case_again_changes_and_enqueues_nothing(
    corpus_as: ClientFor, corpus_app: Any
) -> None:
    client = corpus_as(Role.SERVICE)
    client.post("/internal/precedents", json=_new_precedent())

    again = client.post("/internal/precedents", json=_new_precedent())

    assert again.status_code == 200
    assert again.json()["created"] is False
    assert again.json()["changed"] is False
    assert again.json()["index_requested"] is False
    assert len(corpus_app.state.precedents.events) == 1


def test_a_changed_repost_replaces_the_row_and_asks_for_a_rebuild(
    corpus_as: ClientFor, corpus_app: Any
) -> None:
    client = corpus_as(Role.SERVICE)
    client.post("/internal/precedents", json=_new_precedent())

    changed = client.post("/internal/precedents", json=_new_precedent(band=4))

    assert changed.status_code == 200
    assert changed.json()["changed"] is True
    assert changed.json()["index_requested"] is True
    assert len(corpus_app.state.precedents.events) == 2
    first, second = corpus_app.state.precedents.events
    assert first.event_id != second.event_id


def test_a_closed_case_is_stored_in_the_seed_s_outcome_vocabulary(
    corpus_as: ClientFor,
) -> None:
    client = corpus_as(Role.SERVICE)
    client.post("/internal/precedents", json=_new_precedent(outcome="approve"))
    client.post(
        "/internal/precedents",
        json=_new_precedent(case_id="FCRM-2026-0152", outcome="defer"),
    )
    page = client.get("/internal/precedents", params={"limit": 200}).json()
    outcomes = {i["case_id"]: i["outcome"] for i in page["items"]}
    assert outcomes["FCRM-2026-0151"] == "approved"
    assert outcomes["FCRM-2026-0152"] == "deferred"


@pytest.mark.parametrize(
    "overrides",
    [
        {"outcome": "approved"},
        {"closed_date": "02/10/2026"},
        {"band": 5},
        {"title": ""},
        {"case_id": ""},
    ],
)
def test_a_malformed_precedent_is_a_422(
    corpus_as: ClientFor, overrides: dict[str, Any]
) -> None:
    response = corpus_as(Role.SERVICE).post(
        "/internal/precedents", json=_new_precedent(**overrides)
    )
    assert response.status_code == 422


def test_internal_precedents_pages_all_sixty_five_seeded_cases(
    corpus_as: ClientFor,
) -> None:
    client = corpus_as(Role.SERVICE)
    first = client.get("/internal/precedents", params={"limit": 40}).json()
    second = client.get(
        "/internal/precedents",
        params={"limit": 40, "cursor": first["next_cursor"]},
    ).json()
    assert first["total"] == 65
    assert len(first["items"]) == 40
    assert len(second["items"]) == 25
    assert second["next_cursor"] is None
    ids = [i["case_id"] for i in first["items"] + second["items"]]
    assert ids == sorted(ids)


# ── POST /jurisdictions/versions/{version}/activate ───────────────────────


def test_activating_the_fixture_list_swaps_the_active_list_and_announces_it(
    corpus_as: ClientFor, corpus_app: Any, raw_seed: dict[str, Any]
) -> None:
    client = corpus_as(Role.ANALYST)

    response = client.post("/jurisdictions/versions/v2026-10-fixture/activate")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["version"] == "v2026-10-fixture"
    assert body["previous_version"] == "v2026-06"
    assert body["fixture"] is True
    assert body["already_active"] is False
    fixture = raw_seed["jurisdictions"]["fixture"]
    assert body["added"] == fixture["added"]
    assert body["removed"] == fixture["removed"]
    versions = {
        v["version"]: v["active"]
        for v in client.get("/jurisdictions/versions").json()["items"]
    }
    assert versions == {"v2026-06": False, "v2026-10-fixture": True}
    [event] = corpus_app.state.reference.events
    assert event.type == "jurisdiction.list_published"
    assert event.payload == {
        "version": "v2026-10-fixture",
        "added": body["added"],
        "removed": body["removed"],
        "fixture": True,
    }


def test_the_fixture_alias_names_the_fixture_list(corpus_as: ClientFor) -> None:
    response = corpus_as(Role.ANALYST).post("/jurisdictions/versions/fixture/activate")
    assert response.status_code == 200
    assert response.json()["version"] == "v2026-10-fixture"


def test_activating_the_active_list_again_announces_nothing(
    corpus_as: ClientFor, corpus_app: Any
) -> None:
    client = corpus_as(Role.ANALYST)
    first = client.post("/jurisdictions/versions/v2026-10-fixture/activate")
    again = client.post("/jurisdictions/versions/v2026-10-fixture/activate")

    assert first.json()["already_active"] is False
    assert again.status_code == 200
    assert again.json()["already_active"] is True
    assert len(corpus_app.state.reference.events) == 1


def test_activating_an_unknown_list_is_a_404(corpus_as: ClientFor) -> None:
    response = corpus_as(Role.ANALYST).post("/jurisdictions/versions/v1999/activate")
    assert response.status_code == 404


@pytest.mark.parametrize("role", [Role.SERVICE, Role.COMMITTEE, Role.PO, Role.OBSERVER])
def test_only_the_analyst_activates_a_jurisdiction_list(
    corpus_as: ClientFor, corpus_app: Any, role: Role
) -> None:
    response = corpus_as(role).post("/jurisdictions/versions/fixture/activate")
    assert response.status_code == 403
    assert corpus_app.state.reference.events == []
