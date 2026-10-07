"""``docker-compose.yaml``: an image built on this machine is never pulled.

``harness`` shares the image that ``api`` builds (``trustops/backend:local``) and
has no ``build`` section of its own. Without ``pull_policy: never`` a first
``docker compose up`` on a machine that does not have the image yet tries to pull
it from a registry and stops with "authorization failed".
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

COMPOSE = Path(__file__).resolve().parents[3] / "docker-compose.yaml"
LOCAL_IMAGE_PREFIX = "trustops/"


def _services() -> dict[str, dict[str, Any]]:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]


def test_a_locally_built_image_without_a_build_section_is_never_pulled() -> None:
    sharing = {
        name: service
        for name, service in _services().items()
        if str(service.get("image", "")).startswith(LOCAL_IMAGE_PREFIX)
        and "build" not in service
    }

    assert "harness" in sharing
    for name, service in sharing.items():
        assert service.get("pull_policy") == "never", name


def test_every_local_image_is_built_by_some_service() -> None:
    services = _services().values()
    built = {s["image"] for s in services if "build" in s and "image" in s}
    used = {
        s["image"]
        for s in services
        if str(s.get("image", "")).startswith(LOCAL_IMAGE_PREFIX)
    }

    assert used <= built
