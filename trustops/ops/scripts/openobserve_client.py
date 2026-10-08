"""A small client for the OpenObserve the observability stack exports to
(ADR-68, ADR-72), shared by ``openobserve_dashboards.py`` and
``token_report.py``.

Where and as whom, resolved the way compose resolves the collector's settings
(a shell variable wins over ``.env``; ``.env`` is read, never printed):

- the login is ``OPENOBSERVE_USER``/``OPENOBSERVE_PASSWORD`` (a team
  instance), else the bundled container's ``ZO_ROOT_USER_EMAIL``/
  ``ZO_ROOT_USER_PASSWORD``;
- the instance is ``OPENOBSERVE_OTLP_ENDPOINT`` (``<base>/api/<org>``) when
  it names a host the operator can reach, else the bundled container on
  ``127.0.0.1:${OPENOBSERVE_HOST_PORT:-5080}`` in organisation ``default``.
  The compose default (``http://openobserve:5080``) is the container's name
  on the compose network, which only the collector can resolve.
"""

from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from check_obs_env import effective, read_env_file

__all__ = ["OpenObserve", "OpenObserveError", "Row"]

BUNDLED_HOST = "openobserve"
DEFAULT_ORG = "default"
DEFAULT_PORT = "5080"
TIMEOUT_S = 15
ENDPOINT = re.compile(r"^(?P<base>https?://[^/]+)/api/(?P<org>[^/]+)/?$")

Row = tuple[dict[str, str], float]


class OpenObserveError(RuntimeError):
    """OpenObserve could not be reached, refused the login or the request."""


@dataclass(frozen=True)
class OpenObserve:
    """One OpenObserve organisation and the login to use on it."""

    base: str
    org: str
    user: str
    password: str

    @classmethod
    def from_env(
        cls,
        env_file: Path,
        environ: Optional[Mapping[str, str]] = None,
        *,
        url: Optional[str] = None,
    ) -> OpenObserve:
        """Resolve the instance and login (module docstring); ``url``, an
        ``http(s)://host:port`` the operator gave, overrides the instance."""
        raw = read_env_file(env_file)
        env = os.environ if environ is None else environ
        user = effective("OPENOBSERVE_USER", raw, env) or effective(
            "ZO_ROOT_USER_EMAIL", raw, env
        )
        password = effective("OPENOBSERVE_PASSWORD", raw, env) or effective(
            "ZO_ROOT_USER_PASSWORD", raw, env
        )
        if not user or not password:
            raise OpenObserveError(
                "no OpenObserve login: set ZO_ROOT_USER_EMAIL and "
                "ZO_ROOT_USER_PASSWORD (make env), or OPENOBSERVE_USER and "
                "OPENOBSERVE_PASSWORD for a team instance"
            )
        port = effective("OPENOBSERVE_HOST_PORT", raw, env) or DEFAULT_PORT
        base, org = f"http://127.0.0.1:{port}", DEFAULT_ORG
        match = ENDPOINT.match(effective("OPENOBSERVE_OTLP_ENDPOINT", raw, env) or "")
        if match:
            org = match.group("org")
            host = urllib.parse.urlsplit(match.group("base")).hostname
            if host != BUNDLED_HOST:
                base = match.group("base")
        if url:
            base = url
        return cls(base.rstrip("/"), org, user, password)

    def request(
        self, method: str, path: str, body: Optional[Mapping[str, Any]] = None
    ) -> Any:
        """``method`` ``/api/<org><path>`` with ``body`` as JSON; the decoded
        JSON answer. An HTTP error or an unreachable host raises
        :class:`OpenObserveError` naming the status, never the login."""
        token = base64.b64encode(f"{self.user}:{self.password}".encode()).decode()
        req = urllib.request.Request(  # noqa: S310 - operator-configured URL
            f"{self.base}/api/{self.org}{path}",
            method=method,
            data=None if body is None else json.dumps(body).encode(),
            headers={
                "Authorization": f"Basic {token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as response:  # noqa: S310
                return json.load(response)
        except urllib.error.HTTPError as exc:
            hint = " (check the OpenObserve login in .env)" if exc.code == 401 else ""
            raise OpenObserveError(
                f"{method} {path} answered {exc.code}{hint}"
            ) from exc
        except (urllib.error.URLError, OSError) as exc:
            raise OpenObserveError(
                f"cannot reach OpenObserve at {self.base}: {exc} "
                "(is `make up-obs` running?)"
            ) from exc

    def query(self, promql: str) -> list[Row]:
        """Run an instant PromQL query over OpenObserve's metrics; return
        ``(labels, value)`` rows."""
        body = self.request(
            "GET",
            "/prometheus/api/v1/query?" + urllib.parse.urlencode({"query": promql}),
        )
        if body.get("status") != "success":
            raise OpenObserveError(f"query failed: {promql}: {body.get('error')}")
        return [(r["metric"], float(r["value"][1])) for r in body["data"]["result"]]
