#!/usr/bin/env python3
r"""Wait until compose services are ready (``make up`` / ``make up-infra``).

A service counts as ready when its container(s) report ``healthy``; when it
has no healthcheck, when it is ``running``; when it is a one-shot
(``--oneshot``), when it has exited with code 0. Any container that exits
non-zero (or a one-shot that fails) stops the wait at once with its last
log lines, instead of hanging until the timeout. ``--url NAME=URL`` adds an
HTTP probe for containers that cannot carry a healthcheck (the distroless
otel-collector).

Usage::

    python ops/scripts/wait_for_ready.py --compose "docker compose -f a -f b" \\
        --oneshot mongo-init --oneshot minio-init mongo rabbitmq ... \\
        --url otel-collector=http://127.0.0.1:13133/
"""

from __future__ import annotations

import argparse
import json
import logging
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request

logger = logging.getLogger("wait_for_ready")


def compose_ps(compose: list[str]) -> list[dict[str, object]]:
    """Return ``docker compose ps -a`` rows as dicts."""
    out = subprocess.run(  # noqa: S603 - argv built from the Makefile's compose command
        [*compose, "ps", "-a", "--format", "json"],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    ).stdout.strip()
    if not out:
        return []
    if out.startswith("["):
        rows: list[dict[str, object]] = json.loads(out)
        return rows
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def service_state(rows: list[dict[str, object]], service: str, oneshot: bool) -> str:
    """Classify one service as ``ready``, ``waiting`` or ``failed``."""
    mine = [r for r in rows if r.get("Service") == service]
    if not mine:
        return "waiting"
    states = []
    for row in mine:
        state = str(row.get("State", ""))
        health = str(row.get("Health", ""))
        exit_code = int(str(row.get("ExitCode", 0) or 0))
        if state == "exited":
            states.append("ready" if oneshot and exit_code == 0 else "failed")
        elif oneshot:
            states.append("waiting")
        elif health == "healthy" or (not health and state == "running"):
            states.append("ready")
        elif health == "unhealthy" and state == "running":
            states.append("waiting")  # still inside its retries window
        else:
            states.append("waiting")
    if "failed" in states:
        return "failed"
    return "ready" if all(s == "ready" for s in states) else "waiting"


def url_ok(url: str) -> bool:
    """Return True when ``url`` answers 2xx within two seconds."""
    try:
        with urllib.request.urlopen(url, timeout=2) as response:  # noqa: S310 - local probe
            status: int = response.status
            return 200 <= status < 300
    except (urllib.error.URLError, OSError):
        return False


def main(argv: list[str] | None = None) -> int:
    """Poll until every service is ready, one fails, or the timeout passes."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--compose", required=True, help="compose command prefix")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--oneshot", action="append", default=[])
    parser.add_argument("--url", action="append", default=[], help="NAME=URL")
    parser.add_argument("services", nargs="*")
    args = parser.parse_args(argv)

    compose = shlex.split(args.compose)
    services = list(dict.fromkeys([*args.oneshot, *args.services]))
    urls = dict(item.split("=", 1) for item in args.url)
    deadline = time.monotonic() + args.timeout
    pending: list[str] = []
    while time.monotonic() < deadline:
        rows = compose_ps(compose)
        pending = []
        for service in services:
            state = service_state(rows, service, service in args.oneshot)
            if state == "failed":
                logger.error("%s failed; last log lines:", service)
                try:
                    subprocess.run(  # noqa: S603 - argv built from the Makefile's compose command
                        [*compose, "logs", "--tail", "40", service],
                        check=False,
                        timeout=30,
                    )
                except subprocess.TimeoutExpired:
                    logger.error("%s: reading its logs timed out", service)
                return 1
            if state != "ready":
                pending.append(service)
        pending += [name for name, url in urls.items() if not url_ok(url)]
        if not pending:
            logger.info("ready: %s", ", ".join([*services, *urls]))
            return 0
        time.sleep(3)
    logger.error(
        "timed out after %ss waiting for: %s", args.timeout, ", ".join(pending)
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
