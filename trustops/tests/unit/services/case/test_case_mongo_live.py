"""S1's Mongo-backed store and the full lifespan against a real MongoDB 8
replica set (transactions and the outbox need ``rs0``).

Skipped unless ``FCRM_TEST_MONGO_URI`` points at a replica set, exactly
like S2's ``test_mongo_live.py``::

    export FCRM_TEST_MONGO_URI='mongodb://127.0.0.1:27217/?replicaSet=rs0'
    uv run --project src/backend --all-packages pytest \\
        tests/unit/services/case/test_case_mongo_live.py

Each run uses its own throw-away database (never ``fcrm_case``), so it is
safe against a Mongo other work is using.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fcrm_case.lifespan import lifespan
from fcrm_case.main import create_app
from fcrm_case.routes import router
from fcrm_common.asgi import mount_at_root_path
from fcrm_common.auth import current_user
from fcrm_common.errors import install_error_handlers
from fcrm_common.health import ReadinessProbe, build_health_router
from fcrm_contracts.common import Principal
from fcrm_contracts.enums import Role
from pymongo import AsyncMongoClient

URI = os.environ.get("FCRM_TEST_MONGO_URI", "")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not URI, reason="FCRM_TEST_MONGO_URI not set"),
]

PO_SUB = "u-priya"


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


async def _drop(db_name: str) -> None:
    client: AsyncMongoClient[Any] = AsyncMongoClient(URI)
    await client.drop_database(db_name)
    await client.close()


async def _outbox_types(db_name: str) -> list[str]:
    client: AsyncMongoClient[Any] = AsyncMongoClient(URI)
    try:
        rows = await client[db_name]["outbox"].find({}).sort("_id", 1).to_list()
        return [r["type"] for r in rows]
    finally:
        await client.close()


@pytest.fixture
def db_name() -> Iterator[str]:
    name = f"fcrm_case_t_{uuid.uuid4().hex[:8]}"
    yield name
    _run(_drop(name))


@pytest.fixture
def app(db_name: str, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setenv("MONGODB_URI", URI)
    monkeypatch.setenv("MONGODB_DB", db_name)
    monkeypatch.setenv("SERVICE_TOKEN_SECRET", "x" * 32)
    monkeypatch.setenv("OPA_URL", "http://localhost:8181")
    monkeypatch.setenv("IDENTITY_VALIDATE_URL", "http://localhost:8000/core/validate")
    monkeypatch.setenv("ENV", "test")

    application = FastAPI(lifespan=lifespan)
    install_error_handlers(application)
    application.state.probe = ReadinessProbe()
    application.include_router(router)
    application.include_router(build_health_router(application.state.probe))

    def _po() -> Principal:
        return Principal(sub=PO_SUB, roles=[Role.PO], name=PO_SUB, typ="access")

    application.dependency_overrides[current_user] = _po
    return application


def _new_case(client: TestClient) -> str:
    response = client.post(
        "/cases",
        json={
            "title": "Consumer remittances pilot",
            "unit": "Payments",
            "owner": PO_SUB,
            "change_types": ["vendor_onboarding", "new_geography", "new_product"],
        },
    )
    assert response.status_code == 201
    case_id: str = response.json()["case_id"]
    return case_id


def test_the_full_lifespan_boots_and_readyz_checks_mongo(app: FastAPI) -> None:
    with TestClient(app) as client:
        ready = client.get("/readyz")
        assert ready.status_code == 200
        assert ready.json()["checks"]["mongo"]["ok"] is True


def test_create_persists_the_case_and_its_outbox_event_together(
    app: FastAPI, db_name: str
) -> None:
    with TestClient(app) as client:
        first = _new_case(client)
        second = _new_case(client)
        detail = client.get(f"/cases/{first}").json()
    assert first != second
    assert first.startswith("FCRM-")
    assert int(second.rsplit("-", 1)[1]) == int(first.rsplit("-", 1)[1]) + 1
    # The demo's three change types resolve to 18 required fields (field set v6).
    assert detail["required_field_count"] == 18
    assert _run(_outbox_types(db_name)) == ["case.created", "case.created"]


def test_field_writes_use_a_dotted_path_and_do_not_clobber_each_other(
    app: FastAPI,
) -> None:
    with TestClient(app) as client:
        case_id = _new_case(client)
        for field_id, value in [("F01", "Priya Nair"), ("F02", "Remittances")]:
            response = client.put(
                f"/cases/{case_id}/fields/{field_id}", json={"value": value}
            )
            assert response.status_code == 200
        detail = client.get(f"/cases/{case_id}").json()
    values = {f["field_id"]: f["value"] for f in detail["fields"]}
    assert values["F01"] == "Priya Nair"
    assert values["F02"] == "Remittances"
    assert detail["required_fields_filled"] == 2


def test_submit_is_atomic_and_one_shot_and_closes_the_case_to_writes(
    app: FastAPI, db_name: str
) -> None:
    with TestClient(app) as client:
        case_id = _new_case(client)
        required = [
            f["field_id"] for f in client.get(f"/cases/{case_id}").json()["fields"]
        ]
        for field_id in required:
            client.put(f"/cases/{case_id}/fields/{field_id}", json={"value": "x"})

        first = client.post(
            f"/cases/{case_id}/submit", json={"owner_reviewed_corrections": True}
        )
        assert first.status_code == 200
        assert first.json()["submission_version"] == 1

        second = client.post(
            f"/cases/{case_id}/submit", json={"owner_reviewed_corrections": True}
        )
        assert second.status_code == 409
        write = client.put(f"/cases/{case_id}/fields/F01", json={"value": "late"})
        assert write.status_code == 409
        title = client.put(f"/cases/{case_id}/title", json={"title": "renamed"})
        assert title.status_code == 409

    # Exactly one case.submitted: the rejected second submit rolled back
    # its transaction, so it enqueued nothing.
    assert _run(_outbox_types(db_name)) == ["case.created", "case.submitted"]


def test_list_paginates_with_the_real_cursor(app: FastAPI) -> None:
    with TestClient(app) as client:
        ids = [_new_case(client) for _ in range(3)]
        page = client.get("/cases", params={"limit": 2}).json()
        assert [i["case_id"] for i in page["items"]] == ids[:2]
        assert page["next_cursor"] is not None
        rest = client.get(
            "/cases", params={"limit": 2, "cursor": page["next_cursor"]}
        ).json()
    assert [i["case_id"] for i in rest["items"]] == ids[2:]
    assert rest["next_cursor"] is None


def test_the_mounted_app_runs_its_lifespan_so_readyz_is_not_vacuous(
    db_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The D-14 regression, for S1: under ``mount_at_root_path`` the real
    lifespan must still run. If it silently did not, ``/readyz`` would
    answer 200 with *no* checks registered -- so assert the mongo check is
    actually there.
    """
    monkeypatch.setenv("MONGODB_URI", URI)
    monkeypatch.setenv("MONGODB_DB", db_name)
    monkeypatch.setenv("SERVICE_TOKEN_SECRET", "x" * 32)
    monkeypatch.setenv("OPA_URL", "http://localhost:8181")
    monkeypatch.setenv("IDENTITY_VALIDATE_URL", "http://localhost:8000/core/validate")
    monkeypatch.setenv("ENV", "test")
    mounted = mount_at_root_path(create_app(), "/api/fcrm")
    with TestClient(mounted) as client:
        ready = client.get("/api/fcrm/readyz")
        assert ready.status_code == 200
        assert ready.json()["checks"]["mongo"]["ok"] is True
        # ...and the lifespan-installed verifier is really there: a junk
        # bearer token is a clean 401 from the verifier, not "Authentication
        # is not configured" (503) from a lifespan that never ran.
        junk = client.get(
            "/api/fcrm/cases", headers={"Authorization": "Bearer not-a-jwt"}
        )
        assert junk.status_code == 401
