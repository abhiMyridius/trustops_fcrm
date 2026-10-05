"""fcrm_common.health: readiness aggregation, the FastAPI router, the
worker ASGI app, and the AMQP/TCP checks against local sockets.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fcrm_common.health import (
    ReadinessCheck,
    ReadinessProbe,
    amqp_check,
    build_health_asgi_app,
    build_health_router,
    http_check,
    tcp_check,
)


async def ok() -> None:
    return None


async def broken() -> None:
    raise ConnectionError("down")


async def hangs() -> None:
    await asyncio.sleep(10)


async def test_probe_all_ok():
    code, body = await ReadinessProbe([ReadinessCheck("mongo", ok)]).run()
    assert code == 200
    assert body["status"] == "ok"
    assert body["checks"]["mongo"]["ok"] is True
    assert isinstance(body["checks"]["mongo"]["ms"], float)


async def test_probe_noncritical_failure_is_degraded_200():
    probe = ReadinessProbe(
        [ReadinessCheck("mongo", ok), ReadinessCheck("redis", broken, critical=False)]
    )
    code, body = await probe.run()
    assert (code, body["status"]) == (200, "degraded")
    assert body["checks"]["redis"]["ok"] is False


async def test_probe_critical_failure_is_fail_503():
    code, body = await ReadinessProbe([ReadinessCheck("mongo", broken)]).run()
    assert (code, body["status"]) == (503, "fail")


async def test_probe_check_timeout_counts_as_failure():
    probe = ReadinessProbe([ReadinessCheck("opa", hangs, timeout_s=0.05)])
    code, body = await probe.run()
    assert code == 503
    assert body["checks"]["opa"]["ok"] is False


def test_router_serves_healthz_and_readyz_with_late_checks():
    probe = ReadinessProbe()
    app = FastAPI()
    app.include_router(build_health_router(probe))
    client = TestClient(app)
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/readyz").json() == {"status": "ok", "checks": {}}
    probe.add(ReadinessCheck("mongo", broken))
    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["status"] == "fail"


async def test_asgi_app_for_workers():
    app = build_health_asgi_app(ReadinessProbe([ReadinessCheck("rabbitmq", ok)]))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://w") as client:
        assert (await client.get("/healthz")).json() == {"status": "ok"}
        ready = await client.get("/readyz")
    assert ready.status_code == 200
    assert ready.json()["checks"]["rabbitmq"]["ok"] is True


async def serve(reply: bytes):
    async def handle(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        await reader.readexactly(8)
        writer.write(reply)
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


async def test_amqp_check_accepts_connection_start_frame():
    server, port = await serve(b"\x01\x00\x00\x00\x00\x00\x10")
    async with server:
        await amqp_check(f"amqp://guest:guest@127.0.0.1:{port}/").fn()


async def test_amqp_check_rejects_non_amqp_greeting():
    server, port = await serve(b"HTTP/1.1 400 Bad Request\r\n")
    async with server:
        with pytest.raises(ConnectionError):
            await amqp_check(f"amqp://127.0.0.1:{port}").fn()


async def test_tcp_check_fails_on_closed_port():
    server, port = await serve(b"")
    server.close()
    await server.wait_closed()
    with pytest.raises(ConnectionRefusedError):
        await tcp_check("redis", "127.0.0.1", port).fn()


async def test_http_check_requires_2xx():
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(500))
    )
    with pytest.raises(httpx.HTTPStatusError):
        await http_check("opa", "http://opa:8181/health", client=client).fn()
    healthy = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200))
    )
    await http_check("opa", "http://opa:8181/health", client=healthy).fn()
