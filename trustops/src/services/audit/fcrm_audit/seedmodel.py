"""Test and golden-check helper only — NOT used by the live service.

Design A §11.3 is explicit that the demo case's 67 audit records are the
*live* output of actually running the demo through the API (`make demo`'s
golden), never seeded data. This module exists so the hash-chain logic
itself (``fcrm_audit.chain``) can be exercised against a realistic 67-record
sequence today, before H1/the services that would produce those records
live are built — it replays the seed's recorded ``type``/``by``/``what``
narration through the real chain-building function and checks the result
verifies, which is a genuine test of the chain code, not a shortcut around
building the live path later.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fcrm_contracts.api.audit import AuditRecord, RecordAppend

from fcrm_audit.chain import GENESIS_HASH, build_record


def build_demo_chain(demo: dict[str, Any]) -> list[AuditRecord]:
    """Replay ``demo["audit"]`` (the 67-entry ``{n, type, by, what, scene}``
    list from the seed) through :func:`fcrm_audit.chain.build_record` and
    return the resulting chain. ``created_at`` values are synthetic
    (one second apart) since the seed does not carry real timestamps per
    record — only relative ordering matters for the chain.
    """
    case_id = demo["id"]
    base_time = datetime(2026, 10, 1, 9, 0, 0, tzinfo=UTC)
    records: list[AuditRecord] = []
    prev_hash = GENESIS_HASH

    for entry in demo["audit"]:
        append = RecordAppend(
            case_id=case_id,
            type=entry["type"],
            actor=entry["by"],
            summary=entry["what"],
            data={"scene": entry["scene"]},
        )
        created_at = base_time + timedelta(seconds=entry["n"])
        record = build_record(
            append, seq=entry["n"], prev_hash=prev_hash, created_at=created_at
        )
        records.append(record)
        prev_hash = record.hash

    return records
