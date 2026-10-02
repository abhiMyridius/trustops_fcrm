r"""Canonical JSON and the audit hash chain (design A §1.4, §2.4.1).

S4 computes, for each audit record::

    body = canonical_json({case_id, seq, run_id, type, actor, what,
                           payload, occurred_at, trace_id, versions})
    hash = sha256_hex(prev_hash + "\n" + body)

with ``prev_hash = genesis_hash(case_id)`` for the first record. The
chain is only verifiable if ``canonical_json`` produces the same bytes for
the same logical value on every run, every process and every Python
version, so the encoding is fully specified here instead of relying on
``json.dumps`` defaults:

- object keys sorted by code point; keys must be strings;
- no insignificant whitespace (``","`` and ``":"`` separators);
- UTF-8 output, non-ASCII characters emitted as-is (not ``\u`` escaped);
- ``NaN``/``Infinity`` rejected;
- ``datetime`` must be tz-aware and is rendered in UTC as
  ``YYYY-MM-DDTHH:MM:SS.mmmZ`` (milliseconds, the record format in design
  A §2.4.1); ``date`` as ``YYYY-MM-DD``;
- ``Decimal`` is rendered as a JSON *string* in normalised fixed-point
  form (``Decimal("7.60")`` -> ``"7.6"``), because a JSON number would be
  re-read as a float and could not round-trip exactly;
- ``Enum`` members by value; Pydantic models via ``model_dump(mode="json")``
  then the rules above; tuples as arrays;
- sets, bytes and any other type raise ``TypeError`` (a set has no
  canonical order, and silently ``str()``-ing an object would make the
  hash depend on its ``repr``).
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel

GENESIS_PREFIX = "FCRM-GENESIS:"
HASH_ALG = "sha256-v1"


def _canonical_datetime(value: datetime) -> str:
    """UTC, millisecond precision, ``Z`` suffix."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise TypeError("canonical_json requires tz-aware datetimes")
    utc = value.astimezone(UTC)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond // 1000:03d}Z"


def _canonical_decimal(value: Decimal) -> str:
    """Normalised fixed-point text; rejects NaN and infinities."""
    if not value.is_finite():
        raise ValueError("canonical_json rejects non-finite Decimal values")
    text = format(value.normalize(), "f")
    return "0" if text in ("-0", "0") else text


def _scalar(value: Any) -> tuple[bool, Any]:
    """``(True, canonical)`` for a scalar the module encodes, else
    ``(False, None)``.
    """
    if value is None or isinstance(value, bool | str | int):
        return True, value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("canonical_json rejects NaN and infinities")
        return True, value
    if isinstance(value, datetime):
        return True, _canonical_datetime(value)
    if isinstance(value, date):
        return True, value.isoformat()
    if isinstance(value, Decimal):
        return True, _canonical_decimal(value)
    return False, None


def _to_canonical(value: Any) -> Any:
    """Convert ``value`` to plain JSON types under the module's rules."""
    if isinstance(value, Enum):  # before str/int: StrEnum is a str
        return _to_canonical(value.value)
    if isinstance(value, BaseModel):
        return _to_canonical(value.model_dump(mode="json"))
    handled, scalar = _scalar(value)
    if handled:
        return scalar
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"canonical_json keys must be str, got {type(key)}")
            out[key] = _to_canonical(item)
        return out
    if isinstance(value, list | tuple):
        return [_to_canonical(item) for item in value]
    raise TypeError(f"canonical_json cannot encode {type(value).__name__}")


def canonical_json(obj: Any) -> bytes:
    """The canonical UTF-8 JSON encoding of ``obj`` (see module docstring).

    Raises:
        TypeError: ``obj`` contains a value with no canonical form.
        ValueError: ``obj`` contains NaN or an infinity.
    """
    return json.dumps(
        _to_canonical(obj),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    """Lowercase hex SHA-256 of ``data`` (64 characters)."""
    return hashlib.sha256(data).hexdigest()


def chain_hash(prev_hash: str, record_body: bytes) -> str:
    r"""``sha256_hex(prev_hash + "\n" + record_body)``: the hash of an audit
    record given its predecessor's hash and its own canonical body.
    """
    return sha256_hex((prev_hash + "\n").encode("utf-8") + record_body)


def genesis_hash(case_id: str) -> str:
    """The ``prev_hash`` of a case's first record:
    ``sha256_hex("FCRM-GENESIS:" + case_id)``.
    """
    return sha256_hex((GENESIS_PREFIX + case_id).encode("utf-8"))
