"""The PASS/FAIL table ``make resilience`` prints, and its JSON record."""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final, Optional

from resilience.errors import CommandError
from resilience.shell import CommandRunner

logger = logging.getLogger(__name__)

PASS: Final = "PASS"
FAIL: Final = "FAIL"
SKIP: Final = "SKIP"


@dataclass(frozen=True)
class DrillResult:
    """The outcome of one drill.

    Attributes:
        drill: Short id (``D1a``).
        title: What the drill does, one line.
        outcome: ``PASS``, ``FAIL`` or ``SKIP``.
        seconds: Wall time of the drill.
        detail: For a failure or skip, the first line of why.
    """

    drill: str
    title: str
    outcome: str
    seconds: float
    detail: str = ""


def render_table(results: Sequence[DrillResult], *, header: str = "") -> str:
    """A fixed-width PASS/FAIL table with the elapsed time of each drill and a
    total line; failures and skips list their reason underneath.
    """
    rows = [(r.drill, r.title, r.outcome, f"{r.seconds:.1f}") for r in results]
    widths = [
        max([len(label), *(len(row[i]) for row in rows)])
        for i, label in enumerate(("drill", "what", "result", "seconds"))
    ]
    lines = [header] if header else []
    lines.append(
        f"{'drill':<{widths[0]}}  {'what':<{widths[1]}}  "
        f"{'result':<{widths[2]}}  {'seconds':>{widths[3]}}"
    )
    lines.append("  ".join("-" * w for w in widths))
    for drill, title, outcome, seconds in rows:
        lines.append(
            f"{drill:<{widths[0]}}  {title:<{widths[1]}}  "
            f"{outcome:<{widths[2]}}  {seconds:>{widths[3]}}"
        )
    passed = sum(r.outcome == PASS for r in results)
    failed = sum(r.outcome == FAIL for r in results)
    skipped = sum(r.outcome == SKIP for r in results)
    total = sum(r.seconds for r in results)
    lines.append("")
    lines.append(f"{passed} passed, {failed} failed, {skipped} skipped in {total:.1f}s")
    for r in results:
        if r.detail and r.outcome != PASS:
            lines.append(f"  {r.drill} {r.outcome}: {r.detail}")
    return "\n".join(lines)


def git_commit(runner: CommandRunner, root: Path) -> Optional[str]:
    """The short id of the commit checked out at ``root`` (``None`` when it is
    not a git checkout or git is missing): what a drill run is recorded against.
    """
    try:
        result = runner.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            timeout_s=10.0,
            check=False,
        )
    except CommandError:
        return None
    text = result.stdout.strip()
    return text if result.returncode == 0 and text else None


def write_report(
    results: Sequence[DrillResult],
    directory: Path,
    *,
    stamp: str,
    commit: Optional[str] = None,
) -> Path:
    """Write the run as ``<directory>/<stamp>.json`` and return the path."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{stamp}.json"
    document = {
        "run": stamp,
        "commit": commit,
        "passed": sum(r.outcome == PASS for r in results),
        "failed": sum(r.outcome == FAIL for r in results),
        "skipped": sum(r.outcome == SKIP for r in results),
        "drills": [asdict(r) for r in results],
    }
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    logger.info("resilience report written to %s", path)
    return path
