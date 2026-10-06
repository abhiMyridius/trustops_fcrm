"""``ops/scripts/seed_verify.py``: the checks over answers the gateway would give,
with no stack (an ``httpx.MockTransport`` plays the gateway)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[4]
_SPEC = importlib.util.spec_from_file_location(
    "seed_verify", ROOT / "ops" / "scripts" / "seed_verify.py"
)
assert _SPEC is not None
assert _SPEC.loader is not None
seed_verify = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(seed_verify)

LOGIN = "/api/fcrm/auth/login"


def _client(handler: Any) -> Any:
    client = seed_verify.Client("http://gateway.test")
    client._http = httpx.Client(
        base_url="http://gateway.test", transport=httpx.MockTransport(handler)
    )
    return client


def test_a_rate_limited_login_waits_and_tries_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Playwright's logins and this script share the gateway's ten a minute."""
    answers = [429, 429, 200]
    waits: list[float] = []
    monkeypatch.setattr(seed_verify.time, "sleep", waits.append)

    def handler(request: httpx.Request) -> httpx.Response:
        status = answers.pop(0)
        body = {"access_token": "t"} if status == 200 else {}
        return httpx.Response(status, json=body)

    client = _client(handler)
    client.login("riyer", "pw")

    assert waits == [seed_verify.LOGIN_WAIT_S] * 2
    assert client._tokens == {"riyer": "t"}


def test_a_login_that_stays_rate_limited_gives_up_with_the_gateways_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(seed_verify.time, "sleep", lambda _s: None)
    client = _client(lambda request: httpx.Response(429, json={}))

    with pytest.raises(httpx.HTTPStatusError):
        client.login("riyer", "pw")


def test_a_broken_chain_is_a_problem_naming_the_record() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "case_id": "FCRM-2026-0118",
                "record_count": 9,
                "ok": False,
                "first_break_seq": 4,
            },
        )

    client = _client(handler)
    client._tokens["riyer"] = "t"

    assert seed_verify.check_chain(client, "FCRM-2026-0118") == [
        "FCRM-2026-0118's chain breaks at record 4"
    ]


def test_a_case_with_no_records_is_not_a_verified_chain_except_the_demo_case() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"case_id": "x", "record_count": 0, "ok": True})

    client = _client(handler)
    client._tokens["riyer"] = "t"

    assert seed_verify.check_chain(client, "FCRM-2026-0118") == [
        "FCRM-2026-0118 has no audit records to verify"
    ]
    assert seed_verify.check_chain(client, seed_verify.DEMO_CASE) == []
