"""The third fence on the PO surface (rule 3, D-62): a response guard.

The first fence is the contract (``fcrm_contracts.api.case_po`` has no field that
could carry a score, band, rating or harness identifier); the second is the
router's imports (``routes_po`` reaches nothing that holds one). This one does
not trust either. :class:`PoGuardedRoute` wraps every route of the PO router:
it takes the JSON a handler is about to send to a product owner and runs it
through ``fcrm_guardrails.rules.r3_po_no_restricted.check``. A restricted key or
term anywhere in the body turns the answer into **500 ``rule_violation:R3``**
(fail closed: nothing of the original body is sent), is logged with the case id,
route and the path of each hit (never the restricted value), and counted in
``fcrm_rule_violations_total{rule="R3", route=...}``.

Only successful (2xx) JSON bodies are scanned: an error body carries a message,
not data, and a refusal that names the reserved word it refused must stay
readable. The live stream and the activity feed are fenced by
``fcrm_case.domain.activity`` (the same lists), reused rather than forked.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Coroutine
from functools import lru_cache
from typing import Any, Final

from fastapi import Request, Response
from fastapi.routing import APIRoute
from fcrm_common.errors import problem_response, problem_type
from fcrm_common.tracing import current_trace_ids
from fcrm_contracts.common import Problem
from fcrm_contracts.enums import Role
from fcrm_guardrails.rules.r3_po_no_restricted import RULE_ID as R3_RULE_ID
from fcrm_guardrails.rules.r3_po_no_restricted import check
from opentelemetry import metrics
from opentelemetry.metrics import Counter, Meter

from fcrm_case.domain.activity import audience_of

logger = logging.getLogger(__name__)

COUNTER_NAME: Final = "fcrm_rule_violations_total"
METER_NAME: Final = "fcrm"
#: The ``detail`` a blocked response carries: the rule, nothing of the body.
VIOLATION_DETAIL: Final = (
    "rule_violation:R3 a response for the product owner was withheld"
)


def build_counter(meter: Meter) -> Counter:
    """The violations counter on ``meter``."""
    return meter.create_counter(
        COUNTER_NAME,
        description="Responses withheld because they broke a rule (labelled by rule).",
    )


@lru_cache(maxsize=1)
def violation_counter() -> Counter:
    """The process-wide counter on the global meter provider (the proxy binds
    to the real provider once ``init_otel`` installs it).
    """
    return build_counter(metrics.get_meter(METER_NAME))


def _route_template(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else request.url.path


def _blocked(request: Request) -> Response:
    trace_id = current_trace_ids()[0]
    return problem_response(
        Problem(
            type=problem_type("rule_violation"),
            title="Rule violation",
            status=500,
            detail=VIOLATION_DETAIL,
            instance=request.url.path,
            trace_id=trace_id,
        )
    )


def guard_response(request: Request, response: Response) -> Response:
    """``response`` if it is clean for a product owner, else the 500 that
    replaces it. A caller who is staff (the feed route is shared) is not this
    guard's concern; a request with no principal at all is scanned (fail closed).
    """
    if not 200 <= response.status_code < 300:
        return response
    principal = getattr(request.state, "principal", None)
    if principal is not None and audience_of(principal) != "po":
        return response
    body = getattr(response, "body", None)
    if not isinstance(body, bytes | bytearray) or not body:
        return response
    try:
        payload = json.loads(body)
    except ValueError:
        logger.error(
            "po response is not JSON; withheld",
            extra={"route": _route_template(request)},
        )
        return _blocked(request)
    verdict = check(payload, Role.PO)
    if verdict.ok:
        return response
    template = _route_template(request)
    logger.error(
        "po response withheld by rule 3",
        extra={
            "rule_id": R3_RULE_ID,
            "route": template,
            "case_id": request.path_params.get("case_id"),
            "hits": verdict.detail,
        },
    )
    violation_counter().add(1, {"rule": "R3", "route": template})
    return _blocked(request)


class PoGuardedRoute(APIRoute):
    """An :class:`~fastapi.routing.APIRoute` whose response passes
    :func:`guard_response` on the way out. Give it to the PO router:
    ``APIRouter(route_class=PoGuardedRoute)``.
    """

    def get_route_handler(
        self,
    ) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        """The route's handler, with the guard after it."""
        original = super().get_route_handler()

        async def guarded(request: Request) -> Response:
            return guard_response(request, await original(request))

        return guarded
