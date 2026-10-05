"""``Idempotency-Key`` on S4's two write routes, ``GET /head`` and the web's
``GET /records?case_id=`` row shape, over the real routes and the in-memory
store. The live-Mongo twin (transaction, role, concurrent writers) is in
``test_audit_mongo_live.py``.

A retried H1 step re-sends the same key with the same body; it must get the
record it already wrote (200) and chain nothing.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from fcrm_audit.chain import GENESIS_HASH
from fcrm_audit.routes import checked_idempotency_key
from fcrm_audit.store import InMemoryAuditStore
from fcrm_common.errors import ValidationError as FcrmValidationError

KEY = "idem_0123456789abcdefghijklmnop"
CASE = "FCRM-2026-0151"
BodyFactory = Callable[..., dict[str, Any]]


def _post(
    client: TestClient, path: str, body: dict[str, Any], key: str | None = KEY
) -> httpx.Response:
    headers = {"Idempotency-Key": key} if key is not None else {}
    return client.post(path, json=body, headers=headers)


def test_a_first_use_of_a_key_is_201_and_a_repeat_is_200_with_the_same_record(
    service_client: TestClient, record_body: BodyFactory
) -> None:
    first = _post(service_client, "/records", record_body())
    again = _post(service_client, "/records", record_body())
    assert first.status_code == 201
    assert again.status_code == 200
    assert again.json() == first.json()


def test_a_replayed_key_writes_nothing(
    service_client: TestClient, record_body: BodyFactory
) -> None:
    _post(service_client, "/records", record_body())
    for _ in range(3):
        _post(service_client, "/records", record_body())
    rows = service_client.get("/records", params={"case_id": CASE}).json()
    assert [r["seq"] for r in rows["items"]] == [1]
    assert service_client.get(f"/verify/{CASE}").json()["record_count"] == 1


def test_a_replay_after_other_appends_still_returns_the_original_record(
    service_client: TestClient, record_body: BodyFactory
) -> None:
    first = _post(service_client, "/records", record_body(), key="k-first").json()
    _post(service_client, "/records", record_body(type="workflow.started"), "k-second")
    again = _post(service_client, "/records", record_body(), key="k-first")
    assert again.status_code == 200
    assert again.json() == first
    assert service_client.get(f"/head/{CASE}").json()["seq"] == 2


def test_without_a_key_two_identical_bodies_chain_two_records(
    service_client: TestClient, record_body: BodyFactory
) -> None:
    _post(service_client, "/records", record_body(), key=None)
    _post(service_client, "/records", record_body(), key=None)
    assert service_client.get(f"/head/{CASE}").json()["seq"] == 2


def test_one_key_with_a_different_body_is_a_409_problem(
    service_client: TestClient, record_body: BodyFactory
) -> None:
    _post(service_client, "/records", record_body())
    conflict = _post(service_client, "/records", record_body(summary="another"))
    assert conflict.status_code == 409
    assert conflict.headers["content-type"].startswith("application/problem+json")
    assert conflict.json()["type"].endswith("/conflict")
    # The refusal reveals nothing of the stored record.
    assert "case created" not in conflict.text
    assert service_client.get(f"/head/{CASE}").json()["seq"] == 1


def test_one_key_cannot_be_reused_across_the_two_write_routes(
    service_client: TestClient, record_body: BodyFactory, proposal_body: BodyFactory
) -> None:
    _post(service_client, "/records", record_body())
    assert _post(service_client, "/proposals", proposal_body()).status_code == 409
    other = "idem_other"
    _post(service_client, "/proposals", proposal_body(), key=other)
    assert (
        _post(service_client, "/records", record_body(), key=other).status_code == 409
    )


def test_a_key_is_bound_to_its_body_not_to_its_case(
    service_client: TestClient, record_body: BodyFactory
) -> None:
    _post(service_client, "/records", record_body("FCRM-2026-0001"))
    other_case = _post(service_client, "/records", record_body("FCRM-2026-0002"))
    assert other_case.status_code == 409


def test_a_proposal_replay_returns_the_stored_proposal(
    service_client: TestClient, proposal_body: BodyFactory
) -> None:
    first = _post(service_client, "/proposals", proposal_body())
    again = _post(service_client, "/proposals", proposal_body())
    assert (first.status_code, again.status_code) == (201, 200)
    assert again.json() == first.json()
    page = service_client.get("/proposals", params={"case_id": CASE}).json()
    assert len(page["items"]) == 1


def test_a_proposal_key_with_a_different_payload_is_a_409(
    service_client: TestClient, proposal_body: BodyFactory
) -> None:
    _post(service_client, "/proposals", proposal_body())
    changed = proposal_body(payload={"clauses": ["OFAC-1.2"]})
    assert _post(service_client, "/proposals", changed).status_code == 409


@pytest.mark.parametrize(
    "key", ["", "has space", "tab\there", "x" * 256, "\x7f", "a\nb"]
)
def test_a_malformed_key_is_a_422_and_writes_nothing(
    service_client: TestClient, record_body: BodyFactory, key: str
) -> None:
    response = service_client.post(
        "/records", json=record_body(), headers={"Idempotency-Key": key}
    )
    assert response.status_code in (422, 400)
    assert service_client.get(f"/head/{CASE}").json()["seq"] == 0


@pytest.mark.parametrize(
    "key", ["a", "idem_abc", "uuid-1234-5678", "x" * 255, "a:b/c=d"]
)
def test_checked_idempotency_key_accepts_printable_ascii(key: str) -> None:
    assert checked_idempotency_key(key) == key


def test_checked_idempotency_key_passes_absence_through() -> None:
    assert checked_idempotency_key(None) is None


@pytest.mark.parametrize("key", ["", " ", "a b", "x" * 256, "é"])
def test_checked_idempotency_key_rejects_a_malformed_key(key: str) -> None:
    with pytest.raises(FcrmValidationError):
        checked_idempotency_key(key)


async def test_two_simultaneous_requests_with_one_key_write_one_record(
    async_service_client: httpx.AsyncClient,
    audit_store: InMemoryAuditStore,
    record_body: BodyFactory,
) -> None:
    headers = {"Idempotency-Key": KEY}
    responses = await asyncio.gather(
        *(
            async_service_client.post("/records", json=record_body(), headers=headers)
            for _ in range(5)
        )
    )
    assert sorted(r.status_code for r in responses) == [200, 200, 200, 200, 201]
    assert len({r.json()["hash"] for r in responses}) == 1
    assert (await audit_store.head(CASE)).seq == 1


async def test_two_simultaneous_proposals_with_one_key_write_one_proposal(
    async_service_client: httpx.AsyncClient,
    audit_store: InMemoryAuditStore,
    proposal_body: BodyFactory,
) -> None:
    headers = {"Idempotency-Key": KEY}
    responses = await asyncio.gather(
        *(
            async_service_client.post(
                "/proposals", json=proposal_body(), headers=headers
            )
            for _ in range(4)
        )
    )
    assert sorted(r.status_code for r in responses) == [200, 200, 200, 201]
    assert len({r.json()["proposal_id"] for r in responses}) == 1
    assert len((await audit_store.proposals(CASE)).items) == 1


# ── GET /head/{case_id} ────────────────────────────────────────────────────


def test_head_of_a_case_with_no_records_is_seq_zero_on_the_genesis_hash(
    analyst_client: TestClient,
) -> None:
    response = analyst_client.get(f"/head/{CASE}")
    assert response.status_code == 200
    assert response.json() == {"seq": 0, "hash": GENESIS_HASH}


def test_head_follows_the_last_appended_record(
    service_client: TestClient, record_body: BodyFactory
) -> None:
    last = {}
    for kind in ("case.created", "workflow.started", "case.submitted"):
        last = service_client.post("/records", json=record_body(type=kind)).json()
    head = service_client.get(f"/head/{CASE}").json()
    assert head == {"seq": 3, "hash": last["hash"]}


def test_head_is_per_case(service_client: TestClient, record_body: BodyFactory) -> None:
    service_client.post("/records", json=record_body("FCRM-2026-0001"))
    assert service_client.get("/head/FCRM-2026-0002").json()["seq"] == 0


def test_verify_of_the_empty_demo_case_is_ok_with_zero_records(
    analyst_client: TestClient,
) -> None:
    """The demo case 0151 starts with no records (they are the live run's
    output), and an empty chain verifies trivially."""
    response = analyst_client.get(f"/verify/{CASE}")
    assert response.status_code == 200
    assert response.json() == {
        "case_id": CASE,
        "record_count": 0,
        "ok": True,
        "first_break_seq": None,
    }


# ── GET /records?case_id= (the web's row shape) ─────────────────────────────


def test_records_rows_have_the_web_shape(
    service_client: TestClient, record_body: BodyFactory
) -> None:
    appended = service_client.post(
        "/records",
        json=record_body(
            type="human.override",
            actor="u-riyer",
            summary="CTL-T01 7.6 → 4.5",
            data={"reason": "evidence aged"},
        ),
    ).json()
    page = service_client.get("/records", params={"case_id": CASE}).json()
    assert page["next_cursor"] is None
    (row,) = page["items"]
    assert set(row) == {"seq", "type", "by", "what", "at", "hash", "prev_hash"}
    assert row["seq"] == 1
    assert row["type"] == "human.override"
    assert row["by"] == "u-riyer"
    assert row["what"] == "CTL-T01 7.6 → 4.5"
    assert row["at"] == appended["created_at"]
    assert row["hash"] == appended["hash"]
    assert row["prev_hash"] == GENESIS_HASH


def test_records_rows_page_oldest_first(
    service_client: TestClient, record_body: BodyFactory
) -> None:
    for number in range(5):
        service_client.post("/records", json=record_body(summary=f"record {number}"))
    first = service_client.get("/records", params={"case_id": CASE, "limit": 2}).json()
    assert [r["seq"] for r in first["items"]] == [1, 2]
    assert first["next_cursor"] is not None
    rest = service_client.get(
        "/records",
        params={"case_id": CASE, "limit": 10, "cursor": first["next_cursor"]},
    ).json()
    assert [r["seq"] for r in rest["items"]] == [3, 4, 5]
    assert rest["next_cursor"] is None


def test_records_rows_of_an_empty_case_are_an_empty_page(
    analyst_client: TestClient,
) -> None:
    page = analyst_client.get("/records", params={"case_id": CASE}).json()
    assert page["items"] == []
    assert page["next_cursor"] is None


def test_records_rows_need_a_case_id(analyst_client: TestClient) -> None:
    response = analyst_client.get("/records")
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")


def test_the_per_case_path_keeps_the_backend_shape(
    service_client: TestClient, record_body: BodyFactory
) -> None:
    service_client.post("/records", json=record_body())
    item = service_client.get(f"/records/{CASE}").json()["items"][0]
    assert {"actor", "summary", "data", "created_at"} <= set(item)
    assert "by" not in item
