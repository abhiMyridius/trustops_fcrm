"""The page:line anchored Markdown ``document_reader`` returns (design B
§2.2: "Lines are numbered inside each page as ``p.N l.M: text``").

:func:`render_pages` writes it; :func:`parse_pages` reads it back so a
context-pack builder can register each page shown as a citable source
(Guard 4 checks a ``DocSourceRef``'s quote against that page's lines)
without reaching around the tool.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Final

_ANCHOR: Final = re.compile(r"^p\.(\d+) l\.(\d+): (.*)$")


def render_pages(
    doc_id: str,
    page_count: int,
    pages: Mapping[int, Sequence[str]],
    *,
    banner: str = "",
) -> str:
    """``# doc_id``, an optional ``>`` banner, then ``## Page N`` blocks of
    ``p.N l.M: text`` lines, in page order."""
    out = [f"# {doc_id} ({page_count} pages)"]
    if banner:
        out.append(f"> {banner}")
    for page in sorted(pages):
        out.append(f"## Page {page}")
        out.extend(
            f"p.{page} l.{number}: {text}"
            for number, text in enumerate(pages[page], start=1)
        )
    return "\n".join(out) + "\n"


def parse_pages(markdown: str) -> dict[int, list[str]]:
    """``{page: [line text, ...]}`` from anchored Markdown; lines are placed
    at their anchored number (gaps filled with empty strings)."""
    pages: dict[int, dict[int, str]] = {}
    for raw in markdown.splitlines():
        match = _ANCHOR.match(raw)
        if match is None:
            continue
        page, line, text = int(match.group(1)), int(match.group(2)), match.group(3)
        pages.setdefault(page, {})[line] = text
    return {
        page: [lines.get(n, "") for n in range(1, max(lines) + 1)]
        for page, lines in sorted(pages.items())
    }
