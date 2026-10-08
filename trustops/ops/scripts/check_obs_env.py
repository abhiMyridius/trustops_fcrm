#!/usr/bin/env python3
"""Pre-flight for ``make up-obs``: stop early, with the fix, on an ``.env``
that would start the observability stack wrongly (ADR-66, ADR-68).

Each check guards a failure that is otherwise silent or confusing:

- no OpenObserve root login (an ``.env`` older than ADR-66; ``make env`` never
  overwrites): the bundled OpenObserve exits on start;
- an OpenObserve password with an unquoted ``$`` or `` #``: Docker Compose
  reads ``$x`` as a variable and `` #`` as a comment, so the password is cut
  there without a warning and every export answers 401;
- ``OPENOBSERVE_USER`` set without ``OPENOBSERVE_PASSWORD``: compose falls
  back to the bundled container's password, the wrong one for a team instance;
- ``OPENOBSERVE_OTLP_ENDPOINT`` without the ``/api/<organisation>`` path: the
  collector posts to ``<host>/v1/...``, which OpenObserve answers 404.

A variable exported in the shell wins over ``.env``, as it does in compose.
The script reads ``.env`` only to check it and never prints a value.

Usage::

    python3 ops/scripts/check_obs_env.py [--env-file .env]
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Optional

logger = logging.getLogger("check_obs_env")

ASSIGNMENT = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")
ROOT_LOGIN = ("ZO_ROOT_USER_EMAIL", "ZO_ROOT_USER_PASSWORD")
PASSWORDS = ("ZO_ROOT_USER_PASSWORD", "OPENOBSERVE_PASSWORD")
ROOT_LOGIN_FIX = (
    "Add the lines ZO_ROOT_USER_EMAIL=admin@trustops.example and "
    "ZO_ROOT_USER_PASSWORD=Oo-<openssl rand -hex 14> to .env (OpenObserve needs "
    "upper and lower case, a digit and a symbol), then run make up-obs again."
)


def read_env_file(path: Path) -> dict[str, str]:
    """Return the raw right-hand side of each ``NAME=value`` line (last wins).

    Raw means as written, quotes included, because the quoting is what the
    checks look at. A missing file is an empty mapping.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    raw: dict[str, str] = {}
    for line in text.splitlines():
        match = ASSIGNMENT.match(line.strip())
        if match:
            raw[match.group(1)] = match.group(2)
    return raw


def _is_quoted(value: str) -> bool:
    return len(value) >= 2 and value[0] == value[-1] and value[0] in "'\""


def effective(
    name: str, raw: Mapping[str, str], environ: Mapping[str, str]
) -> Optional[str]:
    """The value compose would use, roughly: the shell first, then ``.env``;
    an empty value counts as unset (the compose files use ``:-``)."""
    value = environ.get(name) or raw.get(name, "")
    if _is_quoted(value):
        value = value[1:-1]
    return value or None


def problems(raw: Mapping[str, str], environ: Mapping[str, str]) -> list[str]:
    """Return one message per problem, each naming the fix; empty when fine."""
    found: list[str] = []
    for name in ROOT_LOGIN:
        if effective(name, raw, environ) is None:
            found.append(
                f"{name} is not set (OpenObserve's root login). {ROOT_LOGIN_FIX}"
            )

    for name in PASSWORDS:
        value = raw.get(name, "")
        if name in environ or not value or _is_quoted(value):
            continue
        if "$" in value or " #" in value:
            found.append(
                f"{name} in .env has an unquoted '$' or ' #'; Docker Compose "
                "would cut the password there. Put the value in single quotes: "
                f"{name}='...'"
            )

    if effective("OPENOBSERVE_USER", raw, environ) and not effective(
        "OPENOBSERVE_PASSWORD", raw, environ
    ):
        found.append(
            "OPENOBSERVE_USER is set but OPENOBSERVE_PASSWORD is not; the "
            "collector would sign in with the bundled container's password. "
            "Set OPENOBSERVE_PASSWORD too, or remove OPENOBSERVE_USER."
        )

    endpoint = effective("OPENOBSERVE_OTLP_ENDPOINT", raw, environ)
    if endpoint and not re.search(r"/api/[^/]+/?$", endpoint):
        found.append(
            "OPENOBSERVE_OTLP_ENDPOINT must end with the organisation path, for "
            "example http://openobserve.example:5080/api/default; the collector "
            "adds /v1/traces, /v1/metrics and /v1/logs to it."
        )
    return found


def main(argv: Optional[list[str]] = None) -> int:
    """Check the env file; print each problem to stderr; 1 when any."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    found = problems(read_env_file(args.env_file), os.environ)
    for message in found:
        logger.error("obs-env: %s", message)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
