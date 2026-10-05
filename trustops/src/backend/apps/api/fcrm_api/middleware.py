"""Per-request component identity for logs and traces.

The process has one OpenTelemetry resource (``service.name = fcrm-api``),
so which mounted service handled a request is carried per request instead:
a ``component`` field on every log line and a ``component.id`` attribute on
the active span.
"""

from __future__ import annotations

from fcrm_common.logging import bound_context
from opentelemetry import trace
from starlette.types import ASGIApp, Receive, Scope, Send


class ComponentContextMiddleware:
    """Pure-ASGI middleware tagging each HTTP request with a component id.

    Args:
        app: The wrapped ASGI application (one mounted service).
        component_id: The service's platform component id (``S2``...).
    """

    def __init__(self, app: ASGIApp, component_id: str) -> None:
        self.app = app
        self.component_id = component_id

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Bind the component for HTTP scopes; pass everything else through."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        trace.get_current_span().set_attribute("component.id", self.component_id)
        with bound_context(component=self.component_id):
            await self.app(scope, receive, send)
