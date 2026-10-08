#!/usr/bin/env python3
"""Create a short local ``.env`` with generated dev secrets.

Used by ``make env`` (and therefore ``make up`` / ``make up-infra``). It never
overwrites an existing file, never prints a secret, and writes the file with
mode 0600.

The file it writes holds only what a person may need to touch, in three
blocks: the optional AWS credentials for live mode, the host ports most
likely to clash (commented out, with their defaults), and the generated
values compose requires. Every other variable is left out, so compose
applies its documented ``${VAR:-default}``. ``.env.example`` stays the full
reference and the contract ``make compose-check`` enforces; every name
written here must exist there.

Usernames get fixed local names; passwords and the service-token secret get
``secrets.token_hex`` values (hex, so they are safe inside the Mongo/AMQP
URIs compose builds).

Usage::

    python ops/scripts/make_env.py [--example .env.example] [--out .env]
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import secrets
import string
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger("make_env")

USERNAMES = {
    "MONGO_INITDB_ROOT_USERNAME": "fcrm_root",
    "RABBITMQ_DEFAULT_USER": "fcrm",
    "MINIO_ROOT_USER": "fcrm-minio",
    "ZO_ROOT_USER_EMAIL": "admin@trustops.example",
}
SECRETS = (
    "MONGO_INITDB_ROOT_PASSWORD",
    "MONGO_AUDIT_WRITER_PASSWORD",
    "MONGO_EVALS_READER_PASSWORD",
    "MONGO_APP_PASSWORD",
    "RABBITMQ_DEFAULT_PASS",
    "MINIO_ROOT_PASSWORD",
    "ZO_ROOT_USER_PASSWORD",
    "SERVICE_TOKEN_SECRET",
)
LOCAL_DEFAULTS = {
    "ENV": "local",
    "VECTOR_BACKEND": "memory",
    "EMBEDDING_PROVIDER": "bedrock",
}
#: The live-mode block, always written so there is one obvious place to add
#: keys: the three standard boto3 / AWS CLI names, with the region pre-filled.
AWS = (
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_DEFAULT_REGION",
)
AWS_DEFAULT_REGION = "us-east-1"
#: Written commented out: only temporary (SSO / assumed-role) keys need it.
AWS_SESSION_TOKEN = "AWS_SESSION_TOKEN"
#: Host ports that most often clash with other local stacks, with what each is.
COMMON_PORTS = (
    ("GATEWAY_HOST_PORT", "the app and API"),
    ("MONGO_HOST_PORT", "MongoDB"),
    ("RABBITMQ_HOST_PORT", "RabbitMQ"),
    ("RABBITMQ_MGMT_HOST_PORT", "RabbitMQ admin UI"),
    ("REDIS_HOST_PORT", "Redis"),
    ("MINIO_HOST_PORT", "MinIO (local S3)"),
    ("MINIO_CONSOLE_HOST_PORT", "MinIO console"),
    ("OPA_HOST_PORT", "OPA policy engine"),
    ("OPENOBSERVE_HOST_PORT", "OpenObserve (traces, metrics, logs)"),
)
#: OpenObserve refuses a root password without a lowercase letter, an uppercase
#: letter, a digit and a special character (it exits on start otherwise). These
#: specials are safe unquoted in .env, in compose interpolation (no ``$``) and
#: in an HTTP basic-auth header (no ``:``).
PASSWORD_SPECIALS = "-_."
COMPLEX_PASSWORD_LENGTH = 32
ASSIGNMENT = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")
#: ``# [8080]`` or ``# [5173] vite dev server``: the next assignment's default.
BRACKET_DEFAULT = re.compile(r"^#.*?\[([^\]]+)\]")

HEADER = """\
# TrustOps local settings, written by `make env`. Never commit this file.
# Anything not listed here uses its built-in default; every setting is
# described in .env.example.
"""
AWS_INTRO = """\
# ── 1. Live AI on Amazon Bedrock ────────────────────────────────────────
# The agents call Bedrock (there is no replay mode). Fill these in to run
# them; without keys the services, login and web app still work, but every
# agent run fails at the model call.
"""
PORTS_INTRO = """\
# ── 2. Optional: host ports ────────────────────────────────────────────
# Change one only if `up` says "port is already allocated": uncomment the
# line and pick a free port. The values shown are the defaults.
"""
GENERATED_INTRO = """\
# ── 3. Generated: leave as is ──────────────────────────────────────────
# Local passwords and secrets. The databases are created with these on the
# first start, so changing them later needs `make down-v` (deletes local data).
"""


def complex_password() -> str:
    """A random password with all four character classes, safe in ``.env``.

    Returns:
        32 characters: letters and digits, with one special from
        :data:`PASSWORD_SPECIALS` somewhere in the interior.
    """
    alnum = string.ascii_letters + string.digits
    chars = [
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.digits),
    ]
    chars += [secrets.choice(alnum) for _ in range(COMPLEX_PASSWORD_LENGTH - 4)]
    secrets.SystemRandom().shuffle(chars)
    chars.insert(
        secrets.randbelow(len(chars) - 1) + 1, secrets.choice(PASSWORD_SPECIALS)
    )
    return "".join(chars)


def example_defaults(example: str) -> dict[str, str]:
    """Map each ``.env.example`` assignment to the ``[default]`` in its comment."""
    defaults: dict[str, str] = {}
    previous = ""
    for line in example.splitlines():
        match = ASSIGNMENT.match(line)
        if match:
            bracket = BRACKET_DEFAULT.match(previous)
            defaults[match.group(1)] = bracket.group(1) if bracket else ""
        previous = line
    return defaults


def render(
    example: str, overrides: Optional[dict[str, str]] = None
) -> tuple[str, list[str]]:
    """Build the short ``.env``; return the text and the names given a value.

    Args:
        example: the contents of ``.env.example``.
        overrides: values the caller computed (``--set NAME=VALUE``), e.g. the
            bcrypt ``DEMO_USERS_PASSWORD_HASH`` the Makefile derives with the
            project venv, since this script runs on the system interpreter.

    Returns:
        The rendered ``.env`` text and the names that received a value.

    Raises:
        ValueError: a name this script writes is missing from ``.env.example``
            (the contract has drifted).
    """
    overrides = overrides or {}
    known = example_defaults(example)
    wanted = [
        *AWS,
        AWS_SESSION_TOKEN,
        *(n for n, _ in COMMON_PORTS),
        *USERNAMES,
        *SECRETS,
        *LOCAL_DEFAULTS,
        *overrides,
    ]
    missing = sorted({n for n in wanted if n not in known})
    if missing:
        raise ValueError(f".env.example has no assignment for: {', '.join(missing)}")

    def value_of(name: str) -> str:
        if name in overrides:
            # docker compose interpolates ``$NAME`` inside .env values, so a
            # literal dollar (bcrypt hashes start with ``$2b$12$``) must be
            # written as ``$$`` or compose silently mangles the value.
            return overrides[name].replace("$", "$$")
        if name == "ZO_ROOT_USER_PASSWORD":
            return complex_password()
        if name in SECRETS:
            return secrets.token_hex(24)
        return USERNAMES.get(name) or LOCAL_DEFAULTS.get(name, "")

    filled: list[str] = []
    lines = [HEADER.rstrip("\n"), "", AWS_INTRO.rstrip("\n")]
    lines += [
        f"{name}={AWS_DEFAULT_REGION if name == 'AWS_DEFAULT_REGION' else ''}"
        for name in AWS
    ]
    lines.append(f"# {AWS_SESSION_TOKEN}=    only for temporary (SSO) credentials")
    lines += ["", PORTS_INTRO.rstrip("\n")]
    width = max(len(f"{n}={known[n]}") for n, _ in COMMON_PORTS)
    lines += [f"# {f'{n}={known[n]}':<{width}}  {what}" for n, what in COMMON_PORTS]
    lines += [
        "# (every other port: see .env.example)",
        "",
        GENERATED_INTRO.rstrip("\n"),
    ]
    generated = [*USERNAMES, *SECRETS, *LOCAL_DEFAULTS]
    generated += [n for n in overrides if n not in generated and n not in AWS]
    for name in generated:
        value = value_of(name)
        if value:
            filled.append(name)
        lines.append(f"{name}={value}")
    return "\n".join(lines) + "\n", filled


def main(argv: Optional[list[str]] = None) -> int:
    """Write the env file unless it already exists."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--example", default=".env.example", type=Path)
    parser.add_argument("--out", default=".env", type=Path)
    parser.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="pre-computed value for one variable (repeatable)",
    )
    args = parser.parse_args(argv)
    overrides: dict[str, str] = {}
    for item in args.set:
        name, sep, value = item.partition("=")
        if not sep or not ASSIGNMENT.match(f"{name}="):
            parser.error(f"--set expects NAME=VALUE, got {item!r}")
        overrides[name] = value

    if args.out.exists():
        logger.info("%s already exists; leaving it untouched", args.out)
        return 0
    try:
        text, filled = render(args.example.read_text(encoding="utf-8"), overrides)
    except ValueError as exc:
        logger.error("make_env: %s", exc)
        return 1
    fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    logger.info("wrote %s (mode 0600); generated: %s", args.out, ", ".join(filled))
    logger.info("to use live Bedrock, add your AWS keys in section 1 of %s", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
