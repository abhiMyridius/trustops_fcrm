"""Registry locks (design B §1.8): sorted ``name<TAB>version<TAB>sha256``
lines for a directory of files, plus parse and diff.

- ``name`` is the file's path relative to the locked directory, POSIX
  separators (``research/research.system.v1.md``), so two files with the
  same stem in different folders never collide.
- ``version`` is the file's own declared version: the front-matter
  ``version`` of a ``.md`` file, the top-level ``version`` key of a YAML
  file, else ``-`` (a file with no version concept).
- ``sha256`` is of the raw bytes.

:func:`diff_locks` is what a lock test prints. The one diff that matters
most is "content changed, version did not": that is a prompt edited in
place, which design B §1.5 forbids (a change is a new ``vN+1`` file).

The ai/ lane's bootstrap script writes ``ai/registry.lock/*.lock`` today;
this is a separately tested implementation of the same idea, and the
formats are reconciled when the lock test lands.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict

from fcrm_prompts.errors import FrontMatterError, LockFormatError
from fcrm_prompts.frontmatter import split_front_matter

logger = logging.getLogger(__name__)

NO_VERSION = "-"
_YAML_SUFFIXES = (".yaml", ".yml")


class LockEntry(BaseModel):
    """One lock line."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    version: str
    sha256: str

    def line(self) -> str:
        """``name<TAB>version<TAB>sha256``."""
        return f"{self.name}\t{self.version}\t{self.sha256}"


def _declared_version(path: Path, data: bytes) -> str:
    text = data.decode("utf-8", errors="replace")
    loaded: Any = None
    if path.suffix == ".md":
        try:
            loaded, _ = split_front_matter(text, source=str(path))
        except FrontMatterError:
            return NO_VERSION
    elif path.suffix in _YAML_SUFFIXES:
        try:
            loaded = yaml.safe_load(text)
        except yaml.YAMLError:
            logger.warning("unparseable YAML in lock scan", extra={"path": str(path)})
            return NO_VERSION
    if isinstance(loaded, dict) and loaded.get("version") is not None:
        return str(loaded["version"])
    return NO_VERSION


def compute_lock(directory: Path, pattern: str = "**/*") -> list[LockEntry]:
    """Lock entries for every regular, non-hidden file under ``directory``
    matching ``pattern``, sorted by name. An absent directory yields no
    entries and a warning (the ai/ lane may not have landed)."""
    if not directory.is_dir():
        logger.warning("lock directory %s is absent; empty lock", directory)
        return []
    entries = []
    for path in directory.glob(pattern):
        relative = path.relative_to(directory)
        if not path.is_file() or any(p.startswith(".") for p in relative.parts):
            continue
        with path.open("rb") as handle:
            data = handle.read()
        entries.append(
            LockEntry(
                name=relative.as_posix(),
                version=_declared_version(path, data),
                sha256=hashlib.sha256(data).hexdigest(),
            )
        )
    return sorted(entries, key=lambda e: e.name)


def render_lock(entries: Sequence[LockEntry]) -> str:
    """The lock file text: one sorted line per entry, trailing newline."""
    lines = [e.line() for e in sorted(entries, key=lambda e: e.name)]
    return "".join(f"{line}\n" for line in lines)


def parse_lock(text: str) -> list[LockEntry]:
    """Parse a lock file (blank lines and ``#`` comments ignored).

    Raises:
        LockFormatError: a line without exactly three tab-separated fields,
            or a name listed twice.
    """
    entries: dict[str, LockEntry] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip() or raw.startswith("#"):
            continue
        parts = raw.split("\t")
        if len(parts) != 3 or not all(parts):
            msg = f"lock line {number}: expected name<TAB>version<TAB>sha256"
            raise LockFormatError(msg)
        name, version, digest = parts
        if name in entries:
            msg = f"lock line {number}: {name!r} listed twice"
            raise LockFormatError(msg)
        entries[name] = LockEntry(name=name, version=version, sha256=digest)
    return list(entries.values())


def diff_locks(expected: Sequence[LockEntry], actual: Sequence[LockEntry]) -> list[str]:
    """Human-readable differences from ``expected`` (the committed lock) to
    ``actual`` (the files now); empty when they match."""
    before = {e.name: e for e in expected}
    after = {e.name: e for e in actual}
    problems = [f"added: {name}" for name in sorted(after.keys() - before.keys())]
    problems += [f"removed: {name}" for name in sorted(before.keys() - after.keys())]
    for name in sorted(before.keys() & after.keys()):
        old, new = before[name], after[name]
        if old.sha256 == new.sha256 and old.version == new.version:
            continue
        if old.sha256 != new.sha256 and old.version == new.version:
            problems.append(f"changed without a version bump: {name} (v{old.version})")
        elif old.version != new.version:
            problems.append(f"version {old.version} -> {new.version}: {name}")
        else:
            problems.append(f"changed: {name}")
    return problems


def write_lock(directory: Path, lock_path: Path, pattern: str = "**/*") -> str:
    """Compute the lock for ``directory`` and write it to ``lock_path``;
    returns the written text (``make locks``)."""
    text = render_lock(compute_lock(directory, pattern))
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w", encoding="utf-8") as handle:
        handle.write(text)
    return text
