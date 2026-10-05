"""Liveness and readiness endpoints for every service and worker
(BUILD-PLAN §3.9, design A §1.4, §10.5).

- ``GET /healthz``: liveness. 200 ``{"status": "ok"}`` whenever the event
  loop can answer; it never touches a dependency.
- ``GET /readyz``: readiness. Runs every registered check concurrently,
  each bounded by a 2 s timeout, and answers
  ``{"status": "ok"|"degraded"|"fail", "checks": {name: {"ok", "ms"}}}``.
  A failed *critical* check (Mongo; RabbitMQ for workers; S4's
  insert-only self-test; S2's seed recompute) is ``fail`` -> 503; a failed
  non-critical one (Redis, an OPA decision cache) is ``degraded`` -> 200,
  per design A §10.5. Error details stay in the logs, not the body.

Checks are plain async callables, so a unit registers exactly the
dependencies it uses, typically in its lifespan once clients exist::

    probe = ReadinessProbe()
    app.include_router(build_health_router(probe))
    ...
    probe.add(mongo_check(client))
    probe.add(http_check("opa", f"{settings.opa_url}/health"))
    probe.add(amqp_check(settings.rabbitmq_url))
    probe.add(ReadinessCheck("s3", lambda: s3.head_bucket(Bucket=b)))

S3 has no built-in check because ``fcrm_common`` does not depend on boto:
the service that owns an S3 client passes its own ``head_bucket``
coroutine as above.

Workers: :func:`build_health_asgi_app` returns a dependency-free Starlette
ASGI app serving the same two routes from the same probe.

TODO(worker lane, H1-H4): design A §11.1 says workers serve these through
``faststream.asgi.AsgiFastStream`` on their health port. FastStream is not
a dependency of this package, so its mounting API is not verified here.
The expected wiring is ``AsgiFastStream(broker, asgi_routes=[("/healthz",
app), ("/readyz", app)])`` with ``app = build_health_asgi_app(probe)``
(the Starlette app routes on the full path, so mounting it at both paths
works); if that signature differs in the pinned FastStream version, run
the same app with ``uvicorn`` on the health port as a background task
next to the broker instead — the probe and response contract are the
same either way.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Optional
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pymongo import AsyncMongoClient
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse as StarletteJSONResponse
from starlette.routing import Route

logger = logging.getLogger(__name__)

CHECK_TIMEOUT_S = 2.0
AMQP_PROTOCOL_HEADER = b"AMQP\x00\x00\x09\x01"
AMQP_FRAME_METHOD = 1

CheckFn = Callable[[], Awaitable[Any]]


@dataclass(frozen=True)
class ReadinessCheck:
    """One dependency check: passes unless ``fn`` raises or times out.

    Attributes:
        name: Key in the ``checks`` object (``mongo``, ``rabbitmq``, ...).
        fn: Zero-argument coroutine function doing the real round trip.
        critical: ``True`` -> a failure makes the unit unready (503).
        timeout_s: Per-check budget (design A: 2 s).
    """

    name: str
    fn: CheckFn
    critical: bool = True
    timeout_s: float = CHECK_TIMEOUT_S


class ReadinessProbe:
    """The set of checks ``/readyz`` runs; checks can be added after the
    router is built (clients usually exist only once the lifespan runs).
    """

    def __init__(self, checks: Optional[Iterable[ReadinessCheck]] = None) -> None:
        self._checks: dict[str, ReadinessCheck] = {}
        for check in checks or ():
            self.add(check)

    def add(self, check: ReadinessCheck) -> None:
        """Register ``check``, replacing one with the same name."""
        self._checks[check.name] = check

    async def _run_one(self, check: ReadinessCheck) -> tuple[str, bool, float]:
        """Run one check; ``(name, ok, elapsed_ms)``. Any failure is a
        not-ok result (logged), never an exception out of ``/readyz``.
        """
        started = perf_counter()
        ok = True
        try:
            async with asyncio.timeout(check.timeout_s):
                await check.fn()
        except TimeoutError:
            ok = False
            logger.warning(
                "readiness check timed out", extra={"dependency": check.name}
            )
        except Exception as exc:  # noqa: BLE001 - a probe reports, it never raises
            ok = False
            logger.warning(
                "readiness check failed",
                extra={"dependency": check.name, "error": {"type": type(exc).__name__}},
            )
        return check.name, ok, round((perf_counter() - started) * 1000, 1)

    async def run(self) -> tuple[int, dict[str, Any]]:
        """Run every check concurrently; returns ``(http_status, body)``."""
        checks = list(self._checks.values())
        results = await asyncio.gather(*(self._run_one(c) for c in checks))
        by_name = {c.name: c for c in checks}
        body_checks = {name: {"ok": ok, "ms": ms} for name, ok, ms in results}
        failed = [name for name, ok, _ in results if not ok]
        if any(by_name[name].critical for name in failed):
            status, code = "fail", 503
        elif failed:
            status, code = "degraded", 200
        else:
            status, code = "ok", 200
        return code, {"status": status, "checks": body_checks}


def mongo_check(
    client: AsyncMongoClient[Any], *, critical: bool = True
) -> ReadinessCheck:
    """``admin.command("ping")`` on ``client``."""

    async def ping() -> None:
        await client.admin.command("ping")

    return ReadinessCheck("mongo", ping, critical=critical)


def http_check(
    name: str,
    url: str,
    *,
    critical: bool = True,
    client: Optional[httpx.AsyncClient] = None,
) -> ReadinessCheck:
    """``GET url`` must answer 2xx (OPA ``/health``, identity ``/readyz``)."""

    async def get() -> None:
        if client is not None:
            response = await client.get(url, timeout=CHECK_TIMEOUT_S)
        else:
            async with httpx.AsyncClient(timeout=CHECK_TIMEOUT_S) as own:
                response = await own.get(url)
        response.raise_for_status()

    return ReadinessCheck(name, get, critical=critical)


def tcp_check(
    name: str, host: str, port: int, *, critical: bool = True
) -> ReadinessCheck:
    """A TCP connection to ``host:port`` can be opened (Redis, MinIO...)."""

    async def connect() -> None:
        _, writer = await asyncio.open_connection(host, port)
        writer.close()
        await writer.wait_closed()

    return ReadinessCheck(name, connect, critical=critical)


def amqp_check(
    url: str, *, name: str = "rabbitmq", critical: bool = True
) -> ReadinessCheck:
    """RabbitMQ is up and speaking AMQP 0-9-1: open a TCP connection, send
    the protocol header and require a method frame (``Connection.Start``)
    back. No credentials are sent and no broker library is needed.
    """
    parts = urlsplit(url)
    host = parts.hostname or "localhost"
    port = parts.port or (5671 if parts.scheme == "amqps" else 5672)
    use_tls = parts.scheme == "amqps"

    async def handshake() -> None:
        reader, writer = await asyncio.open_connection(host, port, ssl=use_tls or None)
        try:
            writer.write(AMQP_PROTOCOL_HEADER)
            await writer.drain()
            first = await reader.readexactly(1)
        finally:
            writer.close()
            await writer.wait_closed()
        if first[0] != AMQP_FRAME_METHOD:
            raise ConnectionError(f"unexpected AMQP greeting byte {first[0]}")

    return ReadinessCheck(name, handshake, critical=critical)


def build_health_router(
    probe: Optional[ReadinessProbe] = None,
    checks: Optional[Iterable[ReadinessCheck]] = None,
) -> APIRouter:
    """``/healthz`` and ``/readyz`` for a FastAPI app.

    Args:
        probe: Shared probe (so checks can be added later); a new one is
            created when omitted.
        checks: Initial checks to register on the probe.
    """
    readiness = probe if probe is not None else ReadinessProbe()
    for check in checks or ():
        readiness.add(check)
    router = APIRouter(tags=["health"])

    @router.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/readyz", include_in_schema=False)
    async def readyz() -> JSONResponse:
        code, body = await readiness.run()
        return JSONResponse(status_code=code, content=body)

    return router


def build_health_asgi_app(probe: ReadinessProbe) -> Starlette:
    """A minimal ASGI app with the same two routes, for FastStream workers
    (see the module docstring for mounting).
    """

    async def healthz(_: Request) -> StarletteJSONResponse:
        return StarletteJSONResponse({"status": "ok"})

    async def readyz(_: Request) -> StarletteJSONResponse:
        code, body = await probe.run()
        return StarletteJSONResponse(body, status_code=code)

    return Starlette(routes=[Route("/healthz", healthz), Route("/readyz", readyz)])
