r"""The hash chain.

Format (design A §2.4, exact): ``hash = sha256(prev_hash + "\n" +
canonical_body)``, where ``canonical_body`` is the record's content
serialised with sorted keys and no incidental whitespace, so the same
logical record always hashes identically regardless of field insertion
order. The first record in a case's chain uses the fixed genesis hash
(64 zero characters) as its ``prev_hash``.

The body that is hashed is the record's **JSON-mode representation** --
exactly what ``GET /records/{case_id}`` serves (Pydantic
``model_dump(mode="json")``: ``created_at`` as ``2026-10-01T09:00:01Z``,
never ``+00:00``) -- so a third party can recompute every ``hash`` from
the served JSON alone, with nothing but ``sha256`` and a sorted-key JSON
encoder (:func:`hashed_body`, :func:`canonical_json`).

Every function here is pure: given the same inputs, the same hash, every
time, on every machine. No module in this file touches Mongo, a clock, or
anything non-deterministic except via the caller supplying ``created_at``
and ``seq`` explicitly.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Final

from fcrm_contracts.api.audit import AuditRecord, RecordAppend, VerifyResult
from pydantic import BaseModel, ConfigDict

GENESIS_HASH = "0" * 64

#: The ``AuditRecord`` fields that enter the hash: everything except
#: ``prev_hash`` and ``hash`` themselves.
HASHED_FIELDS: Final[frozenset[str]] = frozenset(
    {"case_id", "seq", "type", "actor", "summary", "data", "created_at"}
)


class _HashedBody(BaseModel):
    """The hashed subset of an ``AuditRecord``, with the same field types,
    so one Pydantic serialiser produces the body for building and for
    verifying a record alike. Internal: never leaves this module.
    """

    model_config = ConfigDict(extra="forbid")

    case_id: str
    seq: int
    type: str
    actor: str
    summary: str
    data: dict[str, Any]
    created_at: datetime


def canonical_json(obj: Any) -> bytes:
    """Deterministic serialisation: sorted keys, no whitespace, UTF-8 with
    non-ASCII emitted as-is, no ``NaN``/``Infinity``.

    This must never change once any record has been written with it — a
    change here would make every previously-computed hash unreproducible.
    If the canonical form ever needs to change, it is a new hash *scheme*
    version, not a silent edit to this function.

    Raises:
        ValueError: ``obj`` holds a non-finite float (no JSON form) or a
            string with a lone UTF-16 surrogate (not UTF-8 encodable).
            ``RecordAppend`` already rejects both at the contract, so this
            is the last line of defence, not the first.
    """
    try:
        text = json.dumps(
            obj,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except ValueError as exc:
        msg = f"canonical_json rejects non-finite floats (NaN/Infinity): {exc}"
        raise ValueError(msg) from exc
    try:
        return text.encode("utf-8")
    except UnicodeEncodeError as exc:
        msg = (
            "canonical_json rejects strings that are not UTF-8 encodable "
            f"(lone UTF-16 surrogate at offset {exc.start})"
        )
        raise ValueError(msg) from exc


def sha256_hex(data: bytes) -> str:
    """Hex-encoded SHA-256 digest of ``data``."""
    return hashlib.sha256(data).hexdigest()


def hashed_body(record: AuditRecord) -> dict[str, Any]:
    """The JSON-mode body of a stored or served record: the
    :data:`HASHED_FIELDS` of ``record`` exactly as the API serialises them,
    so ``chain_hash(record.prev_hash, hashed_body(record)) == record.hash``
    holds for every intact record.
    """
    return _HashedBody(
        case_id=record.case_id,
        seq=record.seq,
        type=record.type,
        actor=record.actor,
        summary=record.summary,
        data=record.data,
        created_at=record.created_at,
    ).model_dump(mode="json")


def record_body(record: RecordAppend, seq: int, created_at: datetime) -> dict[str, Any]:
    """The exact fields that go into the hash for a record about to be
    appended. Excludes ``prev_hash`` and ``hash`` themselves (obviously)
    and is otherwise everything a verifier needs to reproduce the record's
    content independently -- the same JSON-mode body :func:`hashed_body`
    derives from the stored record afterwards.
    """
    return _HashedBody(
        case_id=record.case_id,
        seq=seq,
        type=record.type,
        actor=record.actor,
        summary=record.summary,
        data=record.data,
        created_at=created_at,
    ).model_dump(mode="json")


def chain_hash(prev_hash: str, body: dict[str, Any]) -> str:
    r"""``sha256(prev_hash + "\n" + canonical_body)`` — the one formula
    every record in every case's chain is built from.
    """
    payload = prev_hash.encode("utf-8") + b"\n" + canonical_json(body)
    return sha256_hex(payload)


def build_record(
    append: RecordAppend, *, seq: int, prev_hash: str, created_at: datetime
) -> AuditRecord:
    """Build the next record in a chain. The caller (the repository layer)
    is responsible for supplying the correct ``seq`` (one more than the
    case's last record) and ``prev_hash`` (the previous record's ``hash``,
    or :data:`GENESIS_HASH` for the first record) atomically — this
    function does not read or write storage, so it cannot by itself
    prevent a race between two concurrent appends; that is enforced by a
    unique ``{case_id, seq}`` index at the database layer (design A §11.1
    mongo-init ``04-indexes.js``), which turns a race into a duplicate-key
    error rather than a silent corruption.
    """
    body = record_body(append, seq, created_at)
    record_hash = chain_hash(prev_hash, body)
    return AuditRecord(
        case_id=append.case_id,
        seq=seq,
        type=append.type,
        actor=append.actor,
        summary=append.summary,
        data=append.data,
        prev_hash=prev_hash,
        hash=record_hash,
        created_at=created_at,
    )


def verify_chain(case_id: str, records: list[AuditRecord]) -> VerifyResult:
    """Recompute every hash in sequence and compare against what is
    stored. Any mismatch — a tampered ``data`` field, a reordered record,
    a record with the wrong ``prev_hash``, a record that belongs to another
    case — is caught here and reported as the first broken ``seq``,
    matching ``GET /verify/{case}``'s contract.

    Records must already be sorted by ``seq`` ascending; this function
    does not sort them, so a caller that hands it an out-of-order list
    will get a (correct) verification failure at the first out-of-order
    position rather than a silent re-sort.
    """
    expected_prev = GENESIS_HASH
    for i, record in enumerate(records):
        broken = (
            record.case_id != case_id
            or record.seq != i + 1
            or record.prev_hash != expected_prev
            or chain_hash(record.prev_hash, hashed_body(record)) != record.hash
        )
        if broken:
            return VerifyResult(
                case_id=case_id,
                record_count=len(records),
                ok=False,
                first_break_seq=record.seq,
            )
        expected_prev = record.hash

    return VerifyResult(
        case_id=case_id, record_count=len(records), ok=True, first_break_seq=None
    )
