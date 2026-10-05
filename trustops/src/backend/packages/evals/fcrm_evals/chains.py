"""Audit chains that critic and examiner record refs resolve against.

Two sources, both built by the real ``fcrm_audit.chain`` code (never a
hand-written list of record numbers):

- The demo case FCRM-2026-0151: ``fcrm_audit.seedmodel.build_demo_chain``
  replays the seed's 67 ``demo.audit`` entries through ``build_record``.
- Any other seed case: :func:`synthetic_case_chain` builds a short,
  deterministic five-record chain (created, submitted, research proposal,
  assessment proposal, score computed) from that case's seed fields, again
  through ``build_record``. The seed carries no audit trail for the other
  79 cases, so this chain is synthesized; it exists so critic planted-error
  rows have real record numbers to cite. Design B §7.2 plans the full
  version (replaying every seed case through H1 in ``--seed-replay`` mode)
  once H1's audit map lands.

Every chain is verified with ``verify_chain`` before a metric may resolve
a ref against it; a chain that does not verify resolves nothing.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from fcrm_audit.chain import GENESIS_HASH, build_record, verify_chain
from fcrm_audit.seedmodel import build_demo_chain
from fcrm_contracts.api.audit import AuditRecord, RecordAppend
from fcrm_seed.loader import cases, demo_case

logger = logging.getLogger(__name__)

SYNTHETIC_RECORD_TYPES: Final[tuple[str, ...]] = (
    "case.created",
    "case.submitted",
    "proposal.research",
    "proposal.assessment",
    "score.computed",
)
"""The record types of a synthetic chain, in ``seq`` order (1-based)."""

ASSESSMENT_SEQ: Final[int] = SYNTHETIC_RECORD_TYPES.index("proposal.assessment") + 1


def _control_count(case: Mapping[str, Any]) -> int:
    return sum(len(area["controls"]) for area in case["inputs"].values())


def _summaries(case: Mapping[str, Any]) -> list[tuple[str, str, str]]:
    score = case["score"]
    return [
        ("case.created", case["owner"], f"Case created for {case['title']}"),
        ("case.submitted", case["owner"], "Submission v1 received"),
        ("proposal.research", "ai", "Research agent · clauses and precedents"),
        (
            "proposal.assessment",
            "ai",
            f"Assessment agent · L/I per area · {_control_count(case)} controls rated",
        ),
        (
            "score.computed",
            "S2",
            f"Composite {score['composite']} · Band {score['band']}",
        ),
    ]


def synthetic_case_chain(case: Mapping[str, Any]) -> list[AuditRecord]:
    """A deterministic five-record chain for a non-demo seed case (see the
    module docstring). Timestamps are synthetic, one second apart from the
    case's ``submitted`` date at 09:00 UTC."""
    base = datetime.fromisoformat(f"{case['submitted']}T09:00:00").replace(tzinfo=UTC)
    records: list[AuditRecord] = []
    prev_hash = GENESIS_HASH
    for seq, (kind, actor, summary) in enumerate(_summaries(case), start=1):
        append = RecordAppend(
            case_id=case["id"], type=kind, actor=actor, summary=summary
        )
        record = build_record(
            append,
            seq=seq,
            prev_hash=prev_hash,
            created_at=base + timedelta(seconds=seq),
        )
        records.append(record)
        prev_hash = record.hash
    return records


def demo_chain() -> list[AuditRecord]:
    """The demo case's 67-record chain, replayed from the seed."""
    return build_demo_chain(demo_case())


def chain_for(case_id: str) -> list[AuditRecord]:
    """The chain records of ``case_id`` resolve against.

    Raises:
        KeyError: ``case_id`` is not a seed case.
    """
    demo = demo_case()
    if case_id == demo["id"]:
        return demo_chain()
    by_id = {case["id"]: case for case in cases()}
    return synthetic_case_chain(by_id[case_id])


def verified_seqs(case_id: str, records: list[AuditRecord]) -> frozenset[int]:
    """The ``seq`` values of ``records`` if the chain verifies end to end,
    otherwise the empty set (a broken chain grounds nothing)."""
    result = verify_chain(case_id, records)
    if not result.ok:
        logger.error(
            "audit chain for %s does not verify (first break at seq %s); "
            "no record ref will resolve against it",
            case_id,
            result.first_break_seq,
        )
        return frozenset()
    return frozenset(record.seq for record in records)
