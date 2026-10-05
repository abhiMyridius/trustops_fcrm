r"""Checks that a value can be carried losslessly as JSON and hashed.

Used by contracts whose content is hash-chained or re-served verbatim (S4's
``RecordAppend``): a string holding a lone UTF-16 surrogate (``"\ud800"``,
which ``json.loads`` happily produces) cannot be UTF-8 encoded, and a
``NaN``/``Infinity`` float has no JSON representation at all -- either would
otherwise surface as a 500 deep inside the hash step, or be hashed as one
token and served as another. Rejecting them at the contract turns both into
an ordinary 422 from Pydantic.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any


def _printable(text: str) -> str:
    r"""``text`` with any unencodable code point shown as ``\udXXX`` so the
    error message itself is always UTF-8 encodable."""
    return text.encode("utf-8", "backslashreplace").decode("utf-8")


def _check_text(text: str, path: str) -> None:
    try:
        text.encode("utf-8")
    except UnicodeEncodeError as exc:
        msg = f"{path} contains a lone UTF-16 surrogate and cannot be UTF-8 encoded"
        raise ValueError(msg) from exc


def assert_json_safe(value: Any, path: str = "$") -> None:
    """Raise ``ValueError`` naming ``path`` when ``value`` (walked recursively
    through mappings and sequences) holds a string that is not UTF-8
    encodable or a float that is not finite. Keys are checked like values.
    """
    if isinstance(value, str):
        _check_text(value, path)
    elif isinstance(value, float):
        if not math.isfinite(value):
            msg = f"{path} is {value!r}, which has no JSON representation"
            raise ValueError(msg)
    elif isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str):
                child = f"{path}.{_printable(key)}"
                _check_text(key, child)
            else:
                child = f"{path}[{key!r}]"
            assert_json_safe(item, child)
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        for index, item in enumerate(value):
            assert_json_safe(item, f"{path}[{index}]")
