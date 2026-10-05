"""S4's evidence ledger: ``POST /proposals`` and ``GET /proposals``, over the
real routes and the in-memory store (the live-Mongo twin, including the
unique ``{case_id, seq}`` index and the insert-only role, is in
``test_audit_mongo_live.py``).

The ledger is append-only: a proposal is never updated, a later version is a
new entry that names the one it ``supersedes``, and ``seq`` is one monotonic
number per case.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from fcrm_contracts.api.audit import ProposalAppend
from pydantic import ValidationError

CASE = "FCRM-2026-0151"
BodyFactory = Callable[..., dict[str, Any]]


def _append(client: TestClient, body: dict[str, Any]) -> dict[str, Any]:
    response = client.post("/proposals", json=body)
    assert response.status_code == 201, response.text
    result: dict[str, Any] = response.json()
    return result


def test_a_proposal_is_stored_with_an_id_a_seq_and_a_timestamp(
    service_client: TestClient, proposal_body: BodyFactory
) -> None:
    stored = _append(service_client, proposal_body())
    assert stored["proposal_id"].startswith("prp_")
    assert stored["seq"] == 1
    assert stored["superseded_by"] is None
    assert stored["created_at"].endswith("Z")
    assert stored["tokens"] == {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
    }
    for field in ("case_id", "run_id", "agent", "kind", "payload", "model", "tier"):
        assert stored[field] == proposal_body()[field]
    assert stored["prompt_v"] == "research.system@1"


def test_token_usage_is_kept_with_the_proposal(
    service_client: TestClient, proposal_body: BodyFactory
) -> None:
    usage = {"input_tokens": 4100, "output_tokens": 620, "cache_read_tokens": 3000}
    stored = _append(service_client, proposal_body(tokens=usage))
    assert stored["tokens"]["input_tokens"] == 4100
    assert stored["tokens"]["cache_read_tokens"] == 3000
    assert stored["tokens"]["cache_write_tokens"] == 0


def test_seq_is_monotonic_per_case_across_runs(
    service_client: TestClient, proposal_body: BodyFactory
) -> None:
    seqs = [
        _append(service_client, proposal_body(run_id=run, kind=kind))["seq"]
        for run, kind in (
            ("run_1", "extraction"),
            ("run_1", "category"),
            ("run_2", "research"),
            ("run_1", "gaps"),
        )
    ]
    assert seqs == [1, 2, 3, 4]


def test_each_case_has_its_own_seq(
    service_client: TestClient, proposal_body: BodyFactory
) -> None:
    _append(service_client, proposal_body("FCRM-2026-0001"))
    _append(service_client, proposal_body("FCRM-2026-0001"))
    assert _append(service_client, proposal_body("FCRM-2026-0002"))["seq"] == 1


def test_proposals_are_listed_in_seq_order(
    service_client: TestClient, proposal_body: BodyFactory
) -> None:
    for kind in ("screening", "research", "assessment"):
        _append(service_client, proposal_body(kind=kind, agent=kind))
    page = service_client.get("/proposals", params={"case_id": CASE}).json()
    assert [p["seq"] for p in page["items"]] == [1, 2, 3]
    assert [p["kind"] for p in page["items"]] == ["screening", "research", "assessment"]


def test_the_list_filters_by_run_agent_and_kind(
    service_client: TestClient, proposal_body: BodyFactory
) -> None:
    _append(
        service_client, proposal_body(run_id="run_1", agent="intake", kind="extraction")
    )
    _append(
        service_client, proposal_body(run_id="run_1", agent="intake", kind="category")
    )
    _append(
        service_client, proposal_body(run_id="run_2", agent="research", kind="research")
    )

    def kinds(**filters: str) -> list[str]:
        params = {"case_id": CASE, **filters}
        items = service_client.get("/proposals", params=params).json()["items"]
        return [p["kind"] for p in items]

    assert kinds() == ["extraction", "category", "research"]
    assert kinds(run_id="run_1") == ["extraction", "category"]
    assert kinds(agent="research") == ["research"]
    assert kinds(kind="category") == ["category"]
    assert kinds(run_id="run_1", agent="intake", kind="extraction") == ["extraction"]
    assert kinds(run_id="run_2", agent="intake") == []


def test_the_list_never_crosses_cases(
    service_client: TestClient, proposal_body: BodyFactory
) -> None:
    _append(service_client, proposal_body("FCRM-2026-0001"))
    page = service_client.get("/proposals", params={"case_id": "FCRM-2026-0002"})
    assert page.json()["items"] == []


def test_the_list_pages_by_seq(
    service_client: TestClient, proposal_body: BodyFactory
) -> None:
    for _ in range(5):
        _append(service_client, proposal_body())
    first = service_client.get(
        "/proposals", params={"case_id": CASE, "limit": 2}
    ).json()
    assert [p["seq"] for p in first["items"]] == [1, 2]
    assert first["next_cursor"] is not None
    second = service_client.get(
        "/proposals",
        params={"case_id": CASE, "limit": 2, "cursor": first["next_cursor"]},
    ).json()
    assert [p["seq"] for p in second["items"]] == [3, 4]
    last = service_client.get(
        "/proposals",
        params={"case_id": CASE, "limit": 2, "cursor": second["next_cursor"]},
    ).json()
    assert [p["seq"] for p in last["items"]] == [5]
    assert last["next_cursor"] is None


def test_the_list_needs_a_case_id(analyst_client: TestClient) -> None:
    assert analyst_client.get("/proposals").status_code == 422


def test_a_malformed_cursor_is_a_422(analyst_client: TestClient) -> None:
    response = analyst_client.get(
        "/proposals", params={"case_id": CASE, "cursor": "not-a-cursor"}
    )
    assert response.status_code == 422


# ── supersedes: a later version is a new entry ─────────────────────────────


def test_a_later_version_marks_the_earlier_one_superseded(
    service_client: TestClient, proposal_body: BodyFactory
) -> None:
    first = _append(service_client, proposal_body())
    second = _append(service_client, proposal_body(supersedes=first["proposal_id"]))
    items = service_client.get("/proposals", params={"case_id": CASE}).json()["items"]
    by_id = {p["proposal_id"]: p for p in items}
    assert by_id[first["proposal_id"]]["superseded_by"] == second["proposal_id"]
    assert by_id[second["proposal_id"]]["superseded_by"] is None
    assert by_id[second["proposal_id"]]["supersedes"] == first["proposal_id"]
    # The earlier entry is untouched: same seq, same payload.
    assert by_id[first["proposal_id"]]["seq"] == 1
    assert by_id[first["proposal_id"]]["payload"] == first["payload"]


def test_superseding_an_unknown_proposal_is_a_422(
    service_client: TestClient, proposal_body: BodyFactory
) -> None:
    response = service_client.post(
        "/proposals", json=proposal_body(supersedes="prp_missing")
    )
    assert response.status_code == 422


def test_superseding_another_cases_proposal_is_a_422(
    service_client: TestClient, proposal_body: BodyFactory
) -> None:
    other = _append(service_client, proposal_body("FCRM-2026-0001"))
    response = service_client.post(
        "/proposals", json=proposal_body(supersedes=other["proposal_id"])
    )
    assert response.status_code == 422


def test_a_proposal_can_have_only_one_successor(
    service_client: TestClient, proposal_body: BodyFactory
) -> None:
    first = _append(service_client, proposal_body())
    _append(service_client, proposal_body(supersedes=first["proposal_id"]))
    response = service_client.post(
        "/proposals", json=proposal_body(supersedes=first["proposal_id"])
    )
    assert response.status_code == 409
    assert response.json()["type"].endswith("/conflict")


# ── validation ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "overrides",
    [
        {"kind": "Research"},
        {"kind": ""},
        {"kind": "1research"},
        {"agent": ""},
        {"run_id": ""},
        {"case_id": ""},
        {"model": ""},
        {"payload": "not an object"},
        {"tokens": {"input_tokens": -1}},
        {"unexpected": True},
    ],
)
def test_an_invalid_proposal_is_a_422_and_stores_nothing(
    service_client: TestClient, proposal_body: BodyFactory, overrides: dict[str, Any]
) -> None:
    response = service_client.post("/proposals", json=proposal_body(**overrides))
    assert response.status_code == 422
    assert (
        service_client.get("/proposals", params={"case_id": CASE}).json()["items"] == []
    )


def test_a_caller_cannot_choose_the_id_seq_or_time(
    service_client: TestClient, proposal_body: BodyFactory
) -> None:
    for field, value in (
        ("proposal_id", "prp_mine"),
        ("seq", 99),
        ("created_at", "2020-01-01T00:00:00Z"),
        ("superseded_by", "prp_x"),
    ):
        response = service_client.post(
            "/proposals", json=proposal_body(**{field: value})
        )
        assert response.status_code == 422, field


def test_a_payload_that_json_cannot_carry_is_refused_by_the_contract() -> None:
    base = {
        "case_id": CASE,
        "run_id": "run_1",
        "agent": "research",
        "kind": "research",
        "model": "m",
        "prompt_v": "p@1",
        "tier": "strong",
    }
    with pytest.raises(ValidationError, match="no JSON representation"):
        ProposalAppend(**base, payload={"score": float("nan")})
    with pytest.raises(ValidationError, match="lone UTF-16 surrogate"):
        ProposalAppend(**base, payload={"text": "bad \ud800 text"})


def test_the_ledger_has_no_update_or_delete_route(
    service_client: TestClient, proposal_body: BodyFactory
) -> None:
    stored = _append(service_client, proposal_body())
    url = f"/proposals/{stored['proposal_id']}"
    for method in ("put", "patch", "delete"):
        response = getattr(service_client, method)(url)
        assert response.status_code in (404, 405)
