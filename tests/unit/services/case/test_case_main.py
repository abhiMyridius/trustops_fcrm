"""``fcrm_case.main``: ``create_app()`` stays bare for tests while the
module-level ``app`` uvicorn serves is mounted under ``APP_ROOT_PATH``
(D-13/D-14; ``fcrm_scoring.main`` is the reference). No Mongo: the lifespan
is not entered here, so these only prove routing and that every case route
refuses an unauthenticated caller. Lifespan-under-mount (the D-14 regression)
is covered against real Mongo in ``test_case_mongo_live.py``.
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
        ("get", "/cases"),
        ("post", "/cases"),
        ("get", "/cases/FCRM-2026-0001"),
        ("put", "/cases/FCRM-2026-0001/title"),
        ("put", "/cases/FCRM-2026-0001/fields/F01"),
        ("post", "/cases/FCRM-2026-0001/submit"),
        ("get", "/cases/FCRM-2026-0001/versions"),
        ("get", "/cases/FCRM-2026-0001/versions/diff?from_version=1&to_version=2"),
        ("get", "/cases/FCRM-2026-0001/stream"),
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
