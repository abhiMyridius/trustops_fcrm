"""``mount_at_root_path``: an empty root path is a no-op; a non-empty one
answers at the prefix and 404s at the bare path, matching nginx's
full-path forwarding (``ops/nginx/default.conf``).
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from fcrm_common.asgi import mount_at_root_path


def _bare_app() -> FastAPI:
    app = FastAPI()

    @app.get("/readyz")
    def readyz() -> dict:
        return {"status": "ok"}

    return app


def test_empty_root_path_returns_the_same_app_unchanged() -> None:
    app = _bare_app()
    assert mount_at_root_path(app, "") is app


def test_non_empty_root_path_answers_at_the_prefixed_path() -> None:
    mounted = mount_at_root_path(_bare_app(), "/api/fcrm/scoring")
    client = TestClient(mounted)
    response = client.get("/api/fcrm/scoring/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_non_empty_root_path_404s_at_the_bare_path() -> None:
    """The bare path must not also work -- nginx forwards the full path,
    so a request that reaches the container at ``/readyz`` instead of
    ``/api/fcrm/scoring/readyz`` would mean something upstream is
    stripping the prefix, which is not this design (D-13)."""
    mounted = mount_at_root_path(_bare_app(), "/api/fcrm/scoring")
    client = TestClient(mounted)
    response = client.get("/readyz")
    assert response.status_code == 404


def test_a_sibling_prefix_does_not_also_match() -> None:
    mounted = mount_at_root_path(_bare_app(), "/api/fcrm/scoring")
    client = TestClient(mounted)
    response = client.get("/api/fcrm/policy/readyz")
    assert response.status_code == 404
