"""Which field a question asks about, when the question does not say (D-128).

The intake agent writes a ``GapQuestion`` per required field it found no value
for, with the field's id. A cheap model sometimes leaves the id out ("Who is the
change owner?" with no ``field_id``), and the owner was then asked twice: once
by that question and once by the field's own question (F01). Code ties such a
question back to its field, in H1 when it merges the questions and in S1 when it
shows and answers them, by the same rule:

1. a field id written in the question ("... (F30)") that is one of ``labels``;
2. else the field whose whole label appears in the question, as words
   ("product sign-off reference" in "What is the product sign-off reference?");
   the longest label wins, and two different fields with labels of the same
   length are a tie, so neither is chosen.

A one-word label ("Geography") is matched by its id only: one word appears in
too many questions to tell a field. Pure, no I/O.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Final, Optional

_WORD: Final = re.compile(r"[a-z0-9]+")
_FIELD_ID: Final = re.compile(r"\b[A-Z]\d{2,3}\b")


def _words(text: str) -> list[str]:
    """``text`` as lower-case words (punctuation and hyphens split words)."""
    return _WORD.findall(text.casefold())


def field_for_question(text: str, labels: Mapping[str, str]) -> Optional[str]:
    """The id of the field ``text`` asks about, from ``labels`` (field id ->
    label), or ``None`` when no field, or more than one equally, matches.
    """
    named: set[str] = {str(m) for m in _FIELD_ID.findall(text) if m in labels}
    if len(named) == 1:
        return named.pop()
    padded = f" {' '.join(_words(text))} "
    best: Optional[str] = None
    best_length = 0
    tied = False
    for field_id, label in labels.items():
        words = _words(label)
        if len(words) < 2:
            continue
        phrase = " ".join(words)
        if f" {phrase} " not in padded:
            continue
        if len(phrase) > best_length:
            best, best_length, tied = field_id, len(phrase), False
        elif len(phrase) == best_length and field_id != best:
            tied = True
    return None if tied else best
