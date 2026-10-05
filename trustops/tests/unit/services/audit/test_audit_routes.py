"""HTTP-layer tests for S4, with no Mongo dependency: the route layer
takes whatever implements ``AuditStore``, so these wire in
``InMemoryAuditStore`` and exercise the app exactly as a caller would
over HTTP -- same shape as ``tests/unit/services/scoring/test_routes.py``.
The caller is the service account (the only role that may append); the
role matrix is covered in ``test_audit_auth.py``.
"""

from __future__ import annotations

import copy
from collections.abc import Callable

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fcrm_audit.routes import router
from fcrm_audit.seedmodel import build_demo_chain
from fcrm_audit.store import InMemoryAuditStore
from fcrm_common.errors import install_error_handlers
from fcrm_common.health import ReadinessProbe, build_health_router
from fcrm_contracts.enums import Role
from fcrm_seed.loader import demo_case


@pytest.fixture
def app(wire_test_auth: Callable[[FastAPI], FastAPI]) -> FastAPI:
    application = FastAPI()
    install_error_handlers(application)
    application.state.probe = ReadinessProbe()
    application.include_router(router)
    application.include_router(build_health_router(application.state.probe))
    application.state.store = InMemoryAuditStore()
    return wire_test_auth(application)


@pytest.fixture
def client(app: FastAPI, act_as: Callable[[Role, str], None]) -> TestClient:
    act_as(Role.SERVICE, "svc:caseflow")
    return TestClient(app)


def _append(client: TestClient, case_id: str, **overrides: object) -> dict:
    body = {
        "case_id": case_id,
        "type": "case.created",
        "actor": "system",
        "summary": "case created",
        "data": {},
        **overrides,
    }
    response = client.post("/records", json=body)
    assert response.status_code == 201
    return response.json()


def test_post_records_builds_the_first_record_on_the_genesis_hash(
    client: TestClient,
) -> None:
    record = _append(client, "FCRM-2026-TEST")
    assert record["seq"] == 1
    assert record["prev_hash"] == "0" * 64
    assert len(record["hash"]) == 64


def test_post_records_chains_a_second_record_to_the_first(client: TestClient) -> None:
    first = _append(client, "FCRM-2026-TEST")
    second = _append(client, "FCRM-2026-TEST", type="human.override", actor="u_1")
    assert second["seq"] == 2
    assert second["prev_hash"] == first["hash"]


def test_post_records_rejects_a_malformed_body_as_422_problem(
    client: TestClient,
) -> None:
    response = client.post("/records", json={"case_id": "FCRM-2026-TEST"})
    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["type"].endswith("/validation_failed")
    assert body["errors"]


def test_post_records_rejects_a_caller_supplied_hash_as_422(
    client: TestClient,
) -> None:
    """``RecordAppend`` has no ``seq``/``prev_hash``/``hash`` fields at all
    (``extra="forbid"``); a caller trying to supply them gets a 422, not a
    silently-ignored field -- the transport-boundary half of rule 1
    ("scores/chain values come only from the service, never the caller").
    """
    response = client.post(
        "/records",
        json={
            "case_id": "FCRM-2026-TEST",
            "type": "case.created",
            "actor": "system",
            "summary": "x",
            "seq": 99,
        },
    )
    assert response.status_code == 422


def test_get_verify_is_ok_for_an_untouched_chain(client: TestClient) -> None:
    _append(client, "FCRM-2026-TEST")
    _append(client, "FCRM-2026-TEST", type="human.override")
    response = client.get("/verify/FCRM-2026-TEST")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["record_count"] == 2
    assert body["first_break_seq"] is None


def test_get_verify_is_true_with_zero_records_for_an_unknown_case(
    client: TestClient,
) -> None:
    response = client.get("/verify/FCRM-2026-NOPE")
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "case_id": "FCRM-2026-NOPE",
        "record_count": 0,
        "ok": True,
        "first_break_seq": None,
    }


def test_get_verify_catches_a_tampered_record(
    app: FastAPI, act_as: Callable[[Role, str], None]
) -> None:
    """Builds the store directly with a tampered chain (rather than going
    through ``POST /records``, which cannot produce one): exercises the
    real ``verify_chain`` through the HTTP layer end to end.
    """
    act_as(Role.ANALYST, "u-riyer")
    demo = demo_case()
    records = build_demo_chain(demo)
    tampered = copy.deepcopy(records)
    tampered[10] = tampered[10].model_copy(update={"summary": "tampered"})
    app.state.store = InMemoryAuditStore(tampered)

    with TestClient(app) as client:
        response = client.get(f"/verify/{demo['id']}")
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is False
        assert body["first_break_seq"] == 11


def test_get_verify_passes_for_the_real_demo_chain(
    app: FastAPI, act_as: Callable[[Role, str], None]
) -> None:
    """The same 67-record replay ``test_chain.py`` proves at the pure
    function level, now proven again through the HTTP layer.
    """
    act_as(Role.COMMITTEE, "u-amendes")
    demo = demo_case()
    app.state.store = InMemoryAuditStore(build_demo_chain(demo))

    with TestClient(app) as client:
        response = client.get(f"/verify/{demo['id']}")
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert body["record_count"] == 67


def test_get_records_paginates_oldest_first(client: TestClient) -> None:
    for i in range(5):
        _append(client, "FCRM-2026-TEST", summary=f"step {i}")

    first_page = client.get("/records/FCRM-2026-TEST", params={"limit": 2})
    assert first_page.status_code == 200
    first_body = first_page.json()
    assert [r["seq"] for r in first_body["items"]] == [1, 2]
    assert first_body["next_cursor"] is not None
    assert first_body["total"] == 5

    second_page = client.get(
        "/records/FCRM-2026-TEST",
        params={"limit": 2, "cursor": first_body["next_cursor"]},
    )
    second_body = second_page.json()
    assert [r["seq"] for r in second_body["items"]] == [3, 4]

    third_page = client.get(
        "/records/FCRM-2026-TEST",
        params={"limit": 2, "cursor": second_body["next_cursor"]},
    )
    third_body = third_page.json()
    assert [r["seq"] for r in third_body["items"]] == [5]
    assert third_body["next_cursor"] is None


def test_get_records_rejects_a_malformed_cursor_as_422(client: TestClient) -> None:
    response = client.get(
        "/records/FCRM-2026-TEST", params={"cursor": "not-a-real-cursor"}
    )
    assert response.status_code == 422
    assert response.json()["type"].endswith("/validation_failed")


def test_get_records_rejects_a_limit_above_the_max_as_422(client: TestClient) -> None:
    response = client.get("/records/FCRM-2026-TEST", params={"limit": 10_000})
    assert response.status_code == 422


def test_healthz_is_always_ok(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}


def test_readyz_is_ok_with_no_checks_registered(client: TestClient) -> None:
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_post_records_with_a_lone_surrogate_is_a_422_not_a_500(
    client: TestClient,
) -> None:
    """The JSON escape ``\\ud800`` is valid JSON text that ``json.loads``
    turns into a lone surrogate -- a string Python cannot UTF-8 encode. It
    used to blow up in ``canonical_json`` as a 500."""
    raw = (
        '{"case_id": "FCRM-2026-TEST", "type": "case.created", "actor": "system",'
        ' "summary": "bad \\ud800 text", "data": {"\\udfff": 1}}'
    )
    response = client.post(
        "/records", content=raw, headers={"content-type": "application/json"}
    )
    assert response.status_code == 422
    assert response.json()["type"].endswith("/validation_failed")


def test_post_records_with_nan_in_data_is_a_422(client: TestClient) -> None:
    raw = (
        '{"case_id": "FCRM-2026-TEST", "type": "case.created", "actor": "system",'
        ' "summary": "x", "data": {"amount": NaN}}'
    )
    response = client.post(
        "/records", content=raw, headers={"content-type": "application/json"}
    )
    assert response.status_code == 422


def test_get_records_hashes_are_reproducible_by_a_third_party(
    client: TestClient,
) -> None:
    """Take the served JSON of ``GET /records`` and recompute every hash with
    nothing but the standard library: ``sha256(prev_hash + "\\n" +
    sorted-key compact JSON of the record minus prev_hash/hash)``."""
    import hashlib
    import json

    for i in range(3):
        _append(client, "FCRM-2026-TEST", summary=f"étape {i}", data={"i": i})
    items = client.get("/records/FCRM-2026-TEST").json()["items"]
    assert len(items) == 3
    prev = "0" * 64
    for item in items:
        assert item["prev_hash"] == prev
        body = {k: v for k, v in item.items() if k not in ("prev_hash", "hash")}
        canonical = json.dumps(
            body, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        recomputed = hashlib.sha256(prev.encode() + b"\n" + canonical).hexdigest()
        assert recomputed == item["hash"], item["seq"]
        prev = item["hash"]
