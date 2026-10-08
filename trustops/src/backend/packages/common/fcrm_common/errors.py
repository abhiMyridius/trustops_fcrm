"""The single exception hierarchy every unit raises and catches, and the
FastAPI handlers that turn it into RFC 9457 problem+json (BUILD-PLAN §3.9,
design A §10.1-10.2).

Services raise these, never ``HTTPException``: the handler installed by
:func:`install_error_handlers` maps every subclass, FastAPI's own
``RequestValidationError``/``HTTPException``, the PyMongo errors that mean
"the database is down" or "duplicate key", and any unexpected exception to
one :class:`fcrm_contracts.common.Problem` body with
``Content-Type: application/problem+json``. Bodies never carry stack
traces, tokens or secrets; the ``trace_id`` links the response to its
trace in OpenObserve.

Naming: the classes carry an ``Error`` suffix (``ValidationError``, not
design A's ``ValidationFailed``) so they read as exceptions and never
collide with ``pydantic.ValidationError`` by accident — import this one as
``from fcrm_common.errors import ValidationError as FcrmValidationError``
wherever pydantic's is also in scope. Each class keeps design A's machine
``code`` (``validation_failed``, ``rule_violation``, ...) as the last path
segment of its problem ``type`` URI.

``fcrm_contracts.common.Problem`` is ``extra="forbid"`` and has no
extension members, so the per-error extras design A §10.2 shows
(``code``, ``rule_id``, ``gate_type``, ``dependency``...) are kept on the
exception's :attr:`FcrmError.extra`, logged with the error, and encoded in
the ``type`` URI and ``detail`` — not emitted as top-level body members.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any, ClassVar, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fcrm_contracts.common import Problem
from pymongo.errors import ConnectionFailure, DuplicateKeyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from fcrm_common.tracing import current_trace_ids

logger = logging.getLogger(__name__)

PROBLEM_TYPE_BASE = "https://trustops.local/errors/"
PROBLEM_CONTENT_TYPE = "application/problem+json"


class FcrmError(Exception):
    """Base of the hierarchy; raise a subclass, never this class directly.

    Args:
        detail: Human-readable, specific explanation of this occurrence.
            Never include secrets, tokens, document text or stack traces.
        errors: Optional field-level problems, each a ``{"loc", "msg"}``
            style mapping of strings (``Problem.errors``).
        **extra: Machine-readable context (``rule_id``, ``dependency``,
            ``gate_type``...). Logged with the error; not part of the body.
    """

    status_code: ClassVar[int] = 500
    code: ClassVar[str] = "internal_error"
    title: ClassVar[str] = "Internal error"

    def __init__(
        self,
        detail: Optional[str] = None,
        *,
        errors: Optional[Sequence[Mapping[str, str]]] = None,
        **extra: Any,
    ) -> None:
        self.detail = detail if detail is not None else self.title
        self.errors: Optional[list[dict[str, str]]] = (
            [dict(e) for e in errors] if errors is not None else None
        )
        self.extra: dict[str, Any] = dict(extra)
        super().__init__(self.detail)

    @property
    def type_uri(self) -> str:
        """The RFC 9457 ``type`` URI, ``https://trustops.local/errors/<code>``."""
        return problem_type(self.code)

    def to_problem(
        self, instance: Optional[str] = None, trace_id: Optional[str] = None
    ) -> Problem:
        """Build the problem+json body for this error."""
        return Problem(
            type=self.type_uri,
            title=self.title,
            status=self.status_code,
            detail=self.detail,
            instance=instance,
            trace_id=trace_id,
            errors=self.errors,
        )


class NotFoundError(FcrmError):
    status_code = 404
    code = "not_found"
    title = "Not found"


class NoRunRecordedError(NotFoundError):
    """The case exists but no assessment run has recorded anything for the
    thing asked for yet (the harness monitor of a seeded case, or of a case
    whose first upload has not opened a run). It is still a 404, so a caller
    that only knows ``NotFoundError`` keeps working, but its ``type`` ends in
    ``/no_run_recorded`` so the web can show "no run yet" as information
    instead of "this case is not in your view". Raise it only after the case
    itself was found: a missing or not-yours case stays the plain
    :class:`NotFoundError`.
    """

    code = "no_run_recorded"
    title = "No run recorded"


class ValidationError(FcrmError):
    """A request is well-formed JSON but violates a domain rule on its
    fields (design A's ``ValidationFailed``). Distinct from
    ``pydantic.ValidationError``.
    """

    status_code = 422
    code = "validation_failed"
    title = "Validation failed"


class ConflictError(FcrmError):
    """The request conflicts with current state: a write to a closed case,
    a duplicate vote, a round already open, an idempotency replay whose body
    differs from the original.
    """

    status_code = 409
    code = "conflict"
    title = "Conflict"


class RuleViolationError(ConflictError):
    """A Guard 5 rule refused the action; pass ``rule_id="R6"`` etc."""

    code = "rule_violation"
    title = "Rule violation"


class GateRequiredError(ConflictError):
    """The action is blocked until a human decides a gate; pass
    ``gate_type``.
    """

    code = "gate_required"
    title = "Gate required"


class IllegalTransitionError(ConflictError):
    """Caseflow refused a state change; pass ``from_state``/``to_state``."""

    code = "illegal_transition"
    title = "Illegal transition"


class UnauthorizedError(FcrmError):
    status_code = 401
    code = "unauthorized"
    title = "Unauthorized"


class ForbiddenError(FcrmError):
    status_code = 403
    code = "forbidden"
    title = "Forbidden"


class BudgetExhaustedError(FcrmError):
    """Guard 3: the case's token budget cannot cover the request (design A
    §3.3 ``BudgetExceeded``); pass ``cap``/``used``.
    """

    status_code = 429
    code = "budget_exceeded"
    title = "Budget exceeded"


class GuardRejectedError(FcrmError):
    """Guard 1 or Guard 4 rejected content; pass ``guard`` and findings."""

    status_code = 422
    code = "guard_rejected"
    title = "Guard rejected"


class ChainBrokenError(FcrmError):
    """S4's hash-chain verification found a break; pass
    ``first_broken_seq``.
    """

    status_code = 500
    code = "chain_broken"
    title = "Audit chain broken"


class UpstreamUnavailableError(FcrmError):
    """A dependency (mongo, rabbitmq, bedrock, s3, opa, identity or a peer
    service) is unreachable or answered 5xx; pass ``dependency``.
    """

    status_code = 503
    code = "upstream_unavailable"
    title = "Upstream unavailable"


class AuthzUnavailableError(UpstreamUnavailableError):
    """OPA could not be consulted. Authorization fails closed (503), never
    open.
    """

    code = "authz_unavailable"
    title = "Authorization unavailable"


class UpstreamTimeoutError(FcrmError):
    status_code = 504
    code = "upstream_timeout"
    title = "Upstream timeout"


_STATUS_TITLES: dict[int, tuple[str, str]] = {
    400: ("bad_request", "Bad request"),
    401: ("unauthorized", "Unauthorized"),
    403: ("forbidden", "Forbidden"),
    404: ("not_found", "Not found"),
    405: ("method_not_allowed", "Method not allowed"),
    409: ("conflict", "Conflict"),
    413: ("payload_too_large", "Payload too large"),
    415: ("unsupported_media_type", "Unsupported media type"),
    422: ("validation_failed", "Validation failed"),
    429: ("too_many_requests", "Too many requests"),
    503: ("upstream_unavailable", "Upstream unavailable"),
}


def problem_type(code: str) -> str:
    """The problem ``type`` URI for a machine code."""
    return f"{PROBLEM_TYPE_BASE}{code}"


def problem_response(
    problem: Problem, headers: Optional[Mapping[str, str]] = None
) -> JSONResponse:
    """Serialise a :class:`Problem` as ``application/problem+json``."""
    return JSONResponse(
        status_code=problem.status,
        content=problem.model_dump(mode="json", exclude_none=True),
        media_type=PROBLEM_CONTENT_TYPE,
        headers=dict(headers) if headers else None,
    )


def _status_problem(status: int, detail: Optional[str], instance: str) -> Problem:
    """A problem body for a bare status code (FastAPI/Starlette errors)."""
    fallback = (
        ("internal_error", "Internal error") if status >= 500 else ("error", "Error")
    )
    code, title = _STATUS_TITLES.get(status, fallback)
    return Problem(
        type=problem_type(code),
        title=title,
        status=status,
        detail=detail,
        instance=instance,
        trace_id=current_trace_ids()[0],
    )


def _validation_items(errors: Sequence[Any]) -> list[dict[str, str]]:
    """Flatten FastAPI/pydantic error dicts into ``Problem.errors`` items.
    Only ``loc``, ``msg`` and ``type`` are kept: pydantic's ``input`` echo
    could carry a secret the caller sent and is deliberately dropped.
    """
    items: list[dict[str, str]] = []
    for err in errors:
        if not isinstance(err, Mapping):
            items.append({"loc": "", "msg": str(err), "type": "error"})
            continue
        loc = err.get("loc", ())
        loc_text = ".".join(str(p) for p in loc) if isinstance(loc, Sequence) else ""
        items.append(
            {
                "loc": loc_text,
                "msg": str(err.get("msg", "")),
                "type": str(err.get("type", "")),
            }
        )
    return items


def _headers_for(status: int) -> Optional[dict[str, str]]:
    """Extra response headers a status code calls for."""
    if status == 401:
        return {"WWW-Authenticate": 'Bearer realm="fcrm"'}
    if status in (503, 429):
        return {"Retry-After": "5"}
    return None


async def _handle_fcrm_error(request: Request, exc: Exception) -> JSONResponse:
    """Map an :class:`FcrmError` to its problem body and log it."""
    if not isinstance(exc, FcrmError):  # pragma: no cover - registration guard
        return await _handle_unexpected(request, exc)
    level = logging.ERROR if exc.status_code >= 500 else logging.INFO
    logger.log(
        level,
        "request failed: %s",
        exc.code,
        extra={"error": {"code": exc.code, "type": type(exc).__name__}, **exc.extra},
    )
    problem = exc.to_problem(request.url.path, current_trace_ids()[0])
    return problem_response(problem, _headers_for(exc.status_code))


async def _handle_request_validation(request: Request, exc: Exception) -> JSONResponse:
    """Map FastAPI's body/query validation failure to a 422 problem."""
    raw = exc.errors() if isinstance(exc, RequestValidationError) else []
    problem = ValidationError(
        "The request did not match the expected schema.",
        errors=_validation_items(raw),
    ).to_problem(request.url.path, current_trace_ids()[0])
    return problem_response(problem)


async def _handle_http_exception(request: Request, exc: Exception) -> JSONResponse:
    """Map Starlette/FastAPI ``HTTPException`` (404 for unknown routes, 405,
    or a stray ``raise HTTPException``) to the same problem shape.
    """
    if not isinstance(exc, StarletteHTTPException):  # pragma: no cover
        return await _handle_unexpected(request, exc)
    detail = exc.detail if isinstance(exc.detail, str) else None
    problem = _status_problem(exc.status_code, detail, request.url.path)
    headers = dict(exc.headers) if exc.headers else _headers_for(exc.status_code)
    return problem_response(problem, headers)


async def _handle_duplicate_key(request: Request, exc: Exception) -> JSONResponse:
    """A unique-index violation that escaped the domain layer is a 409."""
    logger.info("duplicate key on %s", request.url.path)
    problem = ConflictError("The resource already exists.").to_problem(
        request.url.path, current_trace_ids()[0]
    )
    return problem_response(problem)


async def _handle_mongo_down(request: Request, exc: Exception) -> JSONResponse:
    """Mongo unreachable (server selection timeout, network) is a 503."""
    logger.error(
        "mongo unavailable: %s",
        type(exc).__name__,
        extra={"dependency": "mongo"},
    )
    problem = UpstreamUnavailableError(
        "The database is unavailable.", dependency="mongo"
    ).to_problem(request.url.path, current_trace_ids()[0])
    return problem_response(problem, _headers_for(503))


async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
    """Last resort: log with the stack trace, answer a bare 500 problem."""
    logger.exception(
        "unhandled error on %s",
        request.url.path,
        extra={"error": {"code": "internal_error", "type": type(exc).__name__}},
        exc_info=exc,
    )
    problem = _status_problem(500, "An unexpected error occurred.", request.url.path)
    return problem_response(problem)


def install_error_handlers(app: FastAPI) -> None:
    """Register every problem+json handler on ``app``.

    Call once in each service's ``create_app()``. Starlette resolves a
    handler by walking the raised exception's MRO, so every
    :class:`FcrmError` subclass routes to one handler and the bare
    ``Exception`` handler only sees what nothing else claimed (Starlette
    re-raises that one after responding so the server still logs it).
    """
    app.add_exception_handler(FcrmError, _handle_fcrm_error)
    app.add_exception_handler(RequestValidationError, _handle_request_validation)
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
    app.add_exception_handler(DuplicateKeyError, _handle_duplicate_key)
    app.add_exception_handler(ConnectionFailure, _handle_mongo_down)
    app.add_exception_handler(Exception, _handle_unexpected)
