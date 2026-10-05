"""fcrm_common.asgi: mounting a bare app under APP_ROOT_PATH (D-13)."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from fcrm_common.asgi import mount_at_root_path


def _bare_app() -> FastAPI:
    app = FastAPI()

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok"}

    return app


def _app_with_lifespan_state() -> FastAPI:
    """A route that only succeeds if its own app's ``lifespan`` actually
    ran -- the regression case for the bug this module's docstring
    describes (an outer ``Starlette``+``Mount`` wrapper silently never
    invoking the inner app's lifespan, so lifespan-set state like a
    Mongo connection or a signing key was never there).
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.ready = True
        yield

    app = FastAPI(lifespan=lifespan)

    @app.get("/needs-state")
    async def needs_state(request: Request) -> dict:
        return {"ready": request.app.state.ready}

    return app


def test_mount_at_root_path_prefixes_every_route(monkeypatch):
    monkeypatch.delenv("APP_ROOT_PATH", raising=False)
    client = TestClient(mount_at_root_path(_bare_app(), "/api/fcrm/auth"))
    assert client.get("/api/fcrm/auth/healthz").status_code == 200
    assert client.get("/healthz").status_code == 404


def test_mount_at_root_path_reads_the_environment_by_default(monkeypatch):
    monkeypatch.setenv("APP_ROOT_PATH", "/api/fcrm/scoring")
    client = TestClient(mount_at_root_path(_bare_app()))
    assert client.get("/api/fcrm/scoring/healthz").status_code == 200


def test_mount_at_root_path_empty_prefix_is_unwrapped(monkeypatch):
    monkeypatch.delenv("APP_ROOT_PATH", raising=False)
    bare = _bare_app()
    assert mount_at_root_path(bare) is bare


def test_mount_at_root_path_trailing_slash_is_normalised(monkeypatch):
    monkeypatch.delenv("APP_ROOT_PATH", raising=False)
    client = TestClient(mount_at_root_path(_bare_app(), "/api/fcrm/auth/"))
    assert client.get("/api/fcrm/auth/healthz").status_code == 200


def test_mount_at_root_path_still_runs_the_wrapped_app_s_lifespan(monkeypatch):
    """The regression test for this module's documented bug: a mounted
    app's own ``lifespan`` must actually run (Mongo connections, signing
    keys, readiness checks all depend on it), not just its routes."""
    monkeypatch.delenv("APP_ROOT_PATH", raising=False)
    mounted = mount_at_root_path(_app_with_lifespan_state(), "/api/fcrm/auth")
    with TestClient(mounted) as client:
        response = client.get("/api/fcrm/auth/needs-state")
        assert response.status_code == 200
        assert response.json() == {"ready": True}


def test_mount_at_root_path_forwards_root_path_for_url_generation(monkeypatch):
    monkeypatch.delenv("APP_ROOT_PATH", raising=False)
    app = _bare_app()

    @app.get("/root-path")
    async def root_path(request: Request) -> dict:
        return {"root_path": request.scope.get("root_path")}

    client = TestClient(mount_at_root_path(app, "/api/fcrm/auth"))
    response = client.get("/api/fcrm/auth/root-path")
    assert response.json() == {"root_path": "/api/fcrm/auth"}
