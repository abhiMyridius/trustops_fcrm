r"""Decoding JSON that a model wrote as *text* inside a tool call.

A model asked for a list in a tool argument sometimes sends the list as one JSON
string (seen live from Sonnet 5.5: ``response`` was ``"[{...}]"``). When it does,
it writes the JSON by hand, and a verbatim policy quote is where it slips: a real
line break (D-115), a ``"`` it did not escape, ``\'`` for an apostrophe, a
trailing comma, a Markdown fence around the whole thing. Each one failed the
whole answer and cost a retry; two in a row sent the case to handoff
(FCRM-2026-0154, 19 October).

:func:`loads_text` tries, in order, the text as sent and then the text with
those slips mended. A mend only ever adds a backslash before a ``"``, drops the
backslash of an escape JSON does not have, or drops a fence or a trailing comma:
it never removes or rewrites a character of a string's content, so a quote that
decodes is the quote the model wrote, and Guard 4/5 still check it against the
source. What cannot be mended (text cut off mid-item, a ``"`` whose end is
ambiguous) is refused with the decoder's own reason, never guessed at.
"""

from __future__ import annotations

import json
import re
from typing import Any, Final

_FENCE: Final = re.compile(
    r"\A\s*```[A-Za-z0-9_-]*\s*\n?(?P<body>.*?)\n?\s*```\s*\Z", re.S
)
"""A whole text wrapped in one Markdown code fence (```` ```json ... ``` ````)."""

_ESCAPES: Final = frozenset('"\\/bfnrtu')
"""The characters JSON allows after a backslash."""

_CLOSERS: Final = frozenset(",}]:")
"""What may follow a string's closing quote (after white space)."""


class JSONTextError(ValueError):
    """Text that is not JSON even after the mends; the message says why."""


def _next_significant(text: str, start: int) -> str:
    """The first character at or after ``start`` that is not white space, or
    ``""`` at the end of ``text``. Scans by index: slicing copied the rest of
    the text on every call, quadratic in the number of quotes."""
    for i in range(start, len(text)):
        if not text[i].isspace():
            return text[i]
    return ""


def mend(text: str) -> str:
    r"""``text`` with the slips :mod:`fcrm_agents.json_text` describes mended.

    One pass that tracks whether it is inside a string:

    - inside a string, a backslash before a character JSON cannot escape is
      dropped (``\'`` becomes ``'``);
    - inside a string, a ``"`` that is not followed by ``,`` ``}`` ``]`` ``:``
      or the end of the text is the model's own quotation mark and is escaped;
    - outside a string, a ``,`` followed by ``]`` or ``}`` is dropped.

    A ``"`` that *is* followed by one of those closers is taken as the end of
    the string even when the model meant it as a quotation mark
    (``"customer", as defined``); the text then fails to decode, which is the
    safe outcome.
    """
    out: list[str] = []
    in_string = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if in_string:
            if ch == "\\":
                nxt = text[i + 1] if i + 1 < n else ""
                if nxt in _ESCAPES and nxt:
                    out.append(ch + nxt)
                    i += 2
                    continue
                i += 1  # an escape JSON does not have: keep the character only
                continue
            if ch == '"':
                if _next_significant(text, i + 1) in _CLOSERS | {""}:
                    in_string = False
                    out.append(ch)
                else:
                    out.append('\\"')
                i += 1
                continue
            out.append(ch)
            i += 1
            continue
        if ch == '"':
            in_string = True
        elif ch == "," and _next_significant(text, i + 1) in ("]", "}"):
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def looks_like_json(text: str) -> bool:
    """Whether ``text`` starts like a JSON array or object (or a fenced one), so
    a failure to decode it is a broken answer rather than plain prose."""
    head = text.lstrip()
    return head.startswith(("[", "{", "```"))


def loads_text(text: str) -> Any:
    """``text`` decoded as JSON, accepting raw control characters inside
    strings (``strict=False``) and, failing that, the mends of :func:`mend`.

    Raises:
        JSONTextError: Neither the text nor its mended form is JSON. The message
            is the decoder's reason for the text as sent (line, column and what
            it expected), which is what a retry needs to be shown.
    """
    try:
        return json.loads(text, strict=False)
    except json.JSONDecodeError as exc:
        first_error = exc
    fenced = _FENCE.match(text)
    body = fenced.group("body") if fenced is not None else text
    for attempt in dict.fromkeys((body, mend(body))):
        try:
            return json.loads(attempt, strict=False)
        except json.JSONDecodeError:
            continue
    raise JSONTextError(
        f"the list was sent as text, not as a JSON array, and the text is not "
        f"valid JSON ({first_error.msg}: line {first_error.lineno} column "
        f"{first_error.colno}); send an array of objects, not a string"
    ) from first_error
