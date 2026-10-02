"""fcrm_common.healthcheck: the container HEALTHCHECK CLI."""

from __future__ import annotations

import httpx
from fcrm_common import healthcheck


def test_main_exit_codes(monkeypatch):
    responses = {
        "http://ok/readyz": httpx.Response(200),
        "http://fail/readyz": httpx.Response(503),
    }

    def fake_get(url: str, timeout: float) -> httpx.Response:
        if url not in responses:
            raise httpx.ConnectError("refused")
        return responses[url]

    monkeypatch.setattr(healthcheck.httpx, "get", fake_get)
    assert healthcheck.main(["http://ok/readyz"]) == 0
    assert healthcheck.main(["http://fail/readyz"]) == 1
    assert healthcheck.main(["http://down/readyz"]) == 1
    assert healthcheck.main([]) == 2
