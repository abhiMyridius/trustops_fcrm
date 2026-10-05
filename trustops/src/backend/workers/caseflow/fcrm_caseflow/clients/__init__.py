"""Typed HTTP clients H1's step handlers use to reach its peer services.

One client per peer, each a thin wrapper over a shared
:class:`fcrm_common.http.ServiceClient` (service-account bearer token, trace
headers, retry policy) with one method per route, request and response typed
with the ``fcrm_contracts`` models. The template is
:class:`fcrm_caseflow.audit_client.AuditClient`.
"""

from __future__ import annotations

import logging
from typing import Any, Final
from urllib.parse import quote

from fcrm_common.errors import UpstreamUnavailableError
from fcrm_common.http import Timeouts
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

#: The deadline for one call to a peer service. A step makes several calls
#: and has its own overall deadline (:data:`fcrm_caseflow.steps
#: .DEFAULT_STEP_TIMEOUT_S`); this keeps one hung peer from eating all of it.
CALL_TIMEOUTS: Final = Timeouts(connect=2.0, read=10.0, write=10.0, pool=5.0)


def path_segment(value: str) -> str:
    """``value`` made safe to embed as one URL path segment."""
    return quote(value, safe="")


def parse_body[M: BaseModel](
    model: type[M], body: Any, *, dependency: str, route: str
) -> M:
    """Validate a peer's JSON ``body`` as ``model``.

    A body that does not match the contract is the peer being broken, not the
    caller's mistake, so it surfaces as :class:`UpstreamUnavailableError`
    (retried, then dead-lettered) with the field errors logged, never with the
    body itself.

    Raises:
        fcrm_common.errors.UpstreamUnavailableError: ``body`` is not a valid
            ``model``.
    """
    try:
        return model.model_validate(body)
    except ValidationError as exc:
        logger.error(
            "peer answered with a body that breaks its contract",
            extra={
                "dependency": dependency,
                "route": route,
                "errors": [
                    {"loc": ".".join(str(p) for p in e["loc"]), "type": e["type"]}
                    for e in exc.errors()
                ],
            },
        )
        raise UpstreamUnavailableError(
            f"{dependency} answered {route} with an unexpected body",
            dependency=dependency,
        ) from exc
