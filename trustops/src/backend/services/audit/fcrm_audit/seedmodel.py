"""Seed trails for S4 (ADR-16, design A §11.3).

Two things live here, with different jobs:

:func:`build_closed_case_chains` is **live**: at startup, on an empty
``audit_records`` collection, S4 seeds a minimal nine-record hash chain for each
of the 65 closed seed cases, so ``GET /verify`` and the examiner have real data
on cases that closed before the demo. The demo case FCRM-2026-0151 is **not**
seeded: its 67 records are the live demo's own output.

:func:`build_demo_chain` is a test and golden-check helper only, **not** used by
the live service. It replays the seed's recorded ``type``/``by``/``what``
narration of the demo's 67 records through the real chain-building function, so
the hash-chain logic can be exercised against a realistic sequence and ``make
demo`` has an expected record-type sequence to compare the live run against.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any, Final

from fcrm_contracts.api.audit import AuditRecord, RecordAppend
from fcrm_seed.loader import platform_outcome

from fcrm_audit.chain import GENESIS_HASH, build_record

CLOSED_CASE_RECORD_TYPES: Final[tuple[str, ...]] = (
    "case.created",
    "workflow.started",
    "case.submitted",
    "triage.assigned",
    "score.computed",
    "recommendation.locked",
    "outcome.set",
    "case.closed",
    "chain.verified",
)
"""The nine record types of a seeded closed case's trail, in order."""

SYSTEM_ACTOR: Final = "system"


class SeedChainError(ValueError):
    """A seed case cannot be turned into a coherent trail (a date or a field
    the trail needs is missing or out of order)."""


def _at(day: str, hour: int, minute: int = 0, second: int = 0) -> datetime:
    parsed = date.fromisoformat(day)
    return datetime(
        parsed.year, parsed.month, parsed.day, hour, minute, second, tzinfo=UTC
    )


def _timeline(case: Mapping[str, Any]) -> list[datetime]:
    """One timestamp per record type, whole seconds (Mongo keeps milliseconds,
    so a hashed value never loses precision on the way in) and never going
    backwards, even for a case decided on the day it was submitted.
    """
    created = _at(case["created"], 9)
    submitted = _at(case["submitted"], 15)
    decided = _at(case["decided"], 18)
    second = timedelta(seconds=1)
    times = [
        created,
        created + second,
        submitted,
        submitted + second,
        _at(case["submitted"], 16),
        decided,
        decided + timedelta(minutes=1),
        decided + timedelta(minutes=2),
        decided + timedelta(minutes=2, seconds=1),
    ]
    if times != sorted(times):
        raise SeedChainError(f"{case['id']}: dates out of order {times}")
    return times


def _appends(case: Mapping[str, Any]) -> list[RecordAppend]:
    """The nine records of ``case``'s trail, before hashing. ``data`` carries the
    case's own score, band and outcome, and marks the record as seed data.
    """
    case_id: str = case["id"]
    owner: str = case["owner"]
    analyst: str = case["analyst"]
    score = case["score"]
    outcome = platform_outcome(case["outcome"])
    mode = "committee" if score["route"] == "committee" else "delegated"
    seeded = {"seeded": True}
    spec: list[tuple[str, str, str, dict[str, Any]]] = [
        (
            owner,
            f"Case created: {case['title']}",
            "case.created",
            {
                "unit": case["unit"],
                "change_types": list(case["change_types"]),
                "owner": owner,
            },
        ),
        (SYSTEM_ACTOR, "Assessment workflow started", "workflow.started", {}),
        (
            owner,
            "Case submitted for assessment",
            "case.submitted",
            {"submitted": case["submitted"]},
        ),
        (
            SYSTEM_ACTOR,
            f"Assigned to {analyst} (lowest load)",
            "triage.assigned",
            {"analyst": analyst, "rule": "R5"},
        ),
        (
            SYSTEM_ACTOR,
            f"Composite {score['composite']}, band {score['band']}",
            "score.computed",
            {
                "composite": score["composite"],
                "band": score["band"],
                "route": score["route"],
                "boundary_flag": score["boundary_flag"],
            },
        ),
        (
            analyst,
            f"Recommendation locked: {outcome}",
            "recommendation.locked",
            {"outcome": outcome, "band": score["band"]},
        ),
        (
            analyst if mode == "delegated" else SYSTEM_ACTOR,
            f"Outcome set: {outcome} ({mode})",
            "outcome.set",
            {
                "outcome": outcome,
                "mode": mode,
                "band": score["band"],
                "composite": score["composite"],
            },
        ),
        (
            analyst,
            "Case closed",
            "case.closed",
            {"outcome": outcome, "decided": case["decided"]},
        ),
        (
            SYSTEM_ACTOR,
            "Audit chain verified (8 records)",
            "chain.verified",
            {"records": len(CLOSED_CASE_RECORD_TYPES) - 1},
        ),
    ]
    return [
        RecordAppend(
            case_id=case_id,
            type=record_type,
            actor=actor,
            summary=summary,
            data={**data, **seeded},
        )
        for (actor, summary, record_type, data) in spec
    ]


def build_closed_case_chain(case: Mapping[str, Any]) -> list[AuditRecord]:
    """The nine-record hash-chained trail of one closed seed case.

    Raises:
        SeedChainError: The case's dates are out of order.
        KeyError: The case lacks a field the trail needs (it is not a closed
            seed case).
    """
    records: list[AuditRecord] = []
    prev_hash = GENESIS_HASH
    for seq, (append, at) in enumerate(
        zip(_appends(case), _timeline(case), strict=True), start=1
    ):
        record = build_record(append, seq=seq, prev_hash=prev_hash, created_at=at)
        records.append(record)
        prev_hash = record.hash
    return records


def build_closed_case_chains(cases: Sequence[Mapping[str, Any]]) -> list[AuditRecord]:
    """The trails of every closed case in ``cases``, case by case in seed order
    (cases at any other stage, including the demo case, get none).
    """
    records: list[AuditRecord] = []
    for case in cases:
        if case.get("stage") == "closed":
            records.extend(build_closed_case_chain(case))
    return records


def build_demo_chain(demo: dict[str, Any]) -> list[AuditRecord]:
    """Replay ``demo["audit"]`` (the 67-entry ``{n, type, by, what, scene}``
    list from the seed) through :func:`fcrm_audit.chain.build_record` and
    return the resulting chain. ``created_at`` values are synthetic
    (one second apart) since the seed does not carry real timestamps per
    record, only relative ordering matters for the chain.
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
