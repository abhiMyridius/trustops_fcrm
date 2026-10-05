"""fcrm_api.middleware: the component id reaches logs and the active span."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fcrm_api import middleware
from fcrm_api.middleware import ComponentContextMiddleware
from fcrm_common.logging import get_context


class _Span:
    """Records the attributes the middleware sets on the active span."""

    def __init__(self) -> None:
        self.attributes: dict[str, Any] = {}

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value


@pytest.fixture
def span(monkeypatch: pytest.MonkeyPatch) -> _Span:
    recorded = _Span()
    monkeypatch.setattr(middleware.trace, "get_current_span", lambda: recorded)
    return recorded


def _app_with(component_id: str, seen: list[Any]) -> FastAPI:
    app = FastAPI()

    @app.get("/x")
    async def handler() -> dict[str, bool]:
        seen.append(get_context().get("component"))
        return {"ok": True}

    app.add_middleware(ComponentContextMiddleware, component_id=component_id)
    return app


def test_binds_the_component_for_the_request_and_clears_it_after(span: _Span):
    seen: list[Any] = []
    TestClient(_app_with("S9", seen)).get("/x")
    assert seen == ["S9"]
    assert "component" not in get_context()


def test_sets_component_id_on_the_active_span(span: _Span):
    TestClient(_app_with("S9", [])).get("/x")
    assert span.attributes == {"component.id": "S9"}


def test_lifespan_scopes_pass_through_untouched(span: _Span):
    started: list[bool] = []

    async def lifespan(_: FastAPI):
        started.append(True)
        yield

    app = FastAPI(lifespan=lifespan)
    app.add_middleware(ComponentContextMiddleware, component_id="S9")
    with TestClient(app):
        pass
    assert started == [True]
    assert span.attributes == {}
