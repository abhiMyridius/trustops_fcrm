#!/usr/bin/env python3
"""Check that the compose files and ``.env.example`` agree (``make compose-check``).

Three assertions, each a real drift bug if it fails:

1. Every ``${VAR}`` the compose files interpolate is an assignment
   (``VAR=``) in ``.env.example``, and every assignment there is used.
2. Every variable a container receives (its resolved ``environment:`` keys)
   is documented in ``.env.example`` — either as an assignment or as a
   ``# VAR=`` line in the "set per container by compose" blocks.
3. ``docker compose config`` renders every file combination (base, + dev,
   + test, + debug) with every profile (obs, cache) enabled and a throwaway
   env file, i.e. the YAML, anchors, overrides and required-variable checks
   are valid.

No values are read from a real ``.env``: a temporary env file with dummy
values is generated from ``.env.example`` for the render.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
import tempfile
from pathlib import Path

logger = logging.getLogger("check_env_contract")

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / ".env.example"
BASE = ["docker-compose.yaml"]
COMBINATIONS = [
    BASE,
    [*BASE, "compose.dev.yaml"],
    [*BASE, "compose.test.yaml"],
    [*BASE, "compose.debug.yaml"],
]
#: Profiles switched on for the render, so optional services are validated too.
PROFILES = ("obs", "cache")
COMPOSE_FILES = sorted({f for combo in COMBINATIONS for f in combo})

INTERPOLATION = re.compile(r"\$\{([A-Z][A-Z0-9_]*)(?::?[-?][^}]*)?\}")
REQUIRED = re.compile(r"\$\{([A-Z][A-Z0-9_]*):\?")
ASSIGNED = re.compile(r"^([A-Z][A-Z0-9_]*)=", re.MULTILINE)
DOCUMENTED = re.compile(r"^#\s?([A-Z][A-Za-z0-9_]*)=", re.MULTILINE)


def interpolated_names(pattern: re.Pattern[str] = INTERPOLATION) -> set[str]:
    """Return every ``${VAR}`` (or, with ``REQUIRED``, every ``${VAR:?}``)."""
    names: set[str] = set()
    for name in COMPOSE_FILES:
        lines = (ROOT / name).read_text(encoding="utf-8").splitlines()
        # Comment lines are prose; $$VAR is a literal for the container shell.
        text = "\n".join(ln for ln in lines if not ln.lstrip().startswith("#"))
        names.update(pattern.findall(text.replace("$$", "")))
    return names


def compose_config(files: list[str], env_file: Path) -> dict[str, object]:
    """Render ``docker compose config`` for one file combination as JSON."""
    cmd = ["docker", "compose", "--env-file", str(env_file)]
    for profile in PROFILES:
        cmd += ["--profile", profile]
    for name in files:
        cmd += ["-f", str(ROOT / name)]
    cmd += ["config", "--format", "json"]
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        cmd, cwd=ROOT, capture_output=True, text=True, check=False, timeout=60
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"compose config failed for {files}: {result.stderr.strip()}"
        )
    rendered: dict[str, object] = json.loads(result.stdout)
    return rendered


def container_env_names(rendered: dict[str, object]) -> set[str]:
    """Return the union of environment keys over all rendered services."""
    names: set[str] = set()
    services = rendered.get("services", {})
    assert isinstance(services, dict)
    for service in services.values():
        env = service.get("environment") or {}
        names.update(env.keys())
    return names


def main() -> int:
    """Run the three checks; non-zero exit on any drift."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    example = EXAMPLE.read_text(encoding="utf-8")
    assigned = set(ASSIGNED.findall(example))
    documented = set(DOCUMENTED.findall(example))
    problems: list[str] = []

    used = interpolated_names()
    for name in sorted(used - assigned):
        problems.append(
            f"compose interpolates ${{{name}}} but .env.example has no {name}="
        )
    for name in sorted(assigned - used):
        problems.append(f".env.example assigns {name}= but no compose file uses it")

    with tempfile.TemporaryDirectory() as tmp:
        env_file = Path(tmp) / "contract.env"
        # Required secrets get a dummy; everything else stays empty so the
        # compose defaults (ports, images, modes) are what gets validated.
        required = interpolated_names(REQUIRED)
        env_file.write_text(
            "".join(
                f"{n}={'dummy' if n in required else ''}\n" for n in sorted(assigned)
            )
        )
        env_names: set[str] = set()
        for files in COMBINATIONS:
            try:
                env_names |= container_env_names(compose_config(files, env_file))
            except (RuntimeError, subprocess.TimeoutExpired) as exc:
                problems.append(str(exc))
            else:
                logger.info("compose config ok: %s", " + ".join(files))

    for name in sorted(env_names - assigned - documented):
        problems.append(
            f"containers receive {name} but .env.example does not document it"
        )

    for problem in problems:
        logger.error("FAIL %s", problem)
    if problems:
        return 1
    logger.info(
        "env contract ok: %d interpolated, %d container variables documented",
        len(used),
        len(env_names),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
