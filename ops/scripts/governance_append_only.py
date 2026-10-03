#!/usr/bin/env python3
"""pre-commit hook: governance tables are append-only (CLAUDE.md, design C §5.6).

Fails when the staged diff removes or rewrites an existing table row
(a line starting with ``|``) in any Markdown file under ``docs/governance``.
New rows, new prose and new files pass; correcting a decision means a new
row that supersedes the old one, never an edit.
"""

from __future__ import annotations

import logging
import subprocess
import sys

logger = logging.getLogger("governance_append_only")


def removed_rows() -> list[str]:
    """Return ``path: row`` for every table row the staged diff deletes."""
    diff = subprocess.run(
        [
            "git",
            "diff",
            "--cached",
            "--unified=0",
            "--no-color",
            "--",
            "docs/governance",
        ],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    path = ""
    found: list[str] = []
    for line in diff.splitlines():
        if line.startswith("--- "):
            path = line[6:] if line.startswith("--- a/") else line[4:]
        elif (
            line.startswith("-")
            and not line.startswith("---")
            and line[1:].lstrip().startswith("|")
            and path.endswith(".md")
        ):
            found.append(f"{path}: {line[1:].strip()[:100]}")
    return found


def main() -> int:
    """Exit non-zero when an existing governance row was changed or removed."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    rows = removed_rows()
    for row in rows:
        logger.error(
            "governance row changed or removed (append a new row instead): %s", row
        )
    return 1 if rows else 0


if __name__ == "__main__":
    sys.exit(main())
