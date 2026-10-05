"""Shared helpers for synthesis modules."""

from __future__ import annotations

import re
from pathlib import Path


def strip_markdown_fences(text: str) -> str:
    """Remove optional ``` fences from LLM output."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


def extract_json_from_response(text: str) -> str:
    """Extract a JSON object string from a raw LLM response.

    Handles all common LLM output patterns:
    - Clean JSON (no fences)
    - Fenced with ```json ... ``` or ``` ... ```
    - JSON preceded or followed by explanatory prose
    - Truncated responses (returns what can be found)

    Returns the extracted JSON string ready for ``json.loads()``.
    Raises ``ValueError`` if no JSON object boundary can be found.
    """
    text = text.strip()
    if not text:
        raise ValueError("Empty response from LLM")

    # Strategy 1: JSON inside a fenced block (```json or ```)
    fence_match = re.search(
        r"```(?:json)?\s*\n?(.*?)```",
        text,
        re.DOTALL,
    )
    if fence_match:
        candidate = fence_match.group(1).strip()
        if candidate.startswith("{"):
            return candidate

    # Strategy 2: Find the outermost { ... } block
    # Walk forward to find opening brace, then backward to find closing brace.
    brace_start = text.find("{")
    brace_end = text.rfind("}")
    if brace_start != -1 and brace_end > brace_start:
        return text[brace_start : brace_end + 1]

    # Strategy 3: Return stripped text as-is (let caller handle parse error)
    raise ValueError(
        f"No JSON object found in LLM response. "
        f"First 200 chars: {text[:200]!r}"
    )


def mkdir_and_write(path: Path, content: str) -> None:
    """Blocking filesystem write (invoked via asyncio.to_thread)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
