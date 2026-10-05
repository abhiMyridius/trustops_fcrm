"""Turning the conditions a pack drafted into the conditions an approval
activates: pure functions, no I/O, no model.

The narrative agent drafts each condition with an owner and a due date in words
(``Priya Nair``, ``before go-live``, ``6 months``); the pack freezes them as the
committee saw them. Activation resolves the words to what S1 can track: the
owner's user id when the owner is a person on the case, and a due *date* when
the words say one (a number of days, weeks or months after the decision, or the
case's go-live date). A label that names no date stays a label, and the
condition is simply not on the reminder clock until an analyst dates it; nothing
is guessed.
"""

from __future__ import annotations

import calendar
import re
from datetime import date, timedelta
from typing import Final, Literal, Optional

from fcrm_common.ids import deterministic_id
from fcrm_contracts.api.case_internal import CaseRecordInternal
from fcrm_contracts.api.case_internal_outcome import ActivatedCondition
from fcrm_contracts.api.decision import OutcomeCondition, PackCondition, PackView

_MONTHS: Final = {
    name.lower(): index for index, name in enumerate(calendar.month_name) if name
}
_MONTHS.update(
    {name.lower(): index for index, name in enumerate(calendar.month_abbr) if name}
)
_MONTH_YEAR: Final = re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{4})\b")
_QUARTER_YEAR: Final = re.compile(r"\bQ([1-4])\s*(\d{4})\b", re.IGNORECASE)
_ISO_DATE: Final = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_GO_LIVE: Final = re.compile(r"go[\s-]?live", re.IGNORECASE)
_AMOUNT: Final = re.compile(
    r"(\d{1,3})\s*[- ]?\s*(day|week|month|year)s?", re.IGNORECASE
)
_EVERY: Final = {"quarterly": 3, "monthly": 1, "annual": 12, "annually": 12}

#: The two statuses an outcome may activate a condition with.
ActivationStatus = Literal["open", "scheduled"]

#: The GO-LIVE field of the field set (F03): "when does this go live".
GO_LIVE_FIELD: Final = "F03"


def add_months(start: date, months: int) -> date:
    """``start`` plus ``months`` calendar months, the day clamped to the end of a
    shorter month (31 Jan + 1 month = 28 or 29 Feb).
    """
    index = start.month - 1 + months
    year, month = start.year + index // 12, index % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def go_live_date(text: Optional[str]) -> Optional[date]:
    """The go-live date a field value says, or ``None``.

    Reads an ISO date, ``February 2027`` (the first of the month) and ``Q1 2027``
    (the first day of the quarter); anything else is not a date.
    """
    if not text:
        return None
    iso = _ISO_DATE.search(text)
    if iso is not None:
        try:
            return date(int(iso[1]), int(iso[2]), int(iso[3]))
        except ValueError:
            return None
    quarter = _QUARTER_YEAR.search(text)
    if quarter is not None:
        return date(int(quarter[2]), (int(quarter[1]) - 1) * 3 + 1, 1)
    month_year = _MONTH_YEAR.search(text)
    if month_year is not None:
        month = _MONTHS.get(month_year[1].lower())
        if month is not None:
            return date(int(month_year[2]), month, 1)
    return None


def due_date_for(
    label: str, go_live: Optional[date], decided_on: date
) -> Optional[date]:
    """The date ``label`` names, or ``None`` when it names none.

    ``before go-live`` and ``go-live`` are the go-live date (``None`` when the
    case has none); ``N days / weeks / months / years`` and ``within N days``
    count from the day of the decision; ``quarterly``, ``monthly`` and
    ``annual`` are one period from it (the first reminder, not the last).
    """
    if _GO_LIVE.search(label):
        return go_live
    amount = _AMOUNT.search(label)
    if amount is not None:
        count, unit = int(amount[1]), amount[2].lower()
        if unit == "day":
            return decided_on + timedelta(days=count)
        if unit == "week":
            return decided_on + timedelta(weeks=count)
        if unit == "month":
            return add_months(decided_on, count)
        return add_months(decided_on, 12 * count)
    for word, months in _EVERY.items():
        if word in label.lower():
            return add_months(decided_on, months)
    return None


def owner_id_for(label: str, record: CaseRecordInternal) -> Optional[str]:
    """The user id behind ``label`` when it names the case's owner or analyst,
    else ``None`` (a team such as ``FCRM Ops`` has no user id).
    """
    wanted = label.strip().lower()
    for person in (record.owner, record.analyst):
        if person is not None and person.name.strip().lower() == wanted:
            return person.id
    return None


def _status_of(condition: PackCondition) -> ActivationStatus:
    return "scheduled" if condition.status == "scheduled" else "open"


def outcome_conditions(
    pack: PackView, record: CaseRecordInternal
) -> list[OutcomeCondition]:
    """The conditions ``pack`` drafted, as S5's outcome record carries them (the
    pack's own label as the id, the owner as a user id where there is one, the
    due date still in words).
    """
    return [
        OutcomeCondition(
            id=drafted.id,
            text=drafted.text,
            owner_id=owner_id_for(drafted.owner, record),
            due_label=drafted.due,
        )
        for drafted in pack.conditions
    ]


def activated_conditions(
    case_id: str, pack: PackView, record: CaseRecordInternal, decided_on: date
) -> list[ActivatedCondition]:
    """The conditions ``pack`` drafted, as S1 activates them once the decision
    of ``decided_on`` is recorded. S1's ``condition_id`` is derived from the
    case and the pack's label, so activating twice yields the same rows; the due
    dates count from ``decided_on``, which must be the same on every attempt
    (the decision's own date, from S5), or S1 would see a different body on a
    retry.
    """
    go_live = go_live_date(
        next(
            (f.value for f in record.fields if f.field_id == GO_LIVE_FIELD),
            None,
        )
    )
    return [
        ActivatedCondition(
            condition_id=deterministic_id("cnd", case_id, drafted.id),
            ref=drafted.id,
            text=drafted.text,
            owner_id=owner_id_for(drafted.owner, record),
            owner_label=drafted.owner,
            due_label=drafted.due,
            due_date=due_date_for(drafted.due, go_live, decided_on),
            status=_status_of(drafted),
            origin=drafted.from_,
        )
        for drafted in pack.conditions
    ]


def activation(
    case_id: str, pack: PackView, record: CaseRecordInternal, decided_on: date
) -> tuple[list[OutcomeCondition], list[ActivatedCondition]]:
    """Both views of the drafted conditions: S5's and S1's."""
    return (
        outcome_conditions(pack, record),
        activated_conditions(case_id, pack, record, decided_on),
    )
