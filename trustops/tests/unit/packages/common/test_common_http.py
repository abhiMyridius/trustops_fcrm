"""fcrm_common.http: ServiceClient retries, headers and error mapping
(design A §10.3), against ``httpx.MockTransport``.
"""

from __future__ import annotations

import httpx
import jwt
import pytest
from fcrm_common import errors
from fcrm_common.http import ServiceClient, Timeouts, raise_for_problem
from opentelemetry.sdk.trace import TracerProvider

SECRET = "s" * 40


class Script:
    """Answers requests from a list of responses/exceptions, recording them."""

    def __init__(self, *steps) -> None:
        self.steps = list(steps)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


async def no_sleep(_s: float) -> None:
    return None


def client_for(script: Script, **kwargs) -> ServiceClient:
    kwargs.setdefault("sleep", no_sleep)
    return ServiceClient(
        "http://scoring:8002",
        dependency="scoring",
        caller="caseflow",
        token_secret=SECRET,
        transport=httpx.MockTransport(script),
        **kwargs,
    )


async def test_get_retries_on_503_then_succeeds():
    script = Script(
        httpx.Response(503), httpx.Response(502), httpx.Response(200, json={"ok": 1})
    )
    async with client_for(script) as client:
        assert await client.request_json("GET", "/bands") == {"ok": 1}
    assert len(script.requests) == 3


async def test_a_success_status_with_a_body_that_is_not_json_is_a_503_not_a_500():
    """A proxy's HTML error page behind a 200 must reach the caller as a
    problem naming the peer, not as a ``JSONDecodeError`` (a bare 500).
    """
    script = Script(
        httpx.Response(200, text="<html>bad gateway</html>"),
    )
    async with client_for(script) as client:
        with pytest.raises(errors.UpstreamUnavailableError) as raised:
            await client.request_json("GET", "/bands")
    assert raised.value.extra["dependency"] == "scoring"
    assert raised.value.extra["upstream_status"] == 200


async def test_backoff_follows_design_schedule():
    slept: list[float] = []

    async def record(s: float) -> None:
        slept.append(s)

    script = Script(httpx.Response(503), httpx.Response(503), httpx.Response(503))
    async with client_for(script, sleep=record) as client:
        response = await client.get("/bands")
    assert response.status_code == 503
    assert slept == [0.2, 0.6]
    assert len(script.requests) == 3


async def test_post_without_idempotency_key_is_not_retried_on_503():
    script = Script(httpx.Response(503))
    async with client_for(script) as client:
        response = await client.post("/score", json={})
    assert response.status_code == 503
    assert len(script.requests) == 1


async def test_post_with_idempotency_key_retries_with_same_key():
    script = Script(httpx.Response(504), httpx.Response(201, json={}))
    async with client_for(script) as client:
        response = await client.post("/packs", json={}, idempotency_key="idem-1")
    assert response.status_code == 201
    assert [r.headers["idempotency-key"] for r in script.requests] == [
        "idem-1",
        "idem-1",
    ]


async def test_500_is_not_retried():
    script = Script(httpx.Response(500))
    async with client_for(script) as client:
        assert (await client.get("/x")).status_code == 500
    assert len(script.requests) == 1


async def test_connect_error_retried_even_for_post_then_raises_unavailable():
    req = httpx.Request("POST", "http://scoring:8002/score")
    script = Script(*(httpx.ConnectError("refused", request=req) for _ in range(3)))
    async with client_for(script) as client:
        with pytest.raises(errors.UpstreamUnavailableError) as info:
            await client.post("/score", json={})
    assert len(script.requests) == 3
    assert info.value.extra["dependency"] == "scoring"


async def test_read_timeout_raises_upstream_timeout_without_retry():
    req = httpx.Request("GET", "http://scoring:8002/x")
    script = Script(httpx.ReadTimeout("slow", request=req))
    async with client_for(script) as client:
        with pytest.raises(errors.UpstreamTimeoutError):
            await client.get("/x")
    assert len(script.requests) == 1


async def test_requests_carry_verifiable_service_token():
    script = Script(httpx.Response(200, json={}))
    async with client_for(script) as client:
        await client.get("/x")
    auth = script.requests[0].headers["authorization"]
    claims = jwt.decode(
        auth.removeprefix("Bearer "),
        SECRET,
        algorithms=["HS256"],
        audience="fcrm",
        issuer="fcrm-service",
    )
    assert claims["roles"] == ["fcrm_service"]
    assert claims["typ"] == "service"
    assert claims["sub"] == "svc:caseflow"
    assert claims["exp"] - claims["iat"] == 300


async def test_service_token_is_reused_between_requests():
    script = Script(httpx.Response(200), httpx.Response(200))
    async with client_for(script) as client:
        await client.get("/a")
        await client.get("/b")
    first, second = (r.headers["authorization"] for r in script.requests)
    assert first == second


async def test_custom_token_provider_overrides_hs256():
    async def provider() -> str:
        return "identity-minted"

    script = Script(httpx.Response(200))
    async with client_for(script, token_provider=provider) as client:
        await client.get("/a")
    assert script.requests[0].headers["authorization"] == "Bearer identity-minted"


async def test_traceparent_propagated_from_current_span():
    tracer = TracerProvider().get_tracer("t")
    script = Script(httpx.Response(200))
    async with client_for(script) as client:
        with tracer.start_as_current_span("caller") as span:
            await client.get("/a")
            trace_id = format(span.get_span_context().trace_id, "032x")
    assert script.requests[0].headers["traceparent"].split("-")[1] == trace_id


def test_timeouts_default_to_design_values():
    t = Timeouts().to_httpx()
    assert (t.connect, t.read) == (2.0, 10.0)


@pytest.mark.parametrize(
    ("status", "cls"),
    [
        (404, errors.NotFoundError),
        (409, errors.ConflictError),
        (422, errors.ValidationError),
        (403, errors.ForbiddenError),
        (401, errors.UpstreamUnavailableError),
        (429, errors.UpstreamUnavailableError),
        (500, errors.UpstreamUnavailableError),
        (503, errors.UpstreamUnavailableError),
        (504, errors.UpstreamTimeoutError),
    ],
)
def test_raise_for_problem_maps_status(status, cls):
    response = httpx.Response(status, json={"detail": "case FCRM-2026-0999 not found"})
    with pytest.raises(cls) as info:
        raise_for_problem(response, "case")
    assert type(info.value) is cls
    assert info.value.detail == "case FCRM-2026-0999 not found"
    assert info.value.extra == {"dependency": "case", "upstream_status": status}


def test_raise_for_problem_passes_success():
    raise_for_problem(httpx.Response(204), "case")
