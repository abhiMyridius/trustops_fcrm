"""``fcrm_case.main``: ``create_app()`` stays bare for tests while the
module-level ``app`` uvicorn serves is mounted under ``APP_ROOT_PATH``
(D-13/D-14; ``fcrm_scoring.main`` is the reference). No Mongo: the lifespan
is not entered here, so these only prove routing and that every case route
refuses an unauthenticated caller. Lifespan-under-mount (the D-14 regression)
is covered against real Mongo in ``tests/integration/live_mongo``.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from fcrm_case.main import create_app, create_asgi_app


def test_create_app_is_bare_and_serves_unprefixed_paths() -> None:
    client = TestClient(create_app())
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/api/fcrm/healthz").status_code == 404


def test_create_asgi_app_mounts_under_app_root_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ROOT_PATH", "/api/fcrm")
    client = TestClient(create_asgi_app())
    assert client.get("/api/fcrm/healthz").json() == {"status": "ok"}
    assert client.get("/healthz").status_code == 404  # nginx forwards the full path


def test_create_asgi_app_without_a_root_path_is_the_bare_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("APP_ROOT_PATH", raising=False)
    client = TestClient(create_asgi_app())
    assert client.get("/healthz").status_code == 200


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/dashboard"),
        ("get", "/cases"),
        ("post", "/cases"),
        ("get", "/cases/FCRM-2026-0001"),
        ("get", "/cases/FCRM-2026-0001/documents"),
        ("post", "/cases/FCRM-2026-0001/documents"),
        ("get", "/cases/FCRM-2026-0001/intake"),
        ("post", "/cases/FCRM-2026-0001/intake/categories/new_product"),
        ("post", "/cases/FCRM-2026-0001/intake/values/F03"),
        ("post", "/cases/FCRM-2026-0001/intake/reuse"),
        ("post", "/cases/FCRM-2026-0001/intake/answers"),
        ("get", "/cases/FCRM-2026-0001/review"),
        ("post", "/cases/FCRM-2026-0001/validate"),
        ("post", "/cases/FCRM-2026-0001/submit"),
        ("post", "/cases/FCRM-2026-0001/discard"),
        ("get", "/cases/FCRM-2026-0001/activity"),
        ("get", "/integrations"),
        ("post", "/integrations/sharepoint/connect"),
        ("get", "/cases/FCRM-2026-0001/stream"),
        ("get", "/internal/cases/FCRM-2026-0001/record"),
        ("get", "/internal/cases/FCRM-2026-0001/items"),
        ("put", "/internal/cases/FCRM-2026-0001/items"),
        ("put", "/internal/cases/FCRM-2026-0001/intake"),
        ("patch", "/internal/cases/FCRM-2026-0001/state"),
        ("post", "/internal/cases/FCRM-2026-0001/assign"),
        ("post", "/internal/cases/FCRM-2026-0001/events"),
        ("get", "/internal/documents/doc_x"),
        ("patch", "/internal/documents/doc_x"),
        ("get", "/internal/analysts/load"),
    ],
)
def test_every_case_route_refuses_an_unauthenticated_caller(
    method: str, path: str
) -> None:
    client = TestClient(create_app())
    response = getattr(client, method)(path)
    assert response.status_code in (401, 422)  # 422: body missing, never 2xx
    if response.status_code == 401:
        assert response.headers["www-authenticate"].startswith("Bearer")
