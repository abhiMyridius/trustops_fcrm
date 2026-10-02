"""Container HEALTHCHECK probe (design A §11.1).

Usage in a Dockerfile::

    HEALTHCHECK CMD python -m fcrm_common.healthcheck http://localhost:8001/readyz

Exits 0 when the URL answers 2xx within the timeout, 1 otherwise. Uses
only ``httpx`` (already in every image) so slim images need no curl.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Sequence
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

TIMEOUT_S = 3.0


def probe(url: str, timeout_s: float = TIMEOUT_S) -> bool:
    """Whether ``GET url`` answers 2xx within ``timeout_s``."""
    try:
        response = httpx.get(url, timeout=timeout_s)
    except httpx.HTTPError as exc:
        logger.warning("healthcheck failed: %s", type(exc).__name__)
        return False
    return response.is_success


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point; returns the process exit code."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        logger.error("usage: python -m fcrm_common.healthcheck <url>")
        return 2
    return 0 if probe(args[0]) else 1


if __name__ == "__main__":
    sys.exit(main())
