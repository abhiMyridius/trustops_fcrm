"""Per-request component identity for logs and traces, and the HTTP metrics.

The process has one OpenTelemetry resource (``service.name = fcrm-api``),
so which mounted service handled a request is carried per request instead:
a ``component`` field on every log line and a ``component.id`` attribute on
the active span. :class:`HttpMetricsMiddleware` records
``fcrm_http_requests_total`` and ``fcrm_http_latency_ms`` per mounted service,
labelled by the route template (never the raw path: cardinality).
"""

from __future__ import annotations

import logging
import time
from typing import Final, Optional

from fcrm_common.logging import bound_context
from fcrm_common.metrics import FcrmMetrics, get_metrics
from opentelemetry import trace
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)

#: The ``route`` label of a request no route matched (a 404).
UNMATCHED_ROUTE: Final = "unmatched"
#: Probe paths left out of the HTTP metrics (polled every few seconds).
_PROBE_SUFFIXES: Final = ("/healthz", "/readyz")
#: The ``status`` label of a request whose handler raised.
_FAILED_STATUS: Final = "500"


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


class HttpMetricsMiddleware:
    """Pure-ASGI middleware counting and timing each HTTP request of one
    mounted service.

    Labels are ``service`` (the component id), ``method``, ``route`` (the
    matched route template, or ``unmatched``) and ``status``. Health and
    readiness probes are not recorded. Recording never changes the response:
    a metric failure is logged and swallowed, and an exception from the
    application is recorded as ``500`` and re-raised unchanged.

    Args:
        app: The wrapped ASGI application (one mounted service).
        component_id: The service's platform component id (``S2``...).
        metrics: The instrument set to record on; the process-wide one when
            omitted (tests inject their own).
    """

    def __init__(
        self,
        app: ASGIApp,
        component_id: str,
        metrics: Optional[FcrmMetrics] = None,
    ) -> None:
        self.app = app
        self.component_id = component_id
        self._metrics = metrics if metrics is not None else get_metrics()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Time and count HTTP scopes; pass everything else through."""
        if scope["type"] != "http" or str(scope.get("path", "")).endswith(
            _PROBE_SUFFIXES
        ):
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        status: Optional[int] = None

        async def send_and_capture(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
            await send(message)

        failed = True
        try:
            await self.app(scope, receive, send_and_capture)
            failed = False
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            label = _FAILED_STATUS if failed or status is None else str(status)
            self._record(scope, label, elapsed_ms)

    def _record(self, scope: Scope, status: str, elapsed_ms: float) -> None:
        """Add one request and its latency; a metric error is logged only."""
        template = getattr(scope.get("route"), "path", None)
        attributes = {
            "service": self.component_id,
            "method": str(scope.get("method", "")),
            "route": template if isinstance(template, str) else UNMATCHED_ROUTE,
            "status": status,
        }
        try:
            self._metrics.http_requests_total.add(1, attributes)
            self._metrics.http_latency_ms.record(elapsed_ms, attributes)
        except (ValueError, TypeError, KeyError, AttributeError):
            logger.warning(
                "http metric not recorded",
                extra={"route_template": attributes["route"]},
                exc_info=True,
            )
