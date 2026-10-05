"""Identifier minting (design A §1.4).

Entity ids are ``<prefix>_<ULID>``: sortable by creation time to the
millisecond, URL-safe, 26 Crockford base32 characters after the prefix.
Prefixes in use: ``evt`` (events), ``job``, ``run``, ``gate``, ``pack``,
``thr`` (threads), ``cnd`` (conditions), ``doc``, ``sv`` (submission
versions), ``itm`` (review items), ``prp`` (proposals), ``relay``.

Case ids are the human-facing ``FCRM-<year>-<seq>`` form from a per-year
counter (``counters`` collection in S1), e.g. ``FCRM-2026-0152``.

Ids that must be *the same* on every attempt of the same unit of work
(a harness step, the gates a step opens) come from :func:`deterministic_id`
instead, so a redelivered message re-derives the id it already used.
"""

from __future__ import annotations

import base64
import hashlib
import re

from ulid import ULID

_PREFIX = re.compile(r"^[a-z][a-z0-9]{0,15}$")
_PART_SEPARATOR = "|"
_DIGEST_BYTES = 16
CASE_ID_PATTERN = re.compile(r"^FCRM-(\d{4})-(\d{4})$")


def new_id(prefix: str) -> str:
    """``prefix + "_" + ULID``, e.g. ``new_id("evt")`` -> ``evt_01K6...``.

    Raises:
        ValueError: ``prefix`` is not 1-16 lowercase letters/digits
            starting with a letter (so ids stay parseable on ``_``).
    """
    if not _PREFIX.match(prefix):
        raise ValueError(f"invalid id prefix {prefix!r}: use [a-z][a-z0-9]{{0,15}}")
    return f"{prefix}_{ULID()}"


def deterministic_id(prefix: str, *parts: str) -> str:
    """``prefix + "_" + base32(sha256("|".join(parts))[:16])``, lowercase, no
    padding (26 characters after the prefix), e.g.
    ``deterministic_id("gate", run_id, "review_item", "CTL-T01")``.

    The same inputs always give the same id and different inputs give
    different ids (a 128-bit digest), so a retried or redelivered unit of
    work derives the id it used the first time: Caseflow's step ids and the
    ids of the gates a step opens rely on this to make a second run a no-op.

    Args:
        prefix: 1-16 lowercase letters/digits starting with a letter.
        *parts: The values that identify the unit of work. At least one; an
            empty string is allowed. A part may not contain ``|``, the
            separator, because ``("a|b", "c")`` and ``("a", "b|c")`` would
            otherwise hash alike and silently collide.

    Raises:
        ValueError: ``prefix`` is malformed, no part was given, or a part
            contains the separator.
    """
    if not _PREFIX.match(prefix):
        raise ValueError(f"invalid id prefix {prefix!r}: use [a-z][a-z0-9]{{0,15}}")
    if not parts:
        raise ValueError("deterministic_id needs at least one part")
    for part in parts:
        if _PART_SEPARATOR in part:
            raise ValueError(
                f"id part {part!r} contains {_PART_SEPARATOR!r}, the part separator"
            )
    digest = hashlib.sha256(_PART_SEPARATOR.join(parts).encode("utf-8")).digest()
    token = base64.b32encode(digest[:_DIGEST_BYTES]).decode("ascii")
    return f"{prefix}_{token.rstrip('=').lower()}"


def mint_case_id(year: int, seq: int) -> str:
    """``FCRM-<year>-<seq:04d>``, e.g. ``mint_case_id(2026, 152)`` ->
    ``FCRM-2026-0152``.

    Raises:
        ValueError: ``year`` is not four digits or ``seq`` is outside
            1-9999 (a fifth digit would break the id's fixed width).
    """
    if not 1000 <= year <= 9999:
        raise ValueError(f"case id year must have four digits, got {year}")
    if not 1 <= seq <= 9999:
        raise ValueError(f"case id sequence must be 1-9999, got {seq}")
    return f"FCRM-{year}-{seq:04d}"


def parse_case_id(case_id: str) -> tuple[int, int]:
    """``(year, seq)`` from ``FCRM-YYYY-NNNN``.

    Raises:
        ValueError: ``case_id`` is not in that form.
    """
    match = CASE_ID_PATTERN.match(case_id)
    if match is None:
        raise ValueError(f"not a case id: {case_id!r}")
    return int(match.group(1)), int(match.group(2))
