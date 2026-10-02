"""SubagentStop hook: a subagent's final message must carry its report.

Every build subagent ends with three sections, as the brief template asks
(ai/guidance/subagent-brief-template.md): ``Changed``, ``Failed`` and
``Decided``. They feed the defect trail, the failure log and the decision
log. This hook finds the final assistant message and blocks with the
template when a section is missing.

Where the final message comes from, in order: payload keys
``last_assistant_message``, ``final_message``, ``message``, ``result``,
``output``; else the last assistant entry of the JSONL at
``transcript_path``. If no text can be found the hook does not block (it
cannot verify) and says so on stderr. Inside a stop cycle that is already
active (``stop_hook_active``) it blocks at most once more, then lets go.

Run standalone::

    python3 ai/guidance/hooks/subagent_report.py <<< '{"last_assistant_message": "."}'
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _hooklib import allow, block, cache_dir, read_payload, repo_root  # noqa: E402

SECTIONS = ("Changed", "Failed", "Decided")
TEXT_KEYS = ("last_assistant_message", "final_message", "message", "result", "output")
TEMPLATE = """End your final message with three sections (headings or bold labels):

## Changed
- <file path> - <what changed and why>   (or "nothing")

## Failed
- <what failed or was not done, with the command and its output>   (or "nothing")

## Decided
- <each judgement call, marked (proposed), with the D-xx / ADR-nn it touches>"""


def section_present(text: str, name: str) -> bool:
    """A heading, bold label or `Name:` line that starts a section."""
    pattern = rf"(?im)^\s*(#{{1,6}}\s*|\*\*|__)?\s*{name}\b\s*(\*\*|__)?\s*:?"
    return re.search(pattern, text) is not None


def text_of(content: Any) -> str:
    """Concatenate text from a message `content` (string or block list)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        return "\n".join(p for p in parts if p)
    return ""


def from_transcript(path_str: str) -> Optional[str]:
    """Text of the last assistant message in a JSONL transcript."""
    path = Path(path_str).expanduser()
    if not path.is_file():
        return None
    last: Optional[str] = None
    with path.open(encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            message = (
                entry.get("message")
                if isinstance(entry.get("message"), dict)
                else entry
            )
            if entry.get("type") == "assistant" or message.get("role") == "assistant":
                text = text_of(message.get("content"))
                if text.strip():
                    last = text
    return last


def final_text(payload: dict[str, Any]) -> Optional[str]:
    """Best-effort final assistant text from the payload."""
    for key in TEXT_KEYS:
        value = payload.get(key)
        text = (
            text_of(value.get("content")) if isinstance(value, dict) else text_of(value)
        )
        if text.strip():
            return text
    transcript = payload.get("agent_transcript_path") or payload.get("transcript_path")
    return from_transcript(transcript) if isinstance(transcript, str) else None


def main() -> None:
    """Entry point."""
    payload = read_payload()
    text = final_text(payload)
    if text is None:
        allow("subagent_report: no final message in payload or transcript; unverified")
    missing = [s for s in SECTIONS if not section_present(text, s)]
    marker = cache_dir(repo_root(payload)) / "subagent_report.blocked"
    if not missing:
        marker.unlink(missing_ok=True)
        allow()
    if payload.get("stop_hook_active") and marker.exists():
        marker.unlink(missing_ok=True)
        allow(
            f"subagent_report: still missing {', '.join(missing)} after one reminder; "
            "letting the subagent stop"
        )
    marker.write_text(", ".join(missing), encoding="utf-8")
    block(
        f"Your final report is missing: {', '.join(missing)}.\n\n{TEMPLATE}",
        event="SubagentStop",
    )


if __name__ == "__main__":
    main()
