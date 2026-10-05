"""What every HTTP-backed tool shares: the peers, the limits, the cache lifetimes
and the one call that reads a peer and maps its failures.

Failure mapping (so a caller never sees a bare ``httpx``/``botocore``/JSON
error):

* peer 404 -> :class:`~fcrm_tools.errors.UnknownEntityError`;
* peer 400/422 -> :class:`~fcrm_tools.errors.ToolInputError`;
* a peer that answers with something that is not the expected shape ->
  :class:`~fcrm_common.errors.UpstreamUnavailableError`;
* timeouts and connection failures -> ``UpstreamTimeoutError`` /
  ``UpstreamUnavailableError`` from :class:`~fcrm_common.http.ServiceClient`
  (2 s connect, 10 s read, two retries for GETs), which H2 treats as
  transient (the job is redelivered), unlike a ``ToolError`` (terminal).

:mod:`fcrm_tools.http_backed` re-exports what its own callers import.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Final, Optional
from urllib.parse import quote

from fcrm_common.errors import NotFoundError, UpstreamUnavailableError
from fcrm_common.errors import ValidationError as FcrmValidationError
from fcrm_common.http import ServiceClient, Timeouts
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from fcrm_tools.errors import ToolInputError, UnknownEntityError
from fcrm_tools.seed_backed import MAX_TOP_K

logger = logging.getLogger(__name__)

CASE_PEER: Final = "case"
POLICY_PEER: Final = "policy"
AUDIT_PEER: Final = "audit"
TOOL_TIMEOUTS: Final = Timeouts(connect=2.0, read=10.0)
"""Per-request limits for every tool call (design B §1.3: ``timeout_s``)."""
TOOL_RETRIES: Final = 2
"""Extra attempts for an idempotent GET on connection failure or 502-504."""

FIELD_SET_TTL_S: Final = 300
"""A pinned field-set, control-inventory or corpus version never changes once
published, so what a tool read for it can be reused for this long."""
ALIAS_TTL_S: Final = 30
"""``latest``/``current``/``fixture`` can move when a new list is activated."""

PAGE_SIZE: Final = 100
"""Page size a tool asks a paged peer for (S4's maximum is 200)."""
MAX_PAGES: Final = 5
"""Most pages a tool reads in one call: ``PAGE_SIZE * MAX_PAGES`` records at
most, then it refuses rather than returning a silently truncated answer."""


async def fetch_model[M: BaseModel](
    client: ServiceClient,
    path: str,
    model: type[M],
    *,
    tool: str,
    what: str,
    method: str = "GET",
    params: Optional[dict[str, Any]] = None,
    body: Optional[dict[str, Any]] = None,
) -> M:
    """Call a peer and validate its answer as ``model``.

    Args:
        client: The peer's client.
        path: Path relative to the peer's base URL.
        model: The expected response model.
        tool: The calling tool's name, for logs.
        what: What was asked for, for error messages (``"document 'doc_1'"``).
        method: ``GET`` (retried) or ``POST``.
        params: Query parameters.
        body: JSON request body.

    Raises:
        UnknownEntityError: The peer answered 404.
        ToolInputError: The peer answered 400 or 422.
        UpstreamUnavailableError: The peer answered something that is not
            JSON or not ``model``; or the client could not reach it.
        UpstreamTimeoutError: The peer did not answer in time.
        fcrm_common.errors.FcrmError: Any other error status, unchanged
            (403 for refused service credentials, 409, 5xx).
    """
    try:
        payload = await client.request_json(method, path, params=params, json=body)
    except NotFoundError as exc:
        logger.info(
            "tool lookup found nothing",
            extra={"tool": tool, "dependency": client.dependency},
        )
        raise UnknownEntityError(f"{what} does not exist") from exc
    except FcrmValidationError as exc:
        logger.info(
            "tool request refused by peer",
            extra={"tool": tool, "dependency": client.dependency},
        )
        raise ToolInputError(f"{what}: {exc.detail}") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.error(
            "peer answered with a body that is not JSON",
            extra={"tool": tool, "dependency": client.dependency},
        )
        raise UpstreamUnavailableError(
            f"{client.dependency} returned a body that is not JSON.",
            dependency=client.dependency,
        ) from exc
    return validated(model, payload, tool=tool, dependency=client.dependency)


def validated[M: BaseModel](
    model: type[M], payload: Any, *, tool: str, dependency: str
) -> M:
    """``payload`` as ``model``, or :class:`UpstreamUnavailableError` (the peer
    answered, but not with the contract it publishes).
    """
    try:
        return model.model_validate(payload)
    except PydanticValidationError as exc:
        logger.error(
            "peer answered with an unexpected shape",
            extra={"tool": tool, "dependency": dependency, "errors": exc.error_count()},
        )
        raise UpstreamUnavailableError(
            f"{dependency} returned an invalid {model.__name__}.",
            dependency=dependency,
        ) from exc


def segment(value: str) -> str:
    """``value`` as one URL path segment (nothing in it can add a segment)."""
    return quote(value, safe="")


def check_top_k(top_k: int) -> None:
    """Refuse a ``top_k`` outside ``1..MAX_TOP_K``.

    Raises:
        ToolInputError: ``top_k`` is out of range.
    """
    if not 1 <= top_k <= MAX_TOP_K:
        msg = f"top_k must be 1..{MAX_TOP_K}, got {top_k}"
        raise ToolInputError(msg)
