"""The activity feed and the recorded outcome as staff read them. Pure, no I/O.

The feed is the same log the owner reads (``activity``), through the other fence:
an analyst sees every row (``po``, ``analyst`` and ``all`` visibility), with who
did it by name where S1 knows the name, the audit record a row cites and the gate
it opened or decided. The owner's version, in plain words with no actor and no
record number, is built by :func:`fcrm_case.po_views.activity_event`; the two
never share a model.

The outcome is what S5 tallied or the analyst closed under delegated authority
and H1 recorded in S1 (``PUT outcome``): who decided, how, why, and the
conditions it activated. A seeded closed case has no ``outcomes`` row (the seed
stores the case, not its outcome), so its outcome is read from the seed.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, Final, Optional

from fcrm_contracts.api.case_analyst_read import (
    ActivityAnalyst,
    OutcomeAnalyst,
    OutcomeConditionAnalyst,
)
from fcrm_contracts.api.case_internal_outcome import outcome_label
from fcrm_contracts.enums import CaseState, Outcome

from fcrm_case.domain.activity import ActivityEntry
from fcrm_case.domain.outcome import OutcomeRow
from fcrm_case.domain.record import CaseRecord
from fcrm_case.domain.views import ConditionRow

#: Roles whose ``actor.id`` is a user id (a person), not a service.
_PERSON_ROLES: Final = frozenset({"fcrm_po", "fcrm_analyst", "fcrm_committee"})

COMMITTEE_LABEL: Final = "Risk Committee"

#: Who "You" is in a staff reading of an owner's line when the case has no name.
OWNER_FALLBACK: Final = "The product owner"

#: The owner-addressed phrases system lines use ("13 fields are required for your
#: change"); only these are rewritten, so a document name is never touched.
_YOUR_PHRASE: Final = re.compile(
    r"\byour (case|change|submission|documents?|answers?)\b"
)


def _display(record: CaseRecord, user_id: str) -> str:
    """A user id as a name when it is the case's owner or analyst."""
    if user_id == record.owner:
        return record.owner_name or user_id
    if record.analyst_id is not None and user_id == record.analyst_id:
        return record.analyst_name or user_id
    return user_id


def actor_label(entry: ActivityEntry, record: CaseRecord) -> str:
    """Who did it: a person by name when known, else their id; a service by the
    component that wrote the row.
    """
    actor = entry.actor
    if actor.role in _PERSON_ROLES:
        return _display(record, actor.id)
    return actor.component or actor.id


def _record_seq(entry: ActivityEntry) -> Optional[int]:
    """The audit record a row cites: the event's own ``record_seq`` (what
    Caseflow sends beside an event), else one named in its detail.
    """
    if entry.record_seq is not None:
        return entry.record_seq
    value: Any = entry.detail.get("record_seq")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def staff_text(entry: ActivityEntry, record: CaseRecord) -> str:
    """A row's words as staff read them (D-119). Rows the owner can see are
    written to the owner ("You submitted the case (v1)", "Your case was assigned
    to Rahul Iyer"); staff read them in the third person ("Priya Nair submitted
    the case (v1)", "Case assigned to Rahul Iyer"). The stored row, and so the
    owner's own reading of it, is unchanged; a staff-only row is already written
    for staff.
    """
    text = entry.summary
    if entry.visibility == "analyst":
        return text
    if entry.type == "case.assigned":
        name = entry.detail.get("analyst_name")
        if isinstance(name, str) and name:
            return f"Case assigned to {name}"
    if text.startswith("You "):
        return f"{record.owner_name or OWNER_FALLBACK} {text.removeprefix('You ')}"
    if entry.actor.role in _PERSON_ROLES:
        return text
    if text.startswith("Your "):
        text = f"The {text.removeprefix('Your ')}"
    return _YOUR_PHRASE.sub(r"the \1", text)


def activity_analyst(entry: ActivityEntry, record: CaseRecord) -> ActivityAnalyst:
    """One log row as the analyst reads it, in the third person
    (:func:`staff_text`).
    """
    gate = entry.detail.get("gate_id")
    return ActivityAnalyst(
        id=f"ev_{entry.seq}",
        at=entry.at,
        actor=actor_label(entry, record),
        type=entry.type,
        text=staff_text(entry, record),
        record_seq=_record_seq(entry),
        gate_id=gate if isinstance(gate, str) else None,
    )


def _condition(row: ConditionRow) -> OutcomeConditionAnalyst:
    return OutcomeConditionAnalyst(
        id=row.condition_id,
        ref=row.ref,
        text=row.text,
        owner=row.owner_label,
        due=row.due_label,
        due_date=row.due_date,
        status=row.status,
    )


def outcome_analyst(
    record: CaseRecord,
    row: Optional[OutcomeRow],
    conditions: Sequence[ConditionRow],
    seeded: Mapping[str, str],
) -> OutcomeAnalyst:
    """The recorded outcome of a case for staff.

    A case whose outcome S1 recorded reads from that row. A closed case with no
    row (a seeded one) reads its outcome from ``seeded`` and has no mode or
    decider. A case still open shows only whether a delegated close was requested.
    """
    items = [_condition(c) for c in conditions]
    requested = row is not None and row.request is not None
    if row is not None and row.status == "set" and row.outcome is not None:
        delegated = row.mode == "delegated"
        return OutcomeAnalyst(
            decided=True,
            outcome=row.outcome.value,
            outcome_label=outcome_label(row.outcome),
            mode=row.mode,
            decided_by=_display(record, row.decided_by)
            if delegated and row.decided_by
            else COMMITTEE_LABEL,
            decided_at=row.decided_at,
            pack_id=row.pack_id,
            reason=row.reason,
            plain_summary=row.plain_summary,
            close_requested=requested,
            conditions=items,
        )
    if record.state is CaseState.CLOSED:
        label = seeded.get(record.case_id)
        return OutcomeAnalyst(
            decided=True,
            outcome=label,
            outcome_label=outcome_label(Outcome(label)) if label else None,
            mode=None,
            decided_by=None,
            decided_at=record.decided_at,
            pack_id=None,
            reason=None,
            plain_summary=record.outcome_summary,
            close_requested=requested,
            conditions=items,
        )
    return OutcomeAnalyst(
        decided=False,
        outcome=None,
        outcome_label=None,
        mode=None,
        decided_by=None,
        decided_at=None,
        pack_id=None,
        reason=None,
        plain_summary=None,
        close_requested=requested,
        conditions=items,
    )
