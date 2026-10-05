"""``Idempotency-Key`` on S5's service ``POST`` routes (design A §1.4).

H1 calls S5 from steps that may run twice (ADR-28), so every service write
(build a pack, supersede, confirm a re-vote, record an outcome) honours the
header. A key binds to one request: a repeat of it answers what the first
answered and writes nothing; the same key on a *different* request is a 409.
The key row is written in the same transaction as the state change, so a key
never exists without its effect or the reverse.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, Final, Optional

from fastapi import Header
from fcrm_common.errors import ValidationError
from fcrm_common.time import utcnow
from pydantic import BaseModel

from fcrm_decision.errors import IdempotencyKeyReusedError
from fcrm_decision.packs import canonical_json
from fcrm_decision.rows import KeyRow
from fcrm_decision.store import DecisionTx

MAX_KEY_LENGTH: Final = 255
#: Printable ASCII with no whitespace: what ``deterministic_id`` and a UUID give.
_KEY_PATTERN: Final = re.compile(r"^[\x21-\x7e]+$")

IdempotencyKeyHeader = Annotated[Optional[str], Header(alias="Idempotency-Key")]


def checked_key(raw: Optional[str]) -> Optional[str]:
    """``raw`` when it is a usable key, ``None`` when the header is absent.

    Raises:
        ValidationError: The header is empty, over :data:`MAX_KEY_LENGTH`, or
            holds whitespace or non-ASCII characters (a 422, not a stored key
            nobody can type).
    """
    if raw is None:
        return None
    if not raw or len(raw) > MAX_KEY_LENGTH or _KEY_PATTERN.match(raw) is None:
        raise ValidationError(
            "Idempotency-Key must be 1 to 255 printable ASCII characters "
            "with no spaces.",
            errors=[{"loc": "header.Idempotency-Key", "msg": "malformed key"}],
        )
    return raw


def fingerprint(kind: str, target: str, body: BaseModel) -> str:
    """sha256 of the request's identity: its kind, the resource it targets and
    its canonical body.
    """
    payload = {"kind": kind, "target": target, "body": body.model_dump(mode="json")}
    return hashlib.sha256(canonical_json(payload)).hexdigest()


async def once(
    tx: DecisionTx,
    *,
    key: Optional[str],
    kind: str,
    print_: str,
    produce: Callable[[], Awaitable[dict[str, Any]]],
    case_id: Optional[str] = None,
) -> tuple[dict[str, Any], bool]:
    """Run ``produce`` unless ``key`` already ran this request.

    Args:
        tx: The open transaction; the key row is written through it.
        key: The checked ``Idempotency-Key``, or ``None`` (always produce).
        kind: Which operation this is (``pack.build``, ``pack.supersede``...).
        print_: The request's :func:`fingerprint`.
        produce: Does the work and returns its JSON-able result, which is
            stored beside the key and returned verbatim on a replay.
        case_id: The case the request is about, stored with the key.

    Returns:
        ``(result, replayed)``.

    Raises:
        IdempotencyKeyReusedError: ``key`` belongs to another request.
    """
    if key is not None:
        known = await tx.get_key(key)
        if known is not None:
            if known.kind != kind or known.fingerprint != print_:
                raise IdempotencyKeyReusedError(
                    "This Idempotency-Key was already used for a different request."
                )
            return known.result, True
    result = await produce()
    if key is not None:
        await tx.put_key(
            KeyRow(
                key=key,
                kind=kind,
                fingerprint=print_,
                result=result,
                at=utcnow(),
                case_id=case_id,
            )
        )
    return result, False
