"""A clause the analyst adds to a case from a policy-corpus search, and its
removal, as commands (ADR-74).

The analyst searched the corpus on the Policy tab and found a clause the research
agent did not propose. Adding it stores a ``clause`` review item that is
**accepted when it is added**: the analyst's reason is the decision, so the item
has no gate, never waits for one and never blocks the lock. What makes it the
analyst's is its subject: ``origin: "analyst"``, who added it, when and why. The
quote is the clause text S3 holds at the corpus version the case pinned, copied
word for word with its source reference (rule 8); nothing of the client's is
stored as policy text. A clause is never a score input, so nothing here reaches
the score request.

**Identity.** The item id is ``deterministic_id("acl", case, clause, n)`` where
``n`` counts the analyst's earlier additions of the same clause, so the same
addition twice is the same row (a replay that announces nothing), a clause the
analyst removed can be added again as a new row, and an analyst item can never
collide with an agent's (``itm`` ids).

**Removal.** The analyst may remove a clause they added, with a reason, through
the same ``POST items/{id}`` the agent's clauses use. The item has no gate, so
the removal is announced as ``case.clause_removed`` (not ``gate.decided``), and a
``clause_removals`` row keyed by the item id makes a second removal a replay.

Each command enqueues its ``case.*`` event in the same transaction as the item;
Caseflow writes the ``clause.added`` / ``clause.removed`` audit record.

Pure, no I/O.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Final, Optional

from fcrm_common.events import build_envelope
from fcrm_common.ids import deterministic_id
from fcrm_contracts.api.policy import ClauseDetailOut
from fcrm_contracts.enums import ItemKind, ItemStatus
from fcrm_contracts.events.payloads import CaseClauseAdded, CaseClauseRemoved

from fcrm_case.domain.analyst_commands import (
    AnalystCommand,
    OnceRecord,
    activity_row,
)
from fcrm_case.domain.assessment import current_items
from fcrm_case.domain.po_commands import ItemDecision
from fcrm_case.domain.views import DecisionRow, ReviewItemRow

#: ``subject.origin`` of a clause the analyst added (an agent's clause has none).
ANALYST_ORIGIN: Final = "analyst"
#: Where a removal of an analyst's clause is recorded once (``_id = item_id``).
CLAUSE_REMOVALS: Final = "clause_removals"
#: ``proposed.applicability`` of an analyst's clause: why it is on the case is the
#: analyst's reason, kept on the subject.
ANALYST_APPLICABILITY: Final = "added by the analyst from a corpus search"


def is_analyst_clause(row: ReviewItemRow) -> bool:
    """Whether ``row`` is a clause the analyst added (ADR-74)."""
    return row.kind is ItemKind.CLAUSE and row.subject.get("origin") == ANALYST_ORIGIN


def clause_rows(rows: Sequence[ReviewItemRow], clause_id: str) -> list[ReviewItemRow]:
    """Every current clause item of ``clause_id``, oldest first."""
    found = [
        row
        for row in current_items(rows)
        if row.kind is ItemKind.CLAUSE and row.subject.get("clause_id") == clause_id
    ]
    return sorted(found, key=lambda r: (r.created_at, r.item_id))


def live_clause(
    rows: Sequence[ReviewItemRow], clause_id: str
) -> Optional[ReviewItemRow]:
    """The current clause item of ``clause_id`` that is not removed (proposed or
    accepted, the agent's or the analyst's), or ``None``.
    """
    live = [
        r for r in clause_rows(rows, clause_id) if r.status is not ItemStatus.REMOVED
    ]
    return live[-1] if live else None


def shown_clause_rows(rows: Sequence[ReviewItemRow]) -> list[ReviewItemRow]:
    """The current clause items the Policy tab lists: a removed clause that was
    added again later is shown once, as its newer row (the removal stays in the
    pack's removals and on the chain).
    """
    clauses = [r for r in current_items(rows) if r.kind is ItemKind.CLAUSE]
    newest: dict[str, tuple[datetime, str]] = {}
    for row in clauses:
        key = str(row.subject.get("clause_id"))
        stamp = (row.created_at, row.item_id)
        if key not in newest or stamp > newest[key]:
            newest[key] = stamp
    return [
        row
        for row in clauses
        if row.status is not ItemStatus.REMOVED
        or newest[str(row.subject.get("clause_id"))] == (row.created_at, row.item_id)
    ]


def analyst_additions(rows: Sequence[ReviewItemRow], clause_id: str) -> int:
    """How many times the analyst has added ``clause_id`` to the case."""
    return sum(
        1
        for row in rows
        if is_analyst_clause(row) and row.subject.get("clause_id") == clause_id
    )


def analyst_clause_item_id(case_id: str, clause_id: str, nth: int) -> str:
    """The id of the ``nth`` (from 0) addition of ``clause_id`` by the analyst."""
    return deterministic_id("acl", case_id, clause_id, str(nth))


def added_reason(row: ReviewItemRow) -> Optional[str]:
    """The reason the analyst gave when adding ``row`` (``None`` for an agent's)."""
    reason = row.subject.get("added_reason")
    return str(reason) if is_analyst_clause(row) and reason else None


def clause_item(
    *,
    case_id: str,
    run_id: str,
    clause: ClauseDetailOut,
    reason: str,
    nth: int,
    by: str,
    by_name: str,
    now: datetime,
) -> ReviewItemRow:
    """The accepted ``clause`` item for ``clause`` as S3 holds it: its verbatim
    text, both citations and where it sits in the source; no gate.
    """
    subject: dict[str, Any] = {
        "clause_id": clause.clause_id,
        "domain": clause.risk_areas[0] if clause.risk_areas else None,
        "corpus_v": clause.corpus_version,
        "origin": ANALYST_ORIGIN,
        "title": clause.title,
        "source_id": clause.source_id,
        "ref": clause.ref,
        "page": clause.page,
        "line_start": clause.line_start,
        "line_end": clause.line_end,
        "added_by": by,
        "added_by_name": by_name,
        "added_reason": reason,
        "added_at": now.isoformat(),
    }
    return ReviewItemRow(
        item_id=analyst_clause_item_id(case_id, clause.clause_id, nth),
        case_id=case_id,
        kind=ItemKind.CLAUSE,
        status=ItemStatus.CONFIRMED,
        gate_id=None,
        subject=subject,
        proposed={"quote": clause.text, "applicability": ANALYST_APPLICABILITY},
        source_refs=[clause.source_ref.model_dump(mode="json")],
        run_id=run_id,
        decided=DecisionRow(value=None, reason=reason, by=by, at=now),
        created_at=now,
    )


def clause_add_command(
    *,
    case_id: str,
    run_id: str,
    item: ReviewItemRow,
    by: str,
    by_name: str,
) -> AnalystCommand:
    """The command that inserts ``item`` (an analyst's clause) once and
    announces ``case.clause_added``.
    """
    clause_id = str(item.subject["clause_id"])
    reason = str(item.subject["added_reason"])
    event = build_envelope(
        "case.clause_added",
        CaseClauseAdded(
            case_id=case_id,
            actor=by,
            item_id=item.item_id,
            clause_id=clause_id,
            corpus_v=int(item.subject["corpus_v"]),
            reason=reason,
            title=item.subject.get("title"),
            ref=item.subject.get("ref"),
        ).model_dump(mode="json"),
        case_id=case_id,
        run_id=run_id,
    )
    return AnalystCommand(
        case_id=case_id,
        actor=by,
        new_items=[item],
        events=[event],
        activity=[
            activity_row(
                by,
                "clause.added",
                f"{by_name} added clause {clause_id} from the corpus · reason recorded",
                {"item_id": item.item_id, "clause_id": clause_id},
            )
        ],
    )


def clause_remove_command(
    *,
    row: ReviewItemRow,
    reason: str,
    run_id: str,
    by: str,
    by_name: str,
    now: datetime,
) -> AnalystCommand:
    """The command that removes ``row`` (an analyst's clause) with ``reason``,
    once, and announces ``case.clause_removed``.
    """
    clause_id = str(row.subject.get("clause_id", row.item_id))
    event = build_envelope(
        "case.clause_removed",
        CaseClauseRemoved(
            case_id=row.case_id,
            actor=by,
            item_id=row.item_id,
            clause_id=clause_id,
            reason=reason,
        ).model_dump(mode="json"),
        case_id=row.case_id,
        run_id=run_id,
    )
    decided = DecisionRow(value=None, reason=reason, by=by, at=now)
    return AnalystCommand(
        case_id=row.case_id,
        actor=by,
        records=[
            OnceRecord(
                CLAUSE_REMOVALS,
                row.item_id,
                {
                    "item_id": row.item_id,
                    "case_id": row.case_id,
                    "clause_id": clause_id,
                    "reason": reason,
                    "removed_by": by,
                    "removed_at": now,
                },
            )
        ],
        items=[ItemDecision(row.item_id, ItemStatus.REMOVED, decided)],
        events=[event],
        activity=[
            activity_row(
                by,
                "clause.removed",
                f"{by_name} removed clause {clause_id} · reason recorded",
                {"item_id": row.item_id, "clause_id": clause_id},
            )
        ],
    )
