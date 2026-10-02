"""Frozen pack snapshots.

Design A §2.5 / §3.6: a pack is built once from S1's locked view and S4's
ledger head, and is never recomputed from live data while votes are being
cast — "the pack never shows the analyst's own Approve/Reject
recommendation" and "the analyst can still change it during voting" were
both verified defects in the prototype (docs/requirements/research/
04-prototype-fcrm-v2-notes.md). ``content_hash`` is what lets a later
check ("did the pack a committee member voted on match the pack now
displayed?") be a simple equality check rather than a deep diff.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict


def canonical_json(obj: Any) -> bytes:
    """Deterministic serialisation: sorted keys, no whitespace, UTF-8 —
    the same function fcrm_audit.chain uses, duplicated here rather than
    imported (a service must not depend on another service's package;
    both will move to fcrm_common.hashing once it exists, per BUILD-PLAN
    §5's common-first plan).
    """
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def content_hash(snapshot: dict[str, Any]) -> str:
    """SHA-256 of the pack's canonical content. Two packs built from
    identical inputs hash identically; the faintest edit to the
    recommendation, a condition, a locked score or a pinned version
    changes it.
    """
    return hashlib.sha256(canonical_json(snapshot)).hexdigest()


class PackSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    version: int
    content: dict[str, Any]
    content_hash: str


def build_pack(case_id: str, version: int, content: dict[str, Any]) -> PackSnapshot:
    """Freeze ``content`` (S1's locked case view plus S4's ledger head, by
    the time the real route calls this) into a versioned, hashed
    snapshot. Calling this twice with the same ``content`` for the same
    ``case_id``/``version`` always produces the same ``content_hash`` —
    this function has no side effect and reads no clock.
    """
    return PackSnapshot(
        case_id=case_id,
        version=version,
        content=content,
        content_hash=content_hash(content),
    )


def pack_unchanged(pack: PackSnapshot, current_content: dict[str, Any]) -> bool:
    """Used before revealing votes: if the live content no longer hashes
    to what members actually voted on, the vote must not be revealed as
    valid against today's numbers — the caller should require a pack v2
    and a re-vote (R6) instead.
    """
    return pack.content_hash == content_hash(current_content)
