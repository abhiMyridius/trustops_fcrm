"""The clarification round: the questions the analyst asks the owner after the
completeness report (D-16), and the owner's answers.

``clarification_threads`` holds one row per question (design A section 2.1.1):
``awaiting_response`` until the owner answers, ``answered`` after, ``resolved``
once the analyst closes it. The status strings ``awaiting_response`` is what the
owner's list counts (``fcrm_case.po_store``), so it is a name this module keeps.

A row is a storage shape, shared by the analyst's side (which opens and resolves
threads) and the owner's (which answers them). It carries the ``anchor`` the
analyst chose (a field, or a control: analyst-only), so a view built for the owner
must read ``field_label`` and never ``anchor``; :mod:`fcrm_case.po_followup_views`
does.

Pure, no I/O.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Final, Literal, Optional

from fcrm_common.errors import ValidationError
from fcrm_common.ids import deterministic_id
from pydantic import BaseModel, ConfigDict, Field

ThreadStatus = Literal["awaiting_response", "answered", "resolved"]

#: The label the owner reads for a question anchored to nothing they can name.
GENERAL_LABEL: Final = "Your submission"

_FIELD_ID: Final = re.compile(r"^F\d{2,3}$")


class ThreadAnswerRow(BaseModel):
    """What the owner answered one question: the words, the documents they named
    (as they named them), the ids those documents were stored under and the
    fields they set.
    """

    model_config = ConfigDict(extra="forbid")

    text: str
    documents: list[str] = Field(default_factory=list)
    document_ids: list[str] = Field(default_factory=list)
    field_ids: list[str] = Field(default_factory=list)
    at: datetime
    by: str


class ClarificationThreadRow(BaseModel):
    """``clarification_threads`` (``_id = thread_id``): one question of a round."""

    model_config = ConfigDict(extra="forbid")

    thread_id: str
    case_id: str
    round: int
    question_id: str
    text: str
    anchor: Optional[str] = None
    field_ids: list[str] = Field(default_factory=list)
    field_label: str
    status: ThreadStatus = "awaiting_response"
    opened_at: datetime
    opened_by: str
    answer: Optional[ThreadAnswerRow] = None
    resulting_version: Optional[int] = None
    resolved_at: Optional[datetime] = None
    resolved_by: Optional[str] = None
    resolve_note: Optional[str] = None


def thread_id_for(case_id: str, round_no: int, question_id: str) -> str:
    """The stable ``thr_`` id of a question, so the same round sent twice
    addresses the same rows.

    Raises:
        fcrm_common.errors.ValidationError: ``question_id`` cannot be part of an id.
    """
    try:
        return deterministic_id("thr", case_id, str(round_no), question_id)
    except ValueError as exc:
        raise ValidationError(
            f"{question_id!r} cannot name a question: {exc}",
            errors=[{"loc": "body.questions", "msg": "unusable question id"}],
        ) from exc


def is_field_id(anchor: Optional[str]) -> bool:
    """Whether ``anchor`` names a field (``F11``) rather than a control."""
    return anchor is not None and _FIELD_ID.match(anchor) is not None


def anchor_parts(anchor: Optional[str]) -> list[str]:
    """The ids a question's anchor names. The narrative agent anchors a question
    to one field or control; the seed's own questions name both
    (``CTL-T01 / F11``), so an anchor is read as ids separated by ``/``.
    """
    if anchor is None:
        return []
    return [part.strip() for part in anchor.split("/") if part.strip()]


def anchored_field_ids(anchor: Optional[str]) -> list[str]:
    """The fields a question's anchor names (controls are not fields)."""
    return [part for part in anchor_parts(anchor) if is_field_id(part)]


def field_label_for(anchor: Optional[str], labels: Mapping[str, str]) -> str:
    """What the owner reads after "Re:" for a question anchored to ``anchor``: the
    label of the first field it names, else a general label (an analyst's control
    id never reaches the owner).
    """
    for field_id in anchored_field_ids(anchor):
        if field_id in labels:
            return labels[field_id]
    return GENERAL_LABEL


def open_threads(
    threads: Sequence[ClarificationThreadRow],
) -> list[ClarificationThreadRow]:
    """The threads still waiting for the owner."""
    return [t for t in threads if t.status == "awaiting_response"]


def changed_fields_of(threads: Sequence[ClarificationThreadRow]) -> list[str]:
    """The fields a round's answers changed: the fields the owner set, and the
    fields the questions were about (an answer about a field is new information
    about it), once each in first-seen order.
    """
    seen: dict[str, None] = {}
    for thread in threads:
        for field_id in [
            *(thread.answer.field_ids if thread.answer else []),
            *thread.field_ids,
        ]:
            seen.setdefault(field_id)
    return list(seen)


def added_documents_of(threads: Sequence[ClarificationThreadRow]) -> list[str]:
    """The document ids a round's answers stored, once each."""
    seen: dict[str, None] = {}
    for thread in threads:
        for doc_id in thread.answer.document_ids if thread.answer else []:
            seen.setdefault(doc_id)
    return list(seen)
