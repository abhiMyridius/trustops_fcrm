"""Bootstrap generator for ai/registry.lock/*.lock.

Walks the declarative AI layer and writes four lock files, each a sorted list
of ``name<TAB>version<TAB>sha256`` lines, where sha256 is the hex digest of the
file's bytes exactly as committed:

* agents.lock  - ai/agents/<name>.agent.yaml   (name and version from the YAML)
* tools.lock   - ai/tools/<name>.tool.yaml     (name and version from the YAML)
* skills.lock  - ai/skills/<name>.skill.md     (id and version from front-matter)
* prompts.lock - ai/prompts/<agent>/<id>.v<N>.md (id and version from
  front-matter; one line per version file, so old versions stay locked)

This is a bootstrap so the lock files are real today. The fcrm_prompts_loader
package (another lane) should own lock computation and verification
(``make locks`` and tests/registry/test_locks.py) and replace this script.

Usage::

    python3 ai/_tools/compute_locks.py           # write the four lock files
    python3 ai/_tools/compute_locks.py --check   # exit 1 if any lock is stale
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import re
import sys
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)

AI_ROOT = Path(__file__).resolve().parent.parent
LOCK_DIR = AI_ROOT / "registry.lock"
PROMPT_FILE_RE = re.compile(r"^(?P<id>[a-z_]+(\.[a-z_]+)+)\.v(?P<version>\d+)\.md$")
FRONT_MATTER_RE = re.compile(r"\A---\n(?P<body>.*?)\n---\n", re.DOTALL)


class LockError(Exception):
    """Raised when a registry file cannot be read into a lock line."""


def sha256_of(path: Path) -> str:
    """Return the hex sha256 of a file's bytes."""
    with path.open("rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def read_front_matter(path: Path) -> dict[str, Any]:
    """Parse the YAML front-matter block at the top of a Markdown file."""
    with path.open(encoding="utf-8") as handle:
        text = handle.read()
    match = FRONT_MATTER_RE.match(text)
    if match is None:
        raise LockError(f"{path}: no YAML front-matter")
    data = yaml.safe_load(match.group("body"))
    if not isinstance(data, dict):
        raise LockError(f"{path}: front-matter is not a mapping")
    return data


def read_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML spec file that must be a mapping."""
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise LockError(f"{path}: not a mapping")
    return data


def spec_line(path: Path) -> str:
    """Lock line for an agent or tool YAML spec."""
    data = read_yaml(path)
    name, version = data.get("name"), data.get("version")
    if not name or version is None:
        raise LockError(f"{path}: missing name or version")
    return f"{name}\t{version}\t{sha256_of(path)}"


def skill_line(path: Path) -> str:
    """Lock line for a skill file (id and version from front-matter)."""
    meta = read_front_matter(path)
    return f"{meta['id']}\t{meta['version']}\t{sha256_of(path)}"


def prompt_line(path: Path) -> Optional[str]:
    """Lock line for a prompt version file; None for non-prompt Markdown."""
    match = PROMPT_FILE_RE.match(path.name)
    if match is None:
        return None
    meta = read_front_matter(path)
    if meta.get("id") != match.group("id"):
        raise LockError(f"{path}: front-matter id {meta.get('id')!r} != file name")
    if int(meta.get("version", -1)) != int(match.group("version")):
        raise LockError(f"{path}: front-matter version != file name version")
    return f"{meta['id']}\t{meta['version']}\t{sha256_of(path)}"


def build_locks() -> dict[str, str]:
    """Return the content of every lock file keyed by file name."""
    agents = [spec_line(p) for p in sorted((AI_ROOT / "agents").glob("*.agent.yaml"))]
    tools = [spec_line(p) for p in sorted((AI_ROOT / "tools").glob("*.tool.yaml"))]
    skills = [skill_line(p) for p in sorted((AI_ROOT / "skills").glob("*.skill.md"))]
    prompt_paths = sorted((AI_ROOT / "prompts").glob("*/*.md"))
    prompts = [line for p in prompt_paths if (line := prompt_line(p)) is not None]
    return {
        "agents.lock": "\n".join(sorted(agents)) + "\n",
        "tools.lock": "\n".join(sorted(tools)) + "\n",
        "skills.lock": "\n".join(sorted(skills)) + "\n",
        "prompts.lock": "\n".join(sorted(prompts)) + "\n",
    }


def main(argv: Optional[list[str]] = None) -> int:
    """Write or check the lock files; return a process exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if stale")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        locks = build_locks()
    except (LockError, OSError, yaml.YAMLError, KeyError) as exc:
        logger.error("lock computation failed: %s", exc)
        return 2
    stale = []
    for name, content in locks.items():
        target = LOCK_DIR / name
        current = target.read_text(encoding="utf-8") if target.exists() else ""
        if current == content:
            continue
        stale.append(name)
        if not args.check:
            LOCK_DIR.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
    for name, content in locks.items():
        logger.info("%s: %d entries", name, content.count("\n"))
    if args.check and stale:
        logger.error("stale locks (run without --check): %s", ", ".join(stale))
        return 1
    if stale:
        logger.info("rewrote: %s", ", ".join(stale))
    return 0


if __name__ == "__main__":
    sys.exit(main())
