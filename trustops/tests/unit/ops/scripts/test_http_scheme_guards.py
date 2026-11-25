"""The ops probes talk http(s) only: never file: or ftp: (Opengrep SSRF rule)."""

from __future__ import annotations

import pytest
import wait_for_ready
from openobserve_client import OpenObserve, OpenObserveError


@pytest.mark.parametrize(
    "url", ["file:///etc/passwd", "ftp://example.test/x", "gopher://x", ""]
)
def test_a_probe_refuses_a_url_that_is_not_http(url: str) -> None:
    assert wait_for_ready.url_ok(url) is False


@pytest.mark.parametrize("base", ["file:///etc", "ftp://example.test"])
def test_the_openobserve_client_refuses_a_base_that_is_not_http(base: str) -> None:
    client = OpenObserve(base, "default", "user", "password")

    with pytest.raises(OpenObserveError, match="http"):
        client.request("GET", "/x")
