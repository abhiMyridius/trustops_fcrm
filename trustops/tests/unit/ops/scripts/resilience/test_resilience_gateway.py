"""``resilience.gateway``: the persona client and the stream reader, over
``httpx.MockTransport`` (no network).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from resilience.errors import GatewayError
from resilience.gateway import GatewayClient, Reply, StreamReader

BASE = "http://gateway.invalid/api/fcrm"


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> GatewayClient:
    return GatewayClient(
        BASE,
        password="pw",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def _login_ok(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    return httpx.Response(
        200, json={"access_token": f"token-for-{body['username']}", "expires_in": 900}
    )


def test_login_posts_the_persona_username_and_keeps_the_token() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _login_ok(request)

    client = _client(handler)
    assert client.login("analyst") == "token-for-riyer"
    assert client.token("analyst") == "token-for-riyer"
    assert len(seen) == 1, "the token is reused, not fetched again"
    assert json.loads(seen[0].content) == {"username": "riyer", "password": "pw"}


def test_a_token_about_to_expire_is_renewed() -> None:
    logins = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        logins["n"] += 1
        return httpx.Response(
            200, json={"access_token": f"t{logins['n']}", "expires_in": 60}
        )

    client = _client(handler)
    assert client.token("po") == "t1"
    assert client.token("po") == "t2", "60 s of life is inside the renewal margin"


def test_login_without_an_access_token_is_an_error() -> None:
    client = _client(lambda request: httpx.Response(200, json={"nope": 1}))
    with pytest.raises(GatewayError, match="access_token"):
        client.login("po")


def test_a_refused_login_is_an_error_with_the_status() -> None:
    client = _client(lambda request: httpx.Response(429, json={"code": "rate_limited"}))
    with pytest.raises(GatewayError, match="got 429"):
        client.login("po")


def test_calls_send_the_persona_token_as_a_bearer() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/login"):
            return _login_ok(request)
        return httpx.Response(200, json={"auth": request.headers["authorization"]})

    reply = _client(handler).get("po", "/cases")
    assert reply.body == {"auth": "Bearer token-for-pnair"}


def test_an_explicit_token_overrides_the_persona() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"auth": request.headers["authorization"]})

    reply = _client(handler).call("analyst", "GET", "/x", token="issued-earlier")
    assert reply.body == {"auth": "Bearer issued-earlier"}


def test_a_call_with_no_persona_sends_no_authorization() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"has": "authorization" in request.headers})

    assert _client(handler).call(None, "GET", "/x").body == {"has": False}


def test_a_refused_connection_is_a_reply_with_status_zero() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    reply = _client(handler).call(None, "GET", "/x")
    assert reply.status == 0
    assert "ConnectError" in (reply.error or "")


def test_a_problem_json_error_is_recognised() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503,
            content=b'{"status":503,"code":"upstream_unavailable"}',
            headers={"content-type": "application/problem+json"},
        )

    reply = _client(handler).call(None, "GET", "/x")
    assert reply.is_problem
    assert reply.body["code"] == "upstream_unavailable"


def test_a_text_body_is_kept_as_text() -> None:
    reply = _client(lambda r: httpx.Response(502, text="bad gateway")).call(
        None, "GET", "/x"
    )
    assert reply.body == "bad gateway"
    assert not reply.is_problem


def test_require_passes_the_expected_status_and_names_the_unexpected_one() -> None:
    ok = Reply(201, "application/json", {}, 0.1)
    assert ok.require(200, 201) is ok
    with pytest.raises(GatewayError, match=r"expected \(200,\), got 500: oops"):
        Reply(500, "", "oops", 0.1).require(200)


def test_require_shows_the_transport_error_for_status_zero() -> None:
    with pytest.raises(GatewayError, match="ConnectError"):
        Reply(0, "", None, 0.1, "ConnectError: refused").require(200)


def test_pages_follows_the_cursor_to_the_last_page() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/login"):
            return _login_ok(request)
        cursor = request.url.params.get("cursor")
        if cursor is None:
            return httpx.Response(200, json={"items": [1, 2], "next_cursor": "c2"})
        return httpx.Response(200, json={"items": [3], "next_cursor": None})

    assert _client(handler).pages("analyst", "/audit/records/FCRM-1") == [1, 2, 3]


def test_pages_rejects_a_body_that_is_not_a_list_page() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/login"):
            return _login_ok(request)
        return httpx.Response(200, json={"oops": True})

    with pytest.raises(GatewayError, match="did not answer a list page"):
        _client(handler).pages("analyst", "/x")


def test_healthz_reads_the_gateway_root_not_the_api_prefix() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"status": "ok"})

    assert _client(handler).healthz().status == 200
    assert seen == ["http://gateway.invalid/healthz"]


# ---------------------------------------------------------------- streams


def _reader(
    handler: Callable[[httpx.Request], httpx.Response], **kwargs: Any
) -> StreamReader:
    return StreamReader(
        f"{BASE}/cases/FCRM-1/stream",
        params={"access_token": "t"},
        headers=kwargs.get("headers", {}),
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )


def _wait_ended(reader: StreamReader) -> None:
    reader.wait_for(lambda seqs: False, timeout_s=3)


def test_a_stream_reader_collects_frames_until_the_server_closes() -> None:
    body = (
        b": open\n\n"
        b'id: 1\ndata: {"type":"a","seq":1}\n\n'
        b'id: 2\ndata: {"type":"b","seq":2}\n\n'
    )
    reader = _reader(lambda r: httpx.Response(200, content=body))
    reader.start()
    _wait_ended(reader)
    assert (reader.seqs, reader.status, reader.ended) == (
        [1, 2],
        200,
        "closed by the server",
    )


def test_a_stream_reader_reports_a_refused_stream_by_status() -> None:
    reader = _reader(lambda r: httpx.Response(401, json={"code": "unauthorized"}))
    reader.start()
    _wait_ended(reader)
    assert (reader.status, reader.ended, reader.seqs) == (401, "HTTP 401", [])


def test_a_stream_reader_reports_a_dropped_connection() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.RemoteProtocolError("peer closed", request=request)

    reader = _reader(handler)
    reader.start()
    _wait_ended(reader)
    assert (reader.ended or "").startswith("RemoteProtocolError")


def test_a_stream_reader_sends_last_event_id() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("last-event-id", ""))
        return httpx.Response(200, content=b"")

    reader = _reader(handler, headers={"Last-Event-ID": "7"})
    reader.start()
    _wait_ended(reader)
    assert seen == ["7"]


def test_wait_for_returns_true_as_soon_as_the_predicate_holds() -> None:
    body = b'id: 5\ndata: {"seq":5}\n\n'
    reader = _reader(lambda r: httpx.Response(200, content=body))
    reader.start()
    assert reader.wait_for(lambda seqs: 5 in seqs, timeout_s=3)
