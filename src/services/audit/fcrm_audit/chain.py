r"""The hash chain.

Format (design A §2.4, exact): ``hash = sha256(prev_hash + "\n" +
canonical_body)``, where ``canonical_body`` is the record's content
serialised with sorted keys and no incidental whitespace, so the same
logical record always hashes identically regardless of field insertion
order. The first record in a case's chain uses the fixed genesis hash
(64 zero characters) as its ``prev_hash``.

Every function here is pure: given the same inputs, the same hash, every
time, on every machine. No module in this file touches Mongo, a clock, or
anything non-deterministic except via the caller supplying ``created_at``
and ``seq`` explicitly.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from fcrm_contracts.api.audit import AuditRecord, RecordAppend, VerifyResult

GENESIS_HASH = "0" * 64


def canonical_json(obj: Any) -> bytes:
    """Deterministic serialisation: sorted keys, no whitespace, UTF-8.

    This must never change once any record has been written with it — a
    change here would make every previously-computed hash unreproducible.
    If the canonical form ever needs to change, it is a new hash *scheme*
    version, not a silent edit to this function.
    """
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    """Hex-encoded SHA-256 digest of ``data``."""
    return hashlib.sha256(data).hexdigest()


def record_body(record: RecordAppend, seq: int, created_at: datetime) -> dict[str, Any]:
    """The exact fields that go into the hash. Excludes ``prev_hash`` and
    ``hash`` themselves (obviously) and is otherwise everything a verifier
    needs to reproduce the record's content independently.
    """
    return {
        "case_id": record.case_id,
        "seq": seq,
        "type": record.type,
        "actor": record.actor,
        "summary": record.summary,
        "data": record.data,
        "created_at": created_at.isoformat(),
    }


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
    a record with the wrong ``prev_hash`` — is caught here and reported as
    the first broken ``seq``, matching ``GET /verify/{case}``'s contract.

    Records must already be sorted by ``seq`` ascending; this function
    does not sort them, so a caller that hands it an out-of-order list
    will get a (correct) verification failure at the first out-of-order
    position rather than a silent re-sort.
    """
    expected_prev = GENESIS_HASH
    for i, record in enumerate(records):
        if record.seq != i + 1:
            return VerifyResult(
                case_id=case_id,
                record_count=len(records),
                ok=False,
                first_break_seq=record.seq,
            )
        if record.prev_hash != expected_prev:
            return VerifyResult(
                case_id=case_id,
                record_count=len(records),
                ok=False,
                first_break_seq=record.seq,
            )
        body = {
            "case_id": record.case_id,
            "seq": record.seq,
            "type": record.type,
            "actor": record.actor,
            "summary": record.summary,
            "data": record.data,
            "created_at": record.created_at.isoformat(),
        }
        recomputed = chain_hash(record.prev_hash, body)
        if recomputed != record.hash:
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
