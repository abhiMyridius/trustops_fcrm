#!/usr/bin/env python3
"""Load the TrustOps dashboards into OpenObserve (``make obs-dashboards``,
run by ``make up-obs``; ADR-72).

Each ``ops/openobserve/dashboards/*.json`` is an OpenObserve dashboard
(schema v8, PromQL panels over the ``fcrm_*`` metrics and RabbitMQ's queue
depths). A dashboard is matched by title: an existing one is updated in place
(its id, and so any bookmark, is kept), a missing one is created. Running it
again changes nothing but the panels, so it is safe on every ``make up-obs``.

The instance and login are resolved as ``openobserve_client`` says; nothing
is printed but titles and statuses.

Usage::

    python3 ops/scripts/openobserve_dashboards.py [--env-file .env] [--url URL]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Optional

from openobserve_client import OpenObserve, OpenObserveError

logger = logging.getLogger("openobserve_dashboards")

ROOT = Path(__file__).resolve().parents[2]
DASHBOARD_DIR = ROOT / "ops" / "openobserve" / "dashboards"
SCHEMA_VERSION = 8
FOLDER = "default"


def load_dashboards(directory: Path) -> list[dict[str, Any]]:
    """Every ``*.json`` under ``directory``, by file name; each must be a v8
    dashboard with a title, and no two may share one (titles are the key)."""
    dashboards: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        board = json.loads(path.read_text(encoding="utf-8"))
        if board.get("version") != SCHEMA_VERSION or not board.get("title"):
            raise ValueError(
                f"{path.name}: not a v{SCHEMA_VERSION} dashboard with a title"
            )
        dashboards.append(board)
    titles = [board["title"] for board in dashboards]
    if len(set(titles)) != len(titles):
        raise ValueError(f"two dashboards share a title in {directory}")
    return dashboards


def existing_by_title(listing: Mapping[str, Any]) -> dict[str, tuple[str, str]]:
    """``title -> (dashboard_id, hash)`` from ``GET /dashboards``. The hash is
    OpenObserve's guard against overwriting a concurrent change."""
    found: dict[str, tuple[str, str]] = {}
    for entry in listing.get("dashboards", []):
        title, board_id = entry.get("title"), entry.get("dashboard_id")
        if title and board_id:
            found[title] = (str(board_id), str(entry.get("hash", "")))
    return found


def sync(client: OpenObserve, dashboards: list[dict[str, Any]]) -> dict[str, str]:
    """Create or update each dashboard; ``title -> "created" | "updated"``."""
    existing = existing_by_title(client.request("GET", "/dashboards"))
    done: dict[str, str] = {}
    for board in dashboards:
        title = board["title"]
        if title in existing:
            board_id, board_hash = existing[title]
            client.request(
                "PUT",
                f"/dashboards/{board_id}?folder={FOLDER}&hash={board_hash}",
                {**board, "dashboardId": board_id},
            )
            done[title] = "updated"
        else:
            client.request("POST", f"/dashboards?folder={FOLDER}", board)
            done[title] = "created"
    return done


def main(argv: Optional[list[str]] = None) -> int:
    """Load the dashboards; 1 when OpenObserve or a file refuses."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--url", help="OpenObserve base URL (default: from .env)")
    parser.add_argument("--dir", type=Path, default=DASHBOARD_DIR)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        client = OpenObserve.from_env(args.env_file, url=args.url)
        done = sync(client, load_dashboards(args.dir))
    except (OpenObserveError, ValueError, OSError) as exc:
        logger.error("obs-dashboards: %s", exc)
        return 1
    for title, status in done.items():
        logger.info("obs-dashboards: %s %s", status, title)
    logger.info("obs-dashboards: open %s → Dashboards", client.base)
    return 0


if __name__ == "__main__":
    sys.exit(main())
