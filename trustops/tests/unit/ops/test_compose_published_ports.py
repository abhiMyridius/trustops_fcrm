"""``docker-compose.yaml``: what the default stack publishes to the host.

Two things only, both bound through ``BIND_ADDRESS`` (default ``127.0.0.1``, this
machine only): the gateway, which is the web app, and MongoDB, for Compass and
mongosh. Nothing may bind a literal ``0.0.0.0``, which would open the port to
every network the machine is on.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[3]
LOCAL_ONLY_PREFIX = "${BIND_ADDRESS:-127.0.0.1}:"


def _services(name: str) -> dict[str, dict[str, Any]]:
    return yaml.safe_load((ROOT / name).read_text(encoding="utf-8"))["services"]


def test_the_default_stack_publishes_only_the_gateway_and_mongo() -> None:
    published = {
        name
        for name, service in _services("docker-compose.yaml").items()
        if service.get("ports") and not service.get("profiles")
    }

    assert published == {"gateway", "mongo"}


def test_every_published_port_binds_through_bind_address_never_a_literal_wildcard() -> (
    None
):
    for name, service in _services("docker-compose.yaml").items():
        for port in service.get("ports", []):
            assert str(port).startswith(LOCAL_ONLY_PREFIX), (name, port)
            assert "0.0.0.0" not in str(port), (name, port)


def test_the_debug_overlay_does_not_publish_mongo_a_second_time() -> None:
    assert "ports" not in _services("compose.debug.yaml").get("mongo", {})
