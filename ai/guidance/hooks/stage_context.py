"""UserPromptSubmit hook: prepend a three-line status block to the prompt.

Whatever this hook prints on stdout (exit 0) is added to Claude's context
for the turn, so the operator does not have to retype it (design C §5.4):

* the current SDLC stage, from ``.cache/stage`` (one word; default
  "Development");
* open defects, counted from ``docs/governance/defect-trail.md`` rows whose
  last column is not closed;
* the status line of the newest nightly report under
  ``ops/reports/nightly/*.md`` (the first line containing "Status", else its
  first heading).

Never blocks; a missing file just shortens the block.
Run standalone: ``python3 ai/guidance/hooks/stage_context.py <<< '{}'``.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _hooklib import read_payload, repo_root  # noqa: E402

STAGES = (
    "Requirements",
    "Design",
    "Development",
    "Testing",
    "Deployment",
    "Operations",
)
ROW_RE = re.compile(r"^\|\s*DEF-\d{3}\s*\|.*\|\s*(?P<status>[^|]+?)\s*\|\s*$")


def current_stage(root: Path) -> str:
    """Stage from .cache/stage, normalised to one of the six names."""
    path = root / ".cache" / "stage"
    if path.exists():
        word = path.read_text(encoding="utf-8").strip().capitalize()
        if word in STAGES:
            return word
    return "Development"


def open_defects(root: Path) -> Optional[int]:
    """Count of defect-trail rows not marked closed, or None if no file."""
    path = root / "docs" / "governance" / "defect-trail.md"
    if not path.exists():
        return None
    count = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        match = ROW_RE.match(line.strip())
        if match and match.group("status").lower() not in {
            "closed",
            "wontfix",
            "won't fix",
        }:
            count += 1
    return count


def nightly_status(root: Path) -> Optional[str]:
    """Status line of the newest nightly report, if any."""
    reports = sorted((root / "ops" / "reports" / "nightly").glob("*.md"))
    if not reports:
        return None
    newest = reports[-1]
    lines = newest.read_text(encoding="utf-8").splitlines()
    status = next((ln for ln in lines if "status" in ln.lower()), None)
    heading = next((ln for ln in lines if ln.startswith("#")), newest.name)
    return f"{newest.name}: {(status or heading).strip('# ').strip()}"


def main() -> None:
    """Entry point: print the context block."""
    root = repo_root(read_payload())
    lines = [f"[trustops] Stage: {current_stage(root)}"]
    defects = open_defects(root)
    lines.append(
        f"[trustops] Open defects: {defects}"
        if defects is not None
        else "[trustops] Open defects: no defect trail yet"
    )
    nightly = nightly_status(root)
    lines.append(
        f"[trustops] Last nightly: {nightly}"
        if nightly
        else "[trustops] Last nightly: none yet"
    )
    sys.stdout.write("\n".join(lines) + "\n")
    sys.exit(0)


if __name__ == "__main__":
    main()
