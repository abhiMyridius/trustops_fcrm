"""One atomic write of the product owner's, as plain data.

The service decides *what* a PO action does (which values to set, which review
items to decide, which events announce it) and hands the store a
:class:`PoCommand`; the store applies all of it in ONE transaction (the case
document, its field values, its review items, its documents, the frozen
submission, the activity rows and the outbox events) or none of it. The Mongo
store and the in-memory twin both read the same command and both compute the
case document afterwards with :func:`record_after`, so what the unit tests
show is what the database does.

Pure, no I/O.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from fcrm_contracts.api.case_internal import UiEventIn
from fcrm_contracts.enums import ChangeType, ItemStatus
from fcrm_contracts.events.envelope import Envelope

from fcrm_case.domain.fieldset import FieldSetData
from fcrm_case.domain.po_rows import SubmissionVersionRow
from fcrm_case.domain.po_rules import apply_type_delta
from fcrm_case.domain.record import CaseRecord
from fcrm_case.domain.views import (
    DecisionRow,
    DocumentRow,
    FieldOrigin,
    FieldSource,
)


@dataclass(frozen=True)
class TypeDelta:
    """The owner confirmed (``confirmed=True``) or removed a change type."""

    change_type: ChangeType
    confirmed: bool


@dataclass(frozen=True)
class FieldWrite:
    """A value a person set: the replaced value moves to ``history`` first."""

    field_id: str
    label: str
    value: Optional[str]
    origin: FieldOrigin
    source: Optional[FieldSource]


@dataclass(frozen=True)
class ItemDecision:
    """A decision on one review item (a change type, a question)."""

    item_id: str
    status: ItemStatus
    decided: DecisionRow


@dataclass(frozen=True)
class GateEmission:
    """A ``gate.decided`` event, sent only the first time this gate's decision
    is made. ``gate_type`` and ``key`` identify the gate (``po_confirm_category``
    and the change type, ``po_confirm_value`` and the field id...); the store
    records them in the decision ledger and enqueues ``event`` when the row is
    new.
    """

    gate_type: str
    key: str
    event: Envelope


@dataclass(frozen=True)
class ActivityDraft:
    """An activity row the owner sees. With ``only_with_gate`` set it is staged
    only when that gate's ``gate.decided`` was emitted by this very write
    (:func:`fcrm_case.po_store.gate_key`), so confirming a value twice leaves one
    row, not two.
    """

    event: UiEventIn
    only_with_gate: Optional[str] = None


@dataclass(frozen=True)
class PoCommand:
    """Everything one PO action changes, applied together.

    Attributes:
        case_id: The case.
        actor: The owner's user id (who the decision ledger names).
        type_delta: A change type confirmed or removed.
        fields: Values to set.
        items: Review items to decide.
        gates: ``gate.decided`` events, each emitted at most once per gate.
        documents: Uploaded files to record.
        submission: The frozen submission to insert; submits the case.
        discard_reason: Discard the draft with this reason.
        events: Events enqueued unconditionally (``case.document_stored``,
            ``case.submitted``).
        activity: Activity rows the PO sees, staged in the same transaction.
    """

    case_id: str
    actor: str
    type_delta: Optional[TypeDelta] = None
    fields: Sequence[FieldWrite] = ()
    items: Sequence[ItemDecision] = ()
    gates: Sequence[GateEmission] = ()
    documents: Sequence[DocumentRow] = ()
    submission: Optional[SubmissionVersionRow] = None
    discard_reason: Optional[str] = None
    events: Sequence[Envelope] = ()
    activity: Sequence[ActivityDraft] = ()


def record_after(
    record: CaseRecord,
    command: PoCommand,
    now: datetime,
    *,
    field_sets: Callable[[int], FieldSetData],
    type_domains: Mapping[str, Sequence[str]],
) -> CaseRecord:
    """The case document once ``command``'s case-level changes are applied.

    That is the confirmed types with the fields and domains that follow, the
    submission stamp and the discard stamp. (Field values, items and documents
    live in their own collections and are not part of the case document.)
    ``field_sets`` gives the field set of a pinned version: a type is resolved
    against the version the case pinned, not whatever is current.
    """
    updated = record
    if command.type_delta is not None:
        updated = apply_type_delta(
            updated,
            command.type_delta.change_type,
            confirmed=command.type_delta.confirmed,
            field_set=field_sets(record.fieldset_v),
            type_domains=type_domains,
        )
    if command.submission is not None:
        updated = updated.model_copy(
            update={
                "submitted_at": command.submission.captured_at,
                "current_submission_v": command.submission.version,
            }
        )
    if command.discard_reason is not None:
        updated = updated.model_copy(
            update={"discarded_at": now, "discard_reason": command.discard_reason}
        )
    return updated


def staged_activity(command: PoCommand, emitted: frozenset[str]) -> list[UiEventIn]:
    """The activity rows to stage: every draft, except those tied to a gate
    this write did not emit (a decision made before).
    """
    return [
        draft.event
        for draft in command.activity
        if draft.only_with_gate is None or draft.only_with_gate in emitted
    ]
