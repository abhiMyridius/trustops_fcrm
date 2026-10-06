"""S1's watchlist re-flags over the in-memory store: the candidate match, the
idempotent create H1 drives, the analyst's paged list and the acknowledgement
(a reason of at least 20 characters, stored and then announced as
``gate.decided``, allowed on a closed case, and never a score or stage change).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fcrm_case.domain.record import CaseRecord
from fcrm_case.domain.reflags import countries_matched
from fcrm_case.reflag_store import InMemoryReflagStore
from fcrm_case.routes_internal_reflags import router as internal_router
from fcrm_case.routes_reflags import router as analyst_router
from fcrm_common.errors import install_error_handlers
from fcrm_contracts.enums import CaseState, Role

VERSION = "v2026-10-fixture"
RUN = "run_watchlist_1"
REASON = "Checked the corridor evidence: the payout partner does not clear in Algeria."


@pytest.fixture
def reflag_store(make_case_record: Callable[..., CaseRecord]) -> InMemoryReflagStore:
    """Four cases: two open and exposed (one by geography, one by corridor), one
    open and not exposed, one closed and exposed (a closed case is no candidate).
    """
    cases = [
        make_case_record(
            "FCRM-2026-0144",
            title="North Africa trade finance (Algeria)",
            geographies=["Algeria"],
            state=CaseState.REVIEW,
        ),
        make_case_record(
            "FCRM-2026-0150",
            title="Payroll partner",
            geographies=[],
            state=CaseState.ASSESS,
        ),
        make_case_record(
            "FCRM-2026-0139", title="Wealth referral", state=CaseState.ASSESS
        ),
        make_case_record(
            "FCRM-2026-0118",
            title="Closed remittance",
            geographies=["Algeria"],
            state=CaseState.CLOSED,
        ),
    ]
    return InMemoryReflagStore(
        cases, corridors={"FCRM-2026-0150": ["US to Namibia payroll"]}
    )


@pytest.fixture
def reflag_app(
    reflag_store: InMemoryReflagStore, wire_test_auth: Callable[[FastAPI], FastAPI]
) -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(internal_router)
    app.include_router(analyst_router)
    app.state.reflags = reflag_store
    return wire_test_auth(app)


def _as(app: FastAPI, act_as: Callable[[Role, str], None], role: Role) -> TestClient:
    act_as(role, f"user-{role.value}")
    return TestClient(app)


def _reflag(case_id: str, jurisdiction: str, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "reflag_id": f"rf_{case_id[-4:]}_{jurisdiction.lower()}",
        "case_id": case_id,
        "jurisdiction": jurisdiction,
        "list_version": VERSION,
        "list_from": "v2026-06",
        "list_change": "added",
        "fixture": True,
        "reason": f"{jurisdiction} was added to the list; research ran before it.",
        "proposed_by": "agent",
        "rerun": ["research"],
        "list_entry": {"list_id": "FATF", "version": VERSION, "quote": jurisdiction},
        "run_id": RUN,
        "gate_id": f"gate_{case_id[-4:]}_{jurisdiction.lower()}",
    }
    return {**body, **overrides}


def _post(client: TestClient, *reflags: dict[str, Any]) -> Any:
    return client.post(
        "/internal/reflags", json={"list_version": VERSION, "reflags": list(reflags)}
    )


# ── the match ───────────────────────────────────────────────────────────────


def test_countries_matched_reads_geographies_and_corridor_text() -> None:
    assert countries_matched(["algeria"], [], ["Algeria", "Namibia"]) == ["Algeria"]
    assert countries_matched([], ["US to Namibia payroll"], ["Namibia"]) == ["Namibia"]
    assert countries_matched([], ["Congo"], ["Republic of the Congo"]) == []
    assert countries_matched(["Niger"], [], ["Nigeria"]) == []


def test_candidates_are_open_exposed_cases_only(
    reflag_app: FastAPI, act_as: Callable[[Role, str], None]
) -> None:
    client = _as(reflag_app, act_as, Role.SERVICE)
    response = client.get(
        "/internal/reflags/candidates",
        params=[("countries", "Algeria"), ("countries", "Namibia")],
    )
    assert response.status_code == 200
    assert [(c["case_id"], c["jurisdiction"]) for c in response.json()["items"]] == [
        ("FCRM-2026-0144", "Algeria"),
        ("FCRM-2026-0150", "Namibia"),
    ]


@pytest.mark.parametrize("role", [Role.ANALYST, Role.COMMITTEE, Role.PO, Role.OBSERVER])
def test_internal_re_flag_routes_are_service_only(
    reflag_app: FastAPI, act_as: Callable[[Role, str], None], role: Role
) -> None:
    client = _as(reflag_app, act_as, role)
    candidates = client.get("/internal/reflags/candidates", params={"countries": "X"})
    posted = _post(client, _reflag("FCRM-2026-0144", "Algeria"))
    assert (candidates.status_code, posted.status_code) == (403, 403)


# ── create ──────────────────────────────────────────────────────────────────


def test_a_post_creates_each_re_flag_once(
    reflag_app: FastAPI,
    reflag_store: InMemoryReflagStore,
    act_as: Callable[[Role, str], None],
) -> None:
    client = _as(reflag_app, act_as, Role.SERVICE)
    one = _reflag("FCRM-2026-0144", "Algeria")
    two = _reflag("FCRM-2026-0150", "Namibia")
    first = _post(client, one, two)
    again = _post(client, one, two)
    assert first.json()["created"] == 2
    assert again.json() == {
        "created": 0,
        "replayed": 2,
        "reflag_ids": [one["reflag_id"], two["reflag_id"]],
    }
    assert len(reflag_store.rows) == 2
    assert reflag_store.rows[one["reflag_id"]].case_title.startswith("North Africa")


def test_a_post_naming_an_unknown_case_or_another_version_is_422(
    reflag_app: FastAPI, act_as: Callable[[Role, str], None]
) -> None:
    client = _as(reflag_app, act_as, Role.SERVICE)
    unknown = _post(client, _reflag("FCRM-2026-9999", "Algeria"))
    other = _post(client, _reflag("FCRM-2026-0144", "Algeria", list_version="v2026-11"))
    assert (unknown.status_code, other.status_code) == (422, 422)


def test_an_ill_formed_re_flag_is_422(
    reflag_app: FastAPI, act_as: Callable[[Role, str], None]
) -> None:
    client = _as(reflag_app, act_as, Role.SERVICE)
    bad = [
        _reflag("FCRM-2026-0144", "Algeria", reason="too short"),
        _reflag("FCRM-2026-0144", "Algeria", rerun=[]),
        _reflag("FCRM-2026-0144", "Algeria", proposed_by="somebody"),
        _reflag("FCRM-2026-0144", "Algeria", extra=1),
    ]
    assert [_post(client, b).status_code for b in bad] == [422, 422, 422, 422]


# ── the analyst's list ──────────────────────────────────────────────────────


def _seed_two(reflag_app: FastAPI, act_as: Callable[[Role, str], None]) -> None:
    service = _as(reflag_app, act_as, Role.SERVICE)
    _post(
        service,
        _reflag("FCRM-2026-0144", "Algeria"),
        _reflag("FCRM-2026-0150", "Namibia"),
    )


def test_the_list_is_the_webs_shape_and_pages(
    reflag_app: FastAPI, act_as: Callable[[Role, str], None]
) -> None:
    _seed_two(reflag_app, act_as)
    analyst = _as(reflag_app, act_as, Role.ANALYST)
    first = analyst.get("/reflags", params={"limit": 1}).json()
    assert first["total"] == 2
    assert len(first["items"]) == 1
    assert first["next_cursor"]
    second = analyst.get(
        "/reflags", params={"limit": 1, "cursor": first["next_cursor"]}
    ).json()
    assert second["next_cursor"] is None
    seen = {first["items"][0]["id"], second["items"][0]["id"]}
    assert len(seen) == 2
    row = first["items"][0]
    for key in (
        "id",
        "case_id",
        "case_title",
        "jurisdiction",
        "list_version",
        "fixture",
        "reason",
        "acknowledged",
    ):
        assert key in row
    assert row["acknowledged"] is False


@pytest.mark.parametrize("role", [Role.PO, Role.COMMITTEE, Role.OBSERVER, Role.SERVICE])
def test_only_an_analyst_reads_the_list(
    reflag_app: FastAPI, act_as: Callable[[Role, str], None], role: Role
) -> None:
    assert _as(reflag_app, act_as, role).get("/reflags").status_code == 403


def test_a_malformed_cursor_is_422(
    reflag_app: FastAPI, act_as: Callable[[Role, str], None]
) -> None:
    analyst = _as(reflag_app, act_as, Role.ANALYST)
    assert analyst.get("/reflags", params={"cursor": "nonsense"}).status_code == 422


# ── acknowledge ─────────────────────────────────────────────────────────────

ACK = "/reflags/{rid}/acknowledge"


def test_an_acknowledgement_is_stored_then_announced_as_gate_decided(
    reflag_app: FastAPI,
    reflag_store: InMemoryReflagStore,
    act_as: Callable[[Role, str], None],
) -> None:
    _seed_two(reflag_app, act_as)
    rid = "rf_0144_algeria"
    analyst = _as(reflag_app, act_as, Role.ANALYST)
    response = analyst.post(ACK.format(rid=rid), json={"reason": REASON})
    assert response.status_code == 200
    body = response.json()
    assert body["acknowledged"] is True
    assert body["acknowledged_by"] == "user-fcrm_analyst"
    assert body["ack_reason"] == REASON
    assert reflag_store.rows[rid].acknowledged is True
    [event] = reflag_store.events
    assert event.type == "gate.decided"
    assert event.case_id == "FCRM-2026-0144"
    assert event.run_id == RUN
    assert event.payload["gate_id"] == "gate_0144_algeria"
    assert event.payload["gate_type"] == "reflag_ack"
    assert event.payload["decision"] == "acknowledge"
    assert event.payload["reason"] == REASON
    assert event.payload["decided_by"] == "user-fcrm_analyst"


def test_the_web_posts_the_reason_as_a_note(
    reflag_app: FastAPI, act_as: Callable[[Role, str], None]
) -> None:
    _seed_two(reflag_app, act_as)
    analyst = _as(reflag_app, act_as, Role.ANALYST)
    response = analyst.post(
        ACK.format(rid="rf_0144_algeria"), json={"note": REASON, "rerun": True}
    )
    assert response.status_code == 200
    assert response.json()["ack_reason"] == REASON


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"reason": ""},
        {"reason": "too short"},
        {"reason": "x" * 19},
        {"note": "   " + "y" * 10 + "   "},
        {"reason": REASON, "extra": 1},
    ],
)
def test_an_acknowledgement_needs_a_reason_of_twenty_characters(
    reflag_app: FastAPI,
    reflag_store: InMemoryReflagStore,
    act_as: Callable[[Role, str], None],
    body: dict[str, Any],
) -> None:
    _seed_two(reflag_app, act_as)
    analyst = _as(reflag_app, act_as, Role.ANALYST)
    response = analyst.post(ACK.format(rid="rf_0144_algeria"), json=body)
    assert response.status_code == 422
    assert reflag_store.events == []
    assert reflag_store.rows["rf_0144_algeria"].acknowledged is False


def test_acknowledging_twice_is_a_replay_with_one_event_and_another_reason_is_409(
    reflag_app: FastAPI,
    reflag_store: InMemoryReflagStore,
    act_as: Callable[[Role, str], None],
) -> None:
    _seed_two(reflag_app, act_as)
    analyst = _as(reflag_app, act_as, Role.ANALYST)
    url = ACK.format(rid="rf_0144_algeria")
    first = analyst.post(url, json={"reason": REASON})
    again = analyst.post(url, json={"reason": REASON})
    other = analyst.post(url, json={"reason": "A different reason, long enough."})
    assert (first.status_code, again.status_code, other.status_code) == (200, 200, 409)
    assert len(reflag_store.events) == 1


def test_acknowledging_an_unknown_re_flag_is_404(
    reflag_app: FastAPI, act_as: Callable[[Role, str], None]
) -> None:
    analyst = _as(reflag_app, act_as, Role.ANALYST)
    assert (
        analyst.post(ACK.format(rid="rf_nope"), json={"reason": REASON}).status_code
        == 404
    )


@pytest.mark.parametrize("role", [Role.PO, Role.COMMITTEE, Role.OBSERVER, Role.SERVICE])
def test_nobody_but_an_analyst_acknowledges(
    reflag_app: FastAPI,
    reflag_store: InMemoryReflagStore,
    act_as: Callable[[Role, str], None],
    role: Role,
) -> None:
    _seed_two(reflag_app, act_as)
    client = _as(reflag_app, act_as, role)
    response = client.post(ACK.format(rid="rf_0144_algeria"), json={"reason": REASON})
    assert response.status_code == 403
    assert reflag_store.events == []


def test_a_closed_case_re_flag_can_still_be_acknowledged_and_nothing_else_changes(
    reflag_app: FastAPI,
    reflag_store: InMemoryReflagStore,
    act_as: Callable[[Role, str], None],
) -> None:
    service = _as(reflag_app, act_as, Role.SERVICE)
    _post(service, _reflag("FCRM-2026-0118", "Algeria"))
    before = reflag_store.cases["FCRM-2026-0118"].model_copy()
    analyst = _as(reflag_app, act_as, Role.ANALYST)
    response = analyst.post(ACK.format(rid="rf_0118_algeria"), json={"reason": REASON})
    assert response.status_code == 200
    assert reflag_store.cases["FCRM-2026-0118"] == before
    assert reflag_store.cases["FCRM-2026-0118"].state is CaseState.CLOSED


# ── the agents' case_search tool ─────────────────────────────────────────────────

SEARCH = "/internal/cases/search"


def _hits(response: Any) -> list[tuple[str, Optional[str]]]:
    return [(h["case_id"], h["outcome"]) for h in response.json()["hits"]]


def test_case_search_by_geography_tells_open_cases_from_closed_ones(
    reflag_app: FastAPI, act_as: Callable[[Role, str], None]
) -> None:
    client = _as(reflag_app, act_as, Role.SERVICE)
    everything = client.get(
        SEARCH, params={"q": "Algeria", "geography": "Algeria", "closed_only": "false"}
    )
    assert everything.status_code == 200
    assert _hits(everything) == [("FCRM-2026-0118", "closed"), ("FCRM-2026-0144", None)]
    precedents = client.get(SEARCH, params={"q": "Algeria", "geography": "Algeria"})
    assert _hits(precedents) == [("FCRM-2026-0118", "closed")]


def test_case_search_reads_a_corridor_like_the_re_flag_match(
    reflag_app: FastAPI, act_as: Callable[[Role, str], None]
) -> None:
    client = _as(reflag_app, act_as, Role.SERVICE)
    found = client.get(
        SEARCH, params={"q": "Namibia", "geography": "Namibia", "closed_only": "false"}
    )
    assert _hits(found) == [("FCRM-2026-0150", None)]


def test_case_search_by_title_and_top_k(
    reflag_app: FastAPI, act_as: Callable[[Role, str], None]
) -> None:
    client = _as(reflag_app, act_as, Role.SERVICE)
    by_title = client.get(SEARCH, params={"q": "WEALTH", "closed_only": "false"})
    assert _hits(by_title) == [("FCRM-2026-0139", None)]
    trimmed = client.get(SEARCH, params={"q": "a", "closed_only": "false", "top_k": 1})
    assert len(trimmed.json()["hits"]) == 1


@pytest.mark.parametrize(
    "params",
    [{}, {"q": ""}, {"q": "x", "top_k": 0}, {"q": "x", "top_k": 21}, {"q": "x" * 201}],
)
def test_case_search_refuses_a_malformed_request(
    reflag_app: FastAPI, act_as: Callable[[Role, str], None], params: dict[str, Any]
) -> None:
    client = _as(reflag_app, act_as, Role.SERVICE)
    assert client.get(SEARCH, params=params).status_code == 422


@pytest.mark.parametrize("role", [Role.ANALYST, Role.COMMITTEE, Role.PO, Role.OBSERVER])
def test_case_search_is_service_only(
    reflag_app: FastAPI, act_as: Callable[[Role, str], None], role: Role
) -> None:
    assert (
        _as(reflag_app, act_as, role).get(SEARCH, params={"q": "x"}).status_code == 403
    )
