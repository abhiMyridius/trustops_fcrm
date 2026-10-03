#!/usr/bin/env python3
"""Create a local ``.env`` from ``.env.example`` with generated dev secrets.

Used by ``make env`` (and therefore ``make up`` / ``make up-infra``). It never
overwrites an existing file, never prints a secret, and writes the file with
mode 0600. Every variable the compose files require (``${VAR:?...}``) gets a
value: usernames get fixed local names, passwords and the service-token
secret get ``secrets.token_hex`` values (hex, so they are safe inside the
Mongo/AMQP URIs compose builds). Everything else is left empty, which makes
compose apply its documented ``${VAR:-default}``.

Usage::

    python ops/scripts/make_env.py [--example .env.example] [--out .env]
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import secrets
import sys
from pathlib import Path

logger = logging.getLogger("make_env")

USERNAMES = {
    "MONGO_INITDB_ROOT_USERNAME": "fcrm_root",
    "RABBITMQ_DEFAULT_USER": "fcrm",
    "MINIO_ROOT_USER": "fcrm-minio",
    "GF_SECURITY_ADMIN_USER": "admin",
}
SECRETS = (
    "MONGO_INITDB_ROOT_PASSWORD",
    "MONGO_AUDIT_WRITER_PASSWORD",
    "MONGO_APP_PASSWORD",
    "RABBITMQ_DEFAULT_PASS",
    "MINIO_ROOT_PASSWORD",
    "GF_SECURITY_ADMIN_PASSWORD",
    "SERVICE_TOKEN_SECRET",
)
LOCAL_DEFAULTS = {
    "ENV": "local",
    "FCRM_LLM_MODE": "replay",
    "VECTOR_BACKEND": "memory",
    "EMBEDDING_PROVIDER": "dummy",
}
ASSIGNMENT = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")


def render(example: str) -> tuple[str, list[str]]:
    """Fill the example's assignments; return the text and generated names.

    Args:
        example: the contents of ``.env.example``.

    Returns:
        The rendered ``.env`` text and the names that received a value.
    """
    lines: list[str] = []
    filled: list[str] = []
    for line in example.splitlines():
        match = ASSIGNMENT.match(line)
        if not match:
            lines.append(line)
            continue
        name = match.group(1)
        value = ""
        if name in SECRETS:
            value = secrets.token_hex(24)
        elif name in USERNAMES:
            value = USERNAMES[name]
        elif name in LOCAL_DEFAULTS:
            value = LOCAL_DEFAULTS[name]
        if value:
            filled.append(name)
        lines.append(f"{name}={value}")
    return "\n".join(lines) + "\n", filled


def main(argv: list[str] | None = None) -> int:
    """Write the env file unless it already exists."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--example", default=".env.example", type=Path)
    parser.add_argument("--out", default=".env", type=Path)
    args = parser.parse_args(argv)

    if args.out.exists():
        logger.info("%s already exists; leaving it untouched", args.out)
        return 0
    text, filled = render(args.example.read_text(encoding="utf-8"))
    fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    logger.info("wrote %s (mode 0600); generated: %s", args.out, ", ".join(filled))
    logger.info("add AWS credentials there to switch to live mode (BUILD-PLAN §11)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
